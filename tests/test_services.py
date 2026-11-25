"""0.4.0: the one "run a service" interface (liminal-libs 1.93.0's ContainerHost) on Incus' own OCI instances
(`incus-oci`) and on Docker inside liminal-oci (`lxd-docker`, decision 15 a). The plugin is attached to a real apps.App
beside the library's stand-in firewall (firewall-linux's stack) and stand-in service plugins, so the asker's gates are
the real ones. This machine's look and read go to test_plugin's FakeLinux; every service command to FakeIncus below,
and Docker inside liminal-oci to the library's FakeDocker: nothing runs, nothing is fetched, no port is opened."""
from __future__ import annotations

import copy
import json

import pytest

from liminal_libs.apps import App, asking
from liminal_libs.apps.asking import AskRefused
from liminal_libs.instances import Refusal
from liminal_libs.machines import standins
from liminal_libs.machines.linux import Shell
from liminal_libs.machines.standins import FakeDocker, ServiceOwner, StandInNetworkHost
from plugin_platform_lxd import LXC_WORDS, LxdPlugin
from plugin_platform_lxd import Refusal as LxdRefusal
from plugin_platform_lxd import services as svcs
from test_plugin import SUDO, FakeLinux

DIG = "sha256:" + "a" * 64
SECRET = "canary-Qz71-never-shown"
INCUS = "/usr/bin/incus"
LXC = "/snap/bin/lxc"


class Frame(App):
    NAME = "frame"

    def __init__(self, login):
        super().__init__(log=lambda m: None, login=login)
        self.off: set[str] = set()

    def disabled(self):
        return set(self.off)

    def known_plugins(self):
        return list(self._plugins.values())


def spec(owner="jenkins", name="ci", host=8080, lan=True, secrets=(), seeds=()):
    return {"owner": owner, "name": name, "title": owner.capitalize(), "system": "linux", "parts": [{
        "name": "controller",
        "image": {"ref": "docker.io/jenkins/jenkins:2.528.2-lts-jdk21", "digest": DIG, "system": "linux",
                  "platforms": ["linux/amd64"], "size": 300_000_000, "licence": "MIT"},
        "ports": [{"name": "web", "container": 8080, "host": host, "lan": lan}],
        "volumes": [{"name": "home", "path": "/var/jenkins_home", "holds": "its jobs and history", "owner": 1000}],
        "env": {"JAVA_OPTS": "-Dhudson.model.UsageStatistics.disabled=true"}, "secrets": list(secrets),
        "seeds": list(seeds),
        "readable": [{"name": "unlock", "volume": "home", "path": "secrets/initialAdminPassword"}],
        "memory": 2 << 30, "health": {"port": "web", "path": "/login", "ready_s": 30}}]}


def _kv(words) -> dict:
    return dict(w.partition("=")[::2] for w in words if "=" in w)


class FakeIncus:
    """Incus' (or LXD's) client answered from memory: instances, custom volumes and their snapshots and files,
    networks, images, remotes; every command recorded with its standard input. `docker` answers Docker inside
    liminal-oci (the library's FakeDocker), each `sh -c ENV_SH` read as that shell reads it."""

    def __init__(self, client=INCUS, oci=True, tools=("skopeo", "umoci"), version="6.18", pool="default",
                 liminal_oci=None, docker=None, initial=True):
        self.client, self.version, self.pool = client, version, pool
        self.extensions = ["instance_oci"] if oci else []
        self.initial = initial  # the API extension storage_volumes_initial_values (initial.uid on a custom volume)
        self.oci_address = "10.120.17.50"  # liminal-oci's own on incusbr0, while it runs ("" for none yet)
        self.tools = tuple(tools)
        self.instances: dict[str, dict] = {}
        self.volumes: dict[str, dict] = {}
        self.networks: dict[str, dict] = {"incusbr0": {"name": "incusbr0", "managed": True, "type": "bridge",
                                                       "config": {"ipv4.address": "10.120.17.1/24"}}}
        self.images: dict[str, str] = {}
        self.remotes: dict[str, dict] = {"images": {"Addr": "https://images.linuxcontainers.org", "Protocol": "simplestreams"},
                                         "local": {"Addr": "unix://", "Protocol": "incus"}}
        self.docker = docker
        self.calls: list[dict] = []
        self.streamed: list[list[str]] = []
        self.console: dict[str, list[str]] = {}
        if liminal_oci:
            self.instances[svcs.OCI] = {"name": svcs.OCI, "status": liminal_oci, "type": "container",
                                        "config": {"boot.autostart": "false"}, "devices": {}}

    # -- the runner and the streamer ------------------------------------------------------------------------------------
    def _args(self, argv) -> list[str]:
        argv = [str(a) for a in argv]
        words = argv[3:] if argv[:3] == SUDO else argv
        assert words[0] == self.client, words
        return words[1:]

    def __call__(self, argv, timeout, stdin=None, env=None, binary=False, out=None):
        argv = [str(a) for a in argv]
        args = self._args(argv)
        self.calls.append({"argv": argv, "stdin": stdin, "args": args, "env": dict(env or {})})
        if env:
            return 2, "", "the stand-in: a service command was handed an environment"
        if args[:2] == ["exec", svcs.OCI]:
            return self._exec(argv, args, timeout, stdin, binary, out)
        code, text, err = self._answer(args, stdin)
        if binary and isinstance(text, str):
            text = text.encode("utf-8")
        return code, text, err

    def stream(self, argv, timeout, line, stop=None):
        args = self._args(argv)
        self.streamed.append(args)
        self.calls.append({"argv": [str(a) for a in argv], "stdin": None, "args": args, "env": {}})
        if args[:2] == ["image", "copy"]:
            remote = args[2].split(":", 1)[0]
            if remote not in self.remotes:
                return 1, f"Error: The remote \"{remote}\" doesn't exist"
            line("Copying the image: 100% (12.3MB/s)")
            self.images[args[args.index("--alias") + 1]] = "f" * 64
            return 0, ""
        if args[0] == "launch":
            cfg = _kv(args[i + 1] for i, a in enumerate(args) if a == "-c")
            self.instances[args[2]] = {"name": args[2], "status": "Running", "type": "container", "config": cfg,
                                       "devices": {}}
            line(f"Launching {args[2]}")
            return 0, ""
        if args[:2] == ["exec", svcs.OCI] and "sh" in args:
            self.docker = self.docker or FakeDocker(version="28.5.1")
            line("28.5.1")
            return 0, ""
        if args[:2] == ["exec", svcs.OCI] and "docker" in args:
            return self.docker.stream(argv, timeout, line, stop)
        return 1, f"the stand-in streams no {args[:2]}"

    def _exec(self, argv, args, timeout, stdin, binary, out):
        oci = self.instances.get(svcs.OCI)
        if oci is None or oci["status"] != "Running":
            return 1, b"" if binary else "", "Error: Instance is not running"
        inner = args[args.index("--") + 1:]
        if inner[:3] == ["sh", "-c", svcs.ENV_SH]:
            env = dict(line.partition("=")[::2] for line in (stdin or b"").decode("utf-8").splitlines())
            return self.docker(argv, timeout, stdin=None, env=env, binary=binary, out=out)
        if inner[:1] == ["docker"]:
            return self.docker(argv, timeout, stdin=stdin, env=None, binary=binary, out=out)
        if inner[:1] == ["chown"]:
            return 0, "", ""
        return 1, "", f"the stand-in runs no {inner[:2]} inside {svcs.OCI}"

    # -- the answers -------------------------------------------------------------------------------------------------
    def _users(self, volume: str) -> list[str]:
        return [n for n, i in self.instances.items() for d in i["devices"].values()
                if d.get("type") == "disk" and d.get("source") == volume]

    def _instance_json(self, i: dict) -> dict:
        out = {**copy.deepcopy(i), "expanded_devices": copy.deepcopy(i["devices"])}
        if i["name"] == svcs.OCI:  # the default profile's NIC on incusbr0, and while it runs the address Incus gave it
            out["expanded_devices"].setdefault("eth0", {"type": "nic", "network": "incusbr0", "name": "eth0"})
            if i["status"] == "Running" and self.oci_address:
                out["state"] = {"network": {"eth0": {"hwaddr": "00:16:3e:4c:00:01", "addresses": [
                    {"family": "inet6", "address": "fe80::216:3eff:fe4c:1", "netmask": "64", "scope": "link"},
                    {"family": "inet", "address": "169.254.3.3", "netmask": "16", "scope": "link"},
                    {"family": "inet", "address": self.oci_address, "netmask": "24", "scope": "global"}]}}}
        return out

    def _answer(self, a, stdin):
        if a[0] == "list":
            return 0, json.dumps([self._instance_json(i) for i in self.instances.values()]), ""
        if a[:2] == ["image", "list"]:
            return 0, json.dumps([{"fingerprint": f, "aliases": [{"name": al}]} for al, f in self.images.items()]), ""
        if a[:2] == ["remote", "list"]:
            return 0, json.dumps(self.remotes), ""
        if a[:2] == ["remote", "add"]:
            self.remotes[a[2]] = {"Addr": a[3], "Protocol": "oci" if "--protocol=oci" in a else "incus"}
            return 0, "", ""
        if a[:2] == ["storage", "volume"]:
            return self._volume(a[2:], stdin)
        if a[:2] == ["network", "list"]:
            return 0, json.dumps(list(self.networks.values())), ""
        if a[:2] == ["network", "create"]:
            if len(a[2]) > 15:
                return 1, "", f"Error: Network name too long: {a[2]}"
            self.networks[a[2]] = {"name": a[2], "managed": True, "type": "bridge", "config": _kv(a[3:])}
            return 0, "", ""
        if a[:2] == ["network", "delete"]:
            if a[2] not in self.networks:
                return 1, "", "Error: Network not found"
            if any(d.get("network") == a[2] for i in self.instances.values() for d in i["devices"].values()):
                return 1, "", "Error: The network is currently in use"
            del self.networks[a[2]]
            return 0, "", ""
        if a[0] == "init":
            return self._init(a[1], a[2], stdin)
        if a[0] in ("start", "stop", "delete", "console") and a[1] not in self.instances:
            return 1, "", "Error: Instance not found"
        if a[0] == "start":
            self.instances[a[1]]["status"] = "Running"
            return 0, "", ""
        if a[0] == "stop":
            self.instances[a[1]]["status"] = "Stopped"
            return 0, "", ""
        if a[0] == "rename":
            if a[2] in self.instances:
                return 1, "", "Error: Name already in use"
            i = self.instances.pop(a[1])
            i["name"] = a[2]
            self.instances[a[2]] = i
            return 0, "", ""
        if a[0] == "delete":
            if self.instances[a[1]]["status"] == "Running":
                return 1, "", "Error: The instance is currently running, stop it first or pass --force"
            del self.instances[a[1]]
            return 0, "", ""
        if a[:2] == ["config", "device"]:
            return self._device(a[2:])
        if a[0] == "console":
            return 0, "\n".join(self.console.get(a[1], [])), ""
        return 1, "", f"the stand-in incus knows no {a[:3]}"

    def _init(self, alias, name, stdin):
        if alias not in self.images:
            return 1, "", f"Error: Image \"{alias}\" not found"
        if name in self.instances:
            return 1, "", "Error: Instance name already in use"
        body = json.loads(stdin.decode("utf-8"))
        devices = body.get("devices") or {}
        for d in devices.values():
            if d.get("type") == "nic":
                if d.get("network") not in self.networks:
                    return 1, "", "Error: Failed loading network"
                for other in self.instances.values():
                    for od in other["devices"].values():
                        if od.get("type") == "nic" and od.get("network") == d.get("network") and \
                                d.get("ipv4.address") and od.get("ipv4.address") == d.get("ipv4.address"):
                            return 1, "", "Error: IP address already defined on another NIC"
            if d.get("type") == "disk" and d.get("source") not in self.volumes:
                return 1, "", f"Error: Storage volume {d.get('source')} not found"
        self.instances[name] = {"name": name, "status": "Stopped", "type": "container",
                                "config": dict(body.get("config") or {}), "devices": devices}
        return 0, "", ""

    def _device(self, a):
        verb, inst, dev = a[0], a[1], a[2]
        devices = self.instances[inst]["devices"]
        if verb == "add":
            if dev in devices:
                return 1, "", "Error: The device already exists"
            devices[dev] = {"type": a[3], **_kv(a[4:])}
        elif verb == "remove":
            devices.pop(dev)
        elif verb == "set":
            devices[dev].update(_kv(a[3:]))
        elif verb == "unset":
            devices[dev].pop(a[3], None)
        return 0, "", ""

    def _volume(self, a, stdin):
        if a[0] == "list":
            assert a[1] == self.pool
            return 0, json.dumps([{"name": n, "type": "custom", "config": dict(v["config"]),
                                   "used_by": [f"/1.0/instances/{u}" for u in self._users(n)]}
                                  for n, v in self.volumes.items()]), ""
        if a[0] == "create":
            if a[2] in self.volumes:
                return 1, "", "Error: Volume by that name already exists"
            self.volumes[a[2]] = {"config": _kv(a[3:]), "files": {}, "owners": {}, "snapshots": {}}
            return 0, "", ""
        if a[0] == "delete":
            if self._users(a[2]):
                return 1, "", "Error: Storage volume is currently in use"
            del self.volumes[a[2]]
            return 0, "", ""
        if a[:2] == ["file", "push"]:
            vol, _, rel = a[-1].partition("/")
            files = self.volumes[vol]["files"]
            if rel in files:
                return 1, "", "the stand-in: that file is there already"
            files[rel] = stdin
            uid = a[a.index("--uid") + 1] if "--uid" in a else None
            self.volumes[vol]["owners"][rel] = (uid, a[a.index("--mode") + 1])
            return 0, "", ""
        if a[:2] == ["file", "pull"]:
            vol, _, rel = a[3].partition("/")
            data = self.volumes.get(vol, {}).get("files", {}).get(rel)
            return (0, data, "") if data is not None else (1, b"", "Error: not found")
        if a[0] == "snapshot":
            verb, vol = a[1], a[3]
            snaps = self.volumes[vol]["snapshots"]
            if verb == "create":
                snaps[a[4]] = dict(self.volumes[vol]["files"])
            elif verb == "list":
                return 0, json.dumps([{"name": f"{vol}/{s}"} for s in snaps]), ""
            elif verb == "restore":
                self.volumes[vol]["files"] = dict(snaps[a[4]])
            elif verb == "delete":
                snaps.pop(a[4])
            return 0, "", ""
        return 1, "", f"the stand-in knows no storage volume {a[:2]}"

    # -- the services' read, as the plugin's script asks it ----------------------------------------------------------------
    def services_answer(self) -> str:
        server = {"api_extensions": [*(["storage_volumes_initial_values"] if self.initial else []), *self.extensions],
                  "environment": {"server_version": self.version, "kernel_architecture": "x86_64"}}
        profile = {"name": "default", "devices": {"root": {"type": "disk", "path": "/", "pool": self.pool},
                                                  "eth0": {"type": "nic", "network": "incusbr0", "name": "eth0"}}}
        lines = [svcs.MARK, "== server", json.dumps(server, indent=2), "== rc 0", "== profile",
                 json.dumps(profile, indent=2), "== rc 0", "== tools", *self.tools]
        oci = self.instances.get(svcs.OCI)
        if oci is None:
            lines += ["== oci", "Error: Instance not found", "== rc 1"]
        else:
            lines += ["== oci", json.dumps(self._instance_json(oci), indent=2), "== rc 0"]
            if oci["status"] == "Running":
                code, out, err = self.docker(["docker", "version", "--format", "{{json .}}"], 30) if self.docker \
                    else (1, "", "sh: docker: not found")
                lines += ["== docker", out or err, f"== rc {code}"]
        return "\n".join(lines + ["== end"]) + "\n"


class Machine(FakeLinux):
    """test_plugin's FakeLinux, which also answers the services' read from its FakeIncus (`services` counts them)."""

    def __init__(self, *a, incus: FakeIncus | None = None, **kw):
        super().__init__(*a, **kw)
        self.incus = incus
        self.services = 0

    def __call__(self, args, timeout):
        cmd = args[3:] if args[:3] == SUDO else args
        if cmd[:2] == ["sh", "-c"] and svcs.MARK in cmd[2]:
            self.calls.append(list(args))
            self.services += 1
            assert "launch" not in cmd[2] and " start " not in cmd[2]  # the read starts nothing
            return 0, self.incus.services_answer(), ""
        return super().__call__(args, timeout)


class World:
    def __init__(self, tmp_path, backend="incus", oci=True, liminal_oci=None, groups=None, linux=None, incus=None,
                 native=""):
        if backend == "incus":
            self.incus = incus or FakeIncus(INCUS, oci=oci, liminal_oci=liminal_oci,
                                            docker=FakeDocker(version="28.5.1") if liminal_oci else None)
            self.linux = linux or Machine(groups=groups or ("ciara", "wheel", "incus-admin"), incus=self.incus)
        else:
            self.incus = incus or FakeIncus(LXC, oci=False, tools=(), liminal_oci=liminal_oci,
                                            docker=FakeDocker(version="28.5.1") if liminal_oci else None)
            self.linux = linux or Machine("lxd", os="ubuntu", units="lxd", groups=groups or ("ciara", "lxd"),
                                          daemons=("lxd",), sockets=("/var/snap/lxd/common/lxd/unix.socket",),
                                          incus=self.incus)
        self.tid = backend
        self.login = standins.login({"JENKINS_ADMIN_PASSWORD": SECRET})
        out = standins.Outside(sleep=lambda s: None, health=lambda h, p, path: True, platform="linux::TEST",
                               holders=lambda port: [], folder=tmp_path)
        self.lxd = LxdPlugin(shell=Shell("local", runner=self.linux), native=native, service_runner=self.incus,
                             streamer=self.incus.stream, outside=out)
        self.app = Frame(self.login)
        self.firewall = StandInNetworkHost(own="linux", routes_to=())
        self.jenkins = ServiceOwner("jenkins", host="lxd")
        for p in (self.lxd, self.firewall, self.jenkins):
            self.app.attach(p)

    def page(self, plugin, hook, *args):
        with asking.asking(asking.PAGE):
            return self.app.ask(plugin, hook, *args)

    def grant(self, plugin="jenkins"):
        self.page("lxd", "grant_service", plugin, self.tid)
        if "lxd" not in self.firewall.network_grants():
            self.page("standin-firewall", "grant_network", "lxd")

    def wait(self, work):
        w = self.lxd._svc.works.get(work)
        w.thread.join(20)
        assert not w.thread.is_alive()
        return self.jenkins.call("service_work", work, 0)

    def carry(self, action, spec_=None, options=None, target=""):
        plan = self.jenkins.call("service_plan", action, "jenkins/ci", spec_, target, options or {})
        for a in plan["approve"]:
            words = self.page(a["plugin"], "plan_words", a["plan"])
            needed = {x["name"] for x in words["accepts"]}
            order = standins._ACCEPT_ORDER[a["plugin"] == "lxd"]
            self.page(a["plugin"], "approve_plan", a["plan"], *[k in needed for k in order])
        return plan, self.wait(self.jenkins.call("apply_plan", plan["plan"])["work"])

    def service_calls(self) -> list[dict]:
        return list(self.incus.calls)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# -- incus-oci ---------------------------------------------------------------------------------------------------------
def test_incus_with_oci_support_is_its_own_target(world):
    got = world.jenkins.call("hosting", "jenkins", {"linux": spec()})
    t = got["targets"][0]
    assert (got["runtime"], t["id"], t["state"], t["mechanism"]) == ("lxd", "incus", "ready", "incus-oci")
    assert (t["stack"], t["path"], t["firewall"], t["version"], t["arch"]) == ("linux", "direct", "linux", "6.18", "amd64")
    assert "storage pool default" in t["where_data"] and t["title"].endswith("its own OCI instances")
    fit = t["fit"]
    assert fit["takes"] == "" and fit["parts"]["lifecycle"]["words"].startswith("boot.autostart=false")
    assert {k for k, v in fit["parts"].items() if v["fit"] == "differs"} == {"image", "network", "logs"}
    assert world.linux.services == 1  # read once
    assert [c["args"] for c in world.incus.calls] == [["list", "--format", "json"]]  # its services listed: a read alone


def test_an_install_on_incus_hands_everything_on_standard_input(world):
    world.grant()
    s = spec(secrets=[{"variable": "JENKINS_ADMIN_PASSWORD", "env": "ADMIN_PASSWORD"}],
             seeds=[{"volume": "home", "path": "init.groovy.d/off.groovy", "text": "// usage statistics off\n"}])
    plan, end = world.carry("install", s, target="incus")
    assert end["ok"], end
    assert "accept_differs" in [a["name"] for a in plan["accepts"]] and "accept_kept_secret" in [a["name"] for a in plan["accepts"]]
    assert all(SECRET not in " ".join(c["argv"]) for c in world.incus.calls)  # never on a command line
    init = next(c for c in world.incus.calls if c["args"][:1] == ["init"])
    assert init["args"] == ["init", svcs.image_alias(svcs.ImagePin.of(s["parts"][0]["image"])), "liminal-jenkins-ci-controller"]
    body = json.loads(init["stdin"])
    cfg, devices = body["config"], body["devices"]
    assert cfg["boot.autostart"] == "false" and cfg["limits.memory"] == str(2 << 30)  # said outright
    assert cfg["environment.ADMIN_PASSWORD"] == SECRET and cfg["environment.JAVA_OPTS"].startswith("-Dhudson")
    assert cfg["user.liminal"] == "service" and cfg["user.liminal.service"] == "jenkins/ci" and cfg["user.liminal.digest"] == DIG
    assert '"text":""' in cfg["user.liminal.spec"] and "usage statistics" not in cfg["user.liminal.spec"]  # seeds' texts left out
    bridge = svcs.bridge_name("liminal-jenkins-ci")
    assert len(bridge) <= 15 and devices["eth0"]["network"] == bridge and devices["eth0"]["ipv4.address"].startswith("10.77.1")
    assert devices["liminal-jenkins-ci-home"] == {"type": "disk", "pool": "default", "source": "liminal-jenkins-ci-home",
                                                  "path": "/var/jenkins_home"}
    assert {d["listen"] for d in devices.values() if d["type"] == "proxy"} == {"tcp:0.0.0.0:8080", "tcp:[::]:8080"}
    net = world.incus.networks[bridge]["config"]
    assert net["user.liminal"] == "segment" and net["user.liminal.network"] == "liminal-jenkins-ci" and net["ipv4.nat"] == "true"
    assert net["ipv4.address"].endswith(".1/24") and net["ipv6.address"] == "none" and net["ipv4.dhcp.ranges"].endswith(".254")
    vol = world.incus.volumes["liminal-jenkins-ci-home"]
    assert vol["config"]["initial.uid"] == "1000" and vol["config"]["user.liminal"] == "volume"
    assert vol["files"]["init.groovy.d/off.groovy"] == b"// usage statistics off\n" and vol["owners"]["init.groovy.d/off.groovy"] == ("1000", "0640")
    assert world.incus.remotes["liminal-docker"] == {"Addr": "https://docker.io", "Protocol": "oci"}
    assert world.incus.streamed[0][:3] == ["image", "copy", f"liminal-docker:jenkins/jenkins@{DIG}"]
    assert world.incus.instances["liminal-jenkins-ci-controller"]["status"] == "Running"
    said = json.dumps([plan, end, world.page("lxd", "status")])
    assert SECRET not in said
    assert world.jenkins.call("hosted", "jenkins/ci")["state"] == "running"


def test_the_conformance_sequence_runs_on_incus_oci(world):
    world.grant()
    done = standins.conformance(world.jenkins, world.page, spec(), "incus", world.wait)
    assert done[-1] == "conformance"
    assert [n for n in world.incus.instances] == [] and world.incus.volumes == {}
    assert list(world.incus.networks) == ["incusbr0"]  # its bridges gone with it; the default one untouched


def test_an_update_keeps_a_snapshot_and_the_previous_instance_lets_its_address_go(world):
    world.grant()
    world.carry("install", spec(), target="incus")
    first = world.incus.instances["liminal-jenkins-ci-controller"]["devices"]["eth0"]["ipv4.address"]
    world.incus.volumes["liminal-jenkins-ci-home"]["files"]["config.xml"] = b"<old/>"
    newer = spec()
    newer["parts"][0]["image"]["digest"] = "sha256:" + "b" * 64
    _, end = world.carry("update", newer)
    assert end["ok"], end
    prev = world.incus.instances["liminal-jenkins-ci-controller-previous"]
    assert "ipv4.address" not in prev["devices"]["eth0"] and prev["status"] == "Stopped"
    assert world.incus.instances["liminal-jenkins-ci-controller"]["devices"]["eth0"]["ipv4.address"] == first
    snaps = world.incus.volumes["liminal-jenkins-ci-home"]["snapshots"]
    assert len(snaps) == 1 and next(iter(snaps)).startswith("liminal-")
    world.incus.volumes["liminal-jenkins-ci-home"]["files"]["config.xml"] = b"<converted/>"
    _, end = world.carry("rollback", options={"choice": "backup"})
    assert end["ok"], end
    assert world.incus.volumes["liminal-jenkins-ci-home"]["files"]["config.xml"] == b"<old/>"  # the snapshot put back
    back = world.incus.instances["liminal-jenkins-ci-controller"]
    assert back["config"]["user.liminal.digest"] == DIG and back["devices"]["eth0"]["ipv4.address"] == first


def test_a_volume_of_another_service_is_never_made_over(world):
    world.grant()
    world.incus.volumes["liminal-jenkins-ci-home"] = {"config": {"user.liminal": "volume", "user.liminal.service": "gocd/ci"},
                                                      "files": {}, "owners": {}, "snapshots": {}}
    _, end = world.carry("install", spec(), target="incus")
    assert end["ok"] is False and "is not jenkins/ci's" in json.dumps(end)
    assert not any(c["args"][:3] == ["storage", "volume", "create"] for c in world.incus.calls)
    assert world.incus.instances == {}


def test_a_closed_port_listens_on_this_machine_alone(world):
    world.grant()
    world.carry("install", spec(lan=False), target="incus")
    devices = world.incus.instances["liminal-jenkins-ci-controller"]["devices"]
    assert [d["listen"] for d in devices.values() if d["type"] == "proxy"] == ["tcp:127.0.0.1:8080"]


def test_through_sudo_every_service_command_is_roots(tmp_path):
    w = World(tmp_path, groups=("ciara", "wheel"))
    w.grant()
    _, end = w.carry("install", spec(), target="incus")
    assert end["ok"], end
    assert w.incus.calls and all(c["argv"][:4] == [*SUDO, INCUS] for c in w.incus.calls)


# -- lxd-docker ---------------------------------------------------------------------------------------------------------
def test_lxd_without_liminal_oci_offers_its_setup_and_plans_nothing(tmp_path):
    w = World(tmp_path, backend="lxd")
    w.grant()
    t = w.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert (t["id"], t["mechanism"], t["state"]) == ("lxd", "lxd-docker", "not answering")
    assert "Set up for services" in t["why"] and "LXD runs no OCI image itself" in t["why"]
    with pytest.raises(Refusal, match="Nothing here starts it"):
        w.jenkins.call("service_plan", "install", "jenkins/ci", spec(), "lxd", {})
    assert w.incus.calls == []
    st = w.page("lxd", "status")
    assert {"form": "setup_oci", "title": "Set up for services…"}.items() <= next(
        a for a in st["actions"] if a.get("form") == "setup_oci").items()


def test_liminal_oci_is_set_up_once_asked_twice(tmp_path):
    w = World(tmp_path, backend="lxd")
    with pytest.raises(LxdRefusal) as asked:
        w.page("lxd", "setup_oci", False)
    assert asked.value.accept == "accept_download" and "download.docker.com" in str(asked.value)
    assert w.incus.calls == []
    lines = []
    got = w.page("lxd", "setup_oci", True, lines.append)
    assert got["ok"], got
    launch, install = w.incus.streamed
    assert launch[:3] == ["launch", "ubuntu:24.04", svcs.OCI]
    assert _kv(launch[i + 1] for i, a in enumerate(launch) if a == "-c") == {
        "security.nesting": "true", "security.syscalls.intercept.mknod": "true",
        "security.syscalls.intercept.setxattr": "true", "boot.autostart": "false"}
    assert install[:5] == ["exec", svcs.OCI, svcs.EXEC_MODE, "--", "sh"] and "download.docker.com" in install[-1]
    assert any("is set up" in x or "Docker installed" in x for x in lines)
    t = w.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert (t["state"], t["mechanism"], t["version"]) == ("ready", "lxd-docker", "28.5.1")
    with pytest.raises(LxdRefusal, match="there already"):
        w.page("lxd", "setup_oci", True)


def test_liminal_oci_s_setup_ends_when_the_plugin_is_let_go(tmp_path):
    w = World(tmp_path, backend="lxd")
    real = w.incus.stream

    def launch_then_let_go(argv, timeout, line, stop=None):
        got = real(argv, timeout, line, stop)
        w.app.detach("lxd")  # turned off while it runs: the install step is never begun
        return got
    w.lxd._streamer = launch_then_let_go
    got = w.lxd.setup_oci(True)
    assert got["ok"] is False and "asked to stop" in got["said"] and "left as it is" in got["said"]
    assert [a[0] for a in w.incus.streamed] == ["launch"]


def test_incus_with_its_own_oci_images_needs_no_liminal_oci(world):
    with pytest.raises(LxdRefusal, match="runs OCI images itself"):
        world.page("lxd", "setup_oci", True)
    assert world.incus.calls == [] and world.incus.streamed == []


def test_a_stopped_liminal_oci_runs_no_command_at_all(tmp_path):
    w = World(tmp_path, backend="lxd", liminal_oci="Stopped")
    w.grant()
    t = w.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert t["state"] == "stopped" and "nothing here starts it" in t["why"]
    with pytest.raises(Refusal, match="Nothing here starts it"):
        w.jenkins.call("service_plan", "install", "jenkins/ci", spec(), "lxd", {})
    assert w.incus.calls == [] and w.incus.instances[svcs.OCI]["status"] == "Stopped"


@pytest.fixture
def docker_world(tmp_path):
    return World(tmp_path, backend="lxd", liminal_oci="Running")


def test_inside_liminal_oci_a_secret_crosses_on_standard_input(docker_world):
    w = docker_world
    w.grant()
    _, end = w.carry("install", spec(secrets=[{"variable": "JENKINS_ADMIN_PASSWORD", "env": "ADMIN_PASSWORD"}]),
                     target="lxd")
    assert end["ok"], end
    create = next(c for c in w.incus.calls if "create" in c["args"] and "docker" in c["args"]
                  and c["args"][c["args"].index("docker") + 1] == "create")
    assert create["argv"][:6] == [LXC, "exec", svcs.OCI, svcs.EXEC_MODE, "--", "sh"]
    assert create["argv"][6:9] == ["-c", svcs.ENV_SH, "liminal-env"] and SECRET not in " ".join(create["argv"])
    assert create["stdin"] == f"ADMIN_PASSWORD={SECRET}\n".encode() and create["env"] == {}
    line = " ".join(create["argv"])
    assert "-e ADMIN_PASSWORD" in line and "--restart no" in line and "-p 8080:8080/tcp" in line
    assert w.incus.docker.containers["liminal-jenkins-ci-controller"]["secret_values"] == {"ADMIN_PASSWORD": SECRET}
    assert all(SECRET not in " ".join(c["argv"]) for c in w.incus.calls)


def test_inside_liminal_oci_volumes_are_custom_volumes_and_ports_proxy_devices(docker_world):
    w = docker_world
    w.grant()
    _, end = w.carry("install", spec(), target="lxd")
    assert end["ok"], end
    vol = "liminal-jenkins-ci-home"
    assert w.incus.volumes[vol]["config"]["user.liminal.service"] == "jenkins/ci"
    devices = w.incus.instances[svcs.OCI]["devices"]
    assert devices[vol] == {"type": "disk", "pool": "default", "source": vol, "path": f"/srv/liminal/{vol}"}
    assert w.incus.docker.volumes[vol]["labels"]["liminal.service"] == "jenkins/ci"
    made = next(a for a in w.incus.docker.argv_of("volume create"))
    assert "device=/srv/liminal/liminal-jenkins-ci-home" in made and "o=bind" in made
    # 0.4.1: made for its spec, owned by its owner as the daemon makes it (initial.uid): no chown inside
    assert (w.incus.volumes[vol]["config"]["initial.uid"], w.incus.volumes[vol]["config"]["initial.gid"]) == ("1000", "1000")
    assert not any("chown" in c["args"] for c in w.incus.calls)
    proxies = {k: v for k, v in devices.items() if v["type"] == "proxy"}
    assert {v["listen"] for v in proxies.values()} == {"tcp:0.0.0.0:8080", "tcp:[::]:8080"}
    assert {v["connect"] for v in proxies.values()} == {"tcp:127.0.0.1:8080"}
    w.carry("expose", options={"lan": False})
    proxies = {k: v for k, v in w.incus.instances[svcs.OCI]["devices"].items() if v["type"] == "proxy"}
    assert [v["listen"] for v in proxies.values()] == ["tcp:127.0.0.1:8080"]
    w.carry("remove")
    devices = w.incus.instances[svcs.OCI]["devices"]
    assert not any(v["type"] == "proxy" for v in devices.values()) and vol in devices  # the data kept, still bound


def test_the_conformance_sequence_runs_on_docker_inside_liminal_oci(docker_world):
    w = docker_world
    w.grant()
    done = standins.conformance(w.jenkins, w.page, spec(), "lxd", w.wait)
    assert done[-1] == "conformance"
    assert w.incus.docker.containers == {} and w.incus.docker.volumes == {} and w.incus.docker.networks == {}
    assert w.incus.volumes == {} and w.incus.instances[svcs.OCI]["devices"] == {}


def test_liminal_oci_is_not_deleted_while_it_holds_a_service(docker_world):
    w = docker_world
    w.grant()
    w.carry("install", spec(), target="lxd")
    w.incus.instances[svcs.OCI]["status"] = "Stopped"
    with pytest.raises(LxdRefusal, match="cannot be read while it does not answer"):
        w.page("lxd", "delete", svcs.OCI, True)
    w.incus.instances[svcs.OCI]["status"] = "Running"
    with pytest.raises(LxdRefusal, match="holds Liminal's services"):
        w.page("lxd", "restore", svcs.OCI, "snap0", True)
    assert w.linux.changes == []


# -- what is never done -------------------------------------------------------------------------------------------------
def test_its_own_remove_and_restore_refuse_a_services_instance(world):
    world.grant()
    world.carry("install", spec(), target="incus")
    before = len(world.linux.changes)
    for hook, args in (("delete", ("liminal-jenkins-ci-controller", True)),
                       ("restore", ("liminal-jenkins-ci-controller", "snap0", True))):
        with pytest.raises(LxdRefusal, match="on jenkins's page"):
            world.page("lxd", hook, *args)
    assert "liminal-jenkins-ci-controller" in world.incus.instances and len(world.linux.changes) == before


def test_its_own_changes_are_its_pages_alone(world):
    for hook, args in (("power", ("web", "stop")), ("snapshot", ("web",)), ("restore", ("web", "snap0", True)),
                       ("delete", ("db", True)), ("service", ("incus.service",)), ("wake", ()), ("setup_oci", (True,))):
        with pytest.raises(AskRefused):
            world.jenkins.call(hook, *args)
    assert world.incus.calls == [] and world.linux.changes == []


def test_classic_lxc_is_not_offered_and_asked_nothing_more(tmp_path):
    fake = Machine("lxc", os="debian", units="lxc", groups=("ciara", "sudo"), daemons=(), sockets=(), incus=FakeIncus())
    w = World(tmp_path, linux=fake, incus=fake.incus)
    t = w.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert (t["id"], t["state"], t["why"]) == ("lxc", "not answering", LXC_WORDS)
    assert fake.services == 0 and w.incus.calls == []
    st = w.page("lxd", "status")
    assert "Liminal's services" not in [x["title"] for x in st["tables"]]


def test_an_idle_daemon_is_never_woken_by_a_service_plugin(tmp_path):
    from test_plugin import UNITS  # noqa: F401 - the recorded units

    idle = Machine(units="incus-idle", daemons=(), sockets=("/var/lib/incus/unix.socket",), incus=FakeIncus())
    w = World(tmp_path, linux=idle, incus=idle.incus)
    w.grant()
    t = w.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert t["state"] == "idle" and "nothing here wakes it" in t["why"]
    with pytest.raises(Refusal, match="Nothing here starts it"):
        w.jenkins.call("service_plan", "install", "jenkins/ci", spec(), "incus", {})
    assert idle.services == 0 and idle.reads == [] and w.incus.calls == []


def test_on_windows_it_is_not_for_this_machine_and_asks_nothing(tmp_path):
    w = World(tmp_path, native="LXC / LXD is for Linux: this is Windows")
    t = w.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert (t["here"], t["state"]) == (False, "not here") and "Windows" in t["why"]
    assert w.linux.calls == [] and w.incus.calls == []


def test_turning_it_off_stops_its_page_services(world):
    world.grant()
    world.carry("install", spec(), target="incus")
    world.app.off.add("lxd")
    world.app.detach("lxd")
    assert world.incus.instances["liminal-jenkins-ci-controller"]["status"] == "Stopped"
    assert not any(c["args"][0] == "delete" or c["args"][:3] == ["storage", "volume", "delete"]
                   for c in world.incus.calls)


def test_the_page_lists_services_plans_plugins_and_kept_data(world):
    s = world.page("lxd", "status")
    titles = [t["title"] for t in s["tables"]]
    assert titles[-4:] == ["Liminal's services", "Waiting for your answer", "Service plugins", "Kept data"]
    assert world.linux.services == 0  # nothing asked of the services until a service plugin asks


def test_a_stopped_daemon_or_no_rights_plan_nothing(tmp_path):
    stopped = Machine(units="incus-stopped", daemons=(), sockets=(), incus=FakeIncus())
    w = World(tmp_path / "stopped", linux=stopped, incus=stopped.incus)
    w.grant()
    t = w.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert t["state"] == "stopped" and "nothing here starts it" in t["why"]
    outsider = Machine(groups=("ciara", "wheel"), sudo=False, incus=FakeIncus())
    w2 = World(tmp_path / "outsider", linux=outsider, incus=outsider.incus)
    t = w2.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert t["state"] == "no rights" and "sudo asks for a password" in t["why"]
    for m, wo in ((stopped, w), (outsider, w2)):
        with pytest.raises(Refusal):
            wo.jenkins.call("service_plan", "install", "jenkins/ci", spec(), "incus", {})
        assert m.services == 0 and wo.incus.calls == []


def test_inside_wsl_its_stack_is_wsl_as_every_container_plugin_names_it(tmp_path, monkeypatch):
    """0.4.1 (liminal-libs 1.95.0's containers.stack_of, decided 2026-11-25): "wsl" inside WSL, "linux" outside."""
    from liminal_libs.machines import linux as linux_mod

    w = World(tmp_path / "linux")
    t = w.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert (t["stack"], t["path"], t["firewall"]) == ("linux", "direct", "linux")
    monkeypatch.setattr(linux_mod, "wsl", lambda release=None, env=None: "Ubuntu")
    w = World(tmp_path / "wsl")
    t = w.jenkins.call("hosting", "jenkins", {"linux": spec()})["targets"][0]
    assert (t["stack"], t["path"], t["firewall"]) == ("wsl", "wsl-nat", "windows") and "WSL's relay" in t["lan_words"]


def test_inside_liminal_oci_a_volume_of_another_service_is_never_made_over(docker_world):
    w = docker_world
    w.grant()
    w.incus.docker.volumes["liminal-jenkins-ci-home"] = {"labels": {"liminal": "volume", "liminal.service": "gocd/ci"},
                                                         "files": {}}
    _, end = w.carry("install", spec(), target="lxd")
    assert end["ok"] is False and "is not jenkins/ci's" in json.dumps(end)
    assert w.incus.volumes == {} and w.incus.docker.argv_of("volume create") == []


def test_a_service_command_never_takes_an_environment():
    with pytest.raises(Refusal, match="standard input alone"):
        svcs.service_run(["incus", "list"], 5, env={"X": "y"})
    inner = []
    run = svcs.through_stdin(lambda argv, timeout, **kw: inner.append((argv, kw)) or (0, "", ""))
    with pytest.raises(Refusal, match="not both"):
        run(["lxc", "exec", svcs.OCI, "--", "docker", "cp", "-", "x:/"], 5, stdin=b"tar", env={"A": "b"})
    with pytest.raises(Refusal, match="one line"):
        run(["lxc", "exec", svcs.OCI, "--", "docker", "create"], 5, env={"A": "two\nlines"})
    run(["lxc", "exec", svcs.OCI, "--", "docker", "create"], 5, env={"A": "b c"})
    argv, kw = inner[-1]
    assert argv[4:8] == ["sh", "-c", svcs.ENV_SH, "liminal-env"] and kw["stdin"] == b"A=b c\n" and kw["env"] is None
    assert inner and len(inner) == 1


def test_a_plain_stand_in_shell_never_runs_a_service_command_for_real(tmp_path):
    p = LxdPlugin(shell=Shell("local", runner=FakeLinux()), native="")
    with pytest.raises(LxdRefusal, match="takes no service command"):
        p._service_command(["incus", "list"], 5)
    with pytest.raises(LxdRefusal, match="streams no command"):
        p._stream(["incus", "image", "copy"], 5, print)


def service_flow(tmp_path) -> list[list[str]]:
    """Every service command the plugin runs on Incus' own OCI instances and on Docker inside liminal-oci (for its
    API cards' test): the conformance sequence, a log read, a file, a backup put back, liminal-oci's setup."""
    flows = []
    for backend, oci in (("incus", None), ("lxd", "Running")):
        w = World(tmp_path / backend, backend=backend, liminal_oci=oci)
        w.grant()
        w.carry("install", spec(seeds=[{"volume": "home", "path": "a.txt", "text": "x"}]), target=backend)
        if backend == "incus":
            w.incus.volumes["liminal-jenkins-ci-home"]["files"]["secrets/initialAdminPassword"] = b"x"
        else:
            w.incus.docker.volumes["liminal-jenkins-ci-home"]["files"]["secrets/initialAdminPassword"] = b"x"
        w.jenkins.call("hosted_file", "jenkins/ci", "controller", "unlock")
        w.jenkins.call("hosted_logs", "jenkins/ci")
        w.carry("stop")
        w.carry("start")
        newer = spec()
        newer["parts"][0]["image"]["digest"] = "sha256:" + "b" * 64
        w.carry("update", newer)
        w.carry("rollback", options={"choice": "backup"})
        w.carry("remove")
        w.carry("delete_data", options={"backups": "delete"})
        w.jenkins.call("segments")
        flows += [c["argv"] for c in w.incus.calls]
    for backend in ("lxd", "incus"):  # Incus without its OCI support takes liminal-oci too
        setup = World(tmp_path / f"setup-{backend}", backend=backend, oci=False)
        assert setup.page("lxd", "setup_oci", True)["ok"]
        flows += [c["argv"] for c in setup.incus.calls]
    return flows


def test_the_service_flow_ends_well_on_both(tmp_path):
    ran = service_flow(tmp_path)
    assert ran and any("init" in a for a in ran) and any("docker" in a for a in ran)


# -- 0.4.1: liminal-libs 1.95.0's hooks ------------------------------------------------------------------------------------
def test_an_incus_volume_is_made_for_its_spec_and_owned_by_its_owner(world):
    world.grant()
    rt = world.lxd.runtime_of(world.lxd.target("incus"))
    vol = svcs.C.Volume("home", "/var/jenkins_home", "its jobs", 1000)
    labels = {"liminal": "volume", "liminal.service": "jenkins/ci"}
    assert rt.make_volume_for("liminal-jenkins-ci-home", labels, vol) is True
    assert rt.make_volume_for("liminal-other", labels, svcs.C.Volume("x", "/x", "x")) is True  # no owner said: none given
    made = world.incus.volumes
    assert (made["liminal-jenkins-ci-home"]["config"]["initial.uid"], made["liminal-jenkins-ci-home"]["config"]["initial.gid"]) == (
        "1000", "1000")
    assert "initial.uid" not in made["liminal-other"]["config"]
    assert not hasattr(rt, "owners")  # 0.4.1: kept by no runtime: the spec's volume says it


def test_inside_liminal_oci_a_daemon_without_initial_values_gets_a_chown(tmp_path):
    w = World(tmp_path, backend="lxd", liminal_oci="Running",
              incus=FakeIncus(LXC, oci=False, tools=(), liminal_oci="Running", docker=FakeDocker(version="28.5.1"),
                              initial=False))
    w.grant()
    _, end = w.carry("install", spec(), target="lxd")
    assert end["ok"], end
    vol = "liminal-jenkins-ci-home"
    assert "initial.uid" not in w.incus.volumes[vol]["config"]
    assert any(c["args"][-3:] == ["chown", "1000:1000", f"/srv/liminal/{vol}"] for c in w.incus.calls)


def test_an_incus_update_is_backed_up_by_a_snapshot_without_tar_and_put_back_from_its_record(world):
    world.grant()
    s = spec()
    s["parts"][0]["image"]["tar"] = False  # 0.4.1: an image without tar needs no "no backup" accept: Incus snapshots
    world.carry("install", s, target="incus")
    world.incus.volumes["liminal-jenkins-ci-home"]["files"]["config.xml"] = b"<old/>"
    newer = copy.deepcopy(s)
    newer["parts"][0]["image"]["digest"] = "sha256:" + "b" * 64
    plan, end = world.carry("update", newer)
    assert end["ok"], end
    assert "accept_no_backup" not in [a["name"] for a in plan["accepts"]]
    words = json.dumps(plan)
    assert "snapshot the volume liminal-jenkins-ci-home" in words
    snap = next(iter(world.incus.volumes["liminal-jenkins-ci-home"]["snapshots"]))
    records = list(world.lxd._backups_of("jenkins/ci").glob("*/home.snapshot.json"))
    assert len(records) == 1 and not list(world.lxd._backups_of("jenkins/ci").glob("*/home.tar"))
    assert json.loads(records[0].read_text("utf-8")) == {"kind": "snapshot", "volume": "liminal-jenkins-ci-home",
                                                         "said": snap}
    assert any(f"snapshotted by its engine: {snap}" in line for line in end["lines"])
    assert not any("bytes" in line for line in end["lines"])
    world.incus.volumes["liminal-jenkins-ci-home"]["files"]["config.xml"] = b"<converted/>"
    _, end = world.carry("rollback", options={"choice": "backup"})
    assert end["ok"], end
    assert world.incus.volumes["liminal-jenkins-ci-home"]["files"]["config.xml"] == b"<old/>"
    assert any(f"(the snapshot {snap})" in line for line in end["lines"])


def test_an_incus_restore_reads_its_own_record_and_a_0_4_0_one_and_no_other(world, tmp_path):
    world.grant()
    world.carry("install", spec(), target="incus")
    rt = world.lxd.runtime_of(world.lxd.target("incus"))
    assert rt.backup_kind(None) == "snapshot"
    vol = "liminal-jenkins-ci-home"
    world.incus.volumes[vol]["files"]["a"] = b"1"
    said = rt.backup(vol, "x", tmp_path / "20261125T000000Z" / "home.tar", lambda line: None, lambda: False)
    assert said == "liminal-20261125T000000Z" and not (tmp_path / "20261125T000000Z" / "home.tar").exists()
    world.incus.volumes[vol]["files"]["a"] = b"2"
    old = tmp_path / "old.tar"  # a 0.4.0 backup's own record, kept in place of a tar
    old.write_text(json.dumps({"kind": "incus-snapshot", "pool": "default", "volume": vol, "snapshot": said}), "utf-8")
    rt.restore(vol, "x", old, lambda line: None, lambda: False)
    assert world.incus.volumes[vol]["files"]["a"] == b"1"
    for bad, why in (({"kind": "snapshot", "volume": "liminal-gocd-ci-home", "said": said}, "another volume"),
                     ({"kind": "snapshot", "volume": vol, "said": "theirs"}, "no snapshot of Liminal's"),
                     ({"kind": "incus-snapshot", "pool": "elsewhere", "volume": vol, "snapshot": said}, "another volume")):
        f = tmp_path / "bad.snapshot.json"
        f.write_text(json.dumps(bad), "utf-8")
        with pytest.raises(Refusal, match=why):
            rt.restore(vol, "x", f, lambda line: None, lambda: False)


def test_each_runtime_says_who_can_read_a_secret_it_is_handed(world, tmp_path):
    secret = [{"variable": "JENKINS_ADMIN_PASSWORD", "env": "ADMIN_PASSWORD"}]
    world.grant()
    plan = world.jenkins.call("service_plan", "install", "jenkins/ci", spec(secrets=secret), "incus", {})
    kept = next(a for a in plan["accepts"] if a["name"] == "accept_kept_secret")
    assert "incus config show" in json.dumps(kept) and "incus-admin" in json.dumps(kept)
    assert not hasattr(type(world.lxd), "_kept_secret_words") or "_kept_secret_words" not in type(world.lxd).__dict__
    d = World(tmp_path, backend="lxd", liminal_oci="Running")
    d.grant()
    plan = d.jenkins.call("service_plan", "install", "jenkins/ci", spec(secrets=secret), "lxd", {})
    kept = next(a for a in plan["accepts"] if a["name"] == "accept_kept_secret")
    assert "docker inspect" in json.dumps(kept) and svcs.OCI in json.dumps(kept)


def test_inside_liminal_oci_its_networks_sit_behind_liminal_oci_and_it_is_a_found_member(docker_world):
    w = docker_world
    w.grant()
    rt = w.lxd.runtime_of(w.lxd.target("lxd"))
    assert rt.network_want(None, "jenkins/ci") == {"behind": "10.120.17.50"}
    _, end = w.carry("install", spec(), target="lxd")
    assert end["ok"], end
    a = next(x for x in w.firewall._book.all() if x.sid == "jenkins/ci")
    assert a.route and a.route["next_hop"] == "10.120.17.50"  # the route through liminal-oci's own address
    routes = [s for steps in w.firewall.ran for s in steps if s["kind"] == "route"]
    assert routes and "through 10.120.17.50 (the instance it sits behind)" in routes[0]["words"]
    segs = w.jenkins.call("segments")["segments"]
    bridge = next(s for s in segs if s["kind"] == "incus-bridge")
    assert (bridge["bridge"], bridge["sid"], bridge["subnets"], bridge["assigns"]) == ("incusbr0", "", ["10.120.17.0/24"],
                                                                                       "incus")
    assert bridge["members"] == [{"sid": "", "part": "", "container": svcs.OCI, "mac": "00:16:3e:4c:00:01",
                                  "ipv4": "10.120.17.50", "assigned": "incus"}]
    assert any(s["kind"] == "nested" for s in segs)
    w.incus.oci_address = ""  # no address yet: nothing said, the firewall's own way
    assert rt.network_want(None, "jenkins/ci") == {}
