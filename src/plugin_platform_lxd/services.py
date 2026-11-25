"""Liminal's services on Incus and LXD (0.4.0; 0.4.1 on liminal-libs 1.95.0's hooks): the two mechanisms behind
liminal-libs 1.93.0's ContainerHost
(service-containers-design §4.4 and §4.5, the networking design's §5.6 and §5.7, decision 15 a and N25 a).

- **`incus-oci`** (`IncusRuntime`), where the daemon lists the API extension `instance_oci` and the host has skopeo
  and umoci: each part an Incus instance made from its OCI image, asked for by its digest through an OCI remote of
  the image's registry (added once, inside the first approved plan that needs it), its whole configuration on
  standard input (`incus init IMAGE NAME < config`: `environment.*` with the secrets, `user.liminal.*`, `limits.memory`
  and `boot.autostart=false` set explicitly), its data in custom volumes of the pool, a proxy device per published
  port, its own Liminal bridge per segment (`lim-` and 11 hex characters: a bridge's name is an interface's, 15 at
  most) with the subnet the firewall hands out, each part's address pinned by its NIC's `ipv4.address`. An update's
  backup is a snapshot of each custom volume (`liminal-<stamp>`, the last two kept beside the platform's two backup
  folders): since 0.4.1 its kind is "snapshot" (liminal-libs 1.95.0's `backup_kind`), its name kept by the base in
  `<volume>.snapshot.json`, which a rollback hands back; no tar is needed.
- **`lxd-docker`** (`OciDocker`), for Canonical LXD, an Incus without OCI support, or a host without skopeo or umoci:
  Docker inside one system container, `liminal-oci` (set up on your approval, as a job on this plugin's page), driven
  exactly as the library's `DockerRuntime` drives an engine, through `incus exec liminal-oci -- docker ...` (LXD's
  `lxc exec`). Values cross on standard input into the docker command's environment there, never on a command line;
  each service volume is a custom volume attached to liminal-oci at `/srv/liminal/<volume>` and bound into the
  container; each published port a proxy device from this machine's port to liminal-oci's own. Since 0.4.1 its
  networks sit behind liminal-oci's own address on Incus' bridge (`network_want`'s `behind`: the firewall routes them
  through it), and liminal-oci is listed among its segments as a found member of that bridge (Incus addresses it).

Since 0.4.1 each volume is made for its spec (`make_volume_for`: `initial.uid`/`initial.gid` from the volume's owner),
and each runtime says who can read a secret it is handed (`kept_secret_words`).

Every command goes through the runner the plugin hands in, `runner(argv, timeout, stdin=None, env=None, binary=False,
out=None) -> (code, out, err)`, root's prefix (`sudo -n --`) already in `argv` where the platform needs it. The real
runner, `service_run`, refuses an environment: a value goes on standard input or not at all. Nothing here starts a
daemon, wakes an idle one, or starts liminal-oci: the targets say so, and the base refuses a plan first."""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Mapping

from liminal_libs.instances import Refusal
from liminal_libs.machines import containers as C
from liminal_libs.machines.containerhost import Create, Runtime
from liminal_libs.machines.containers import Aspect, Fit, ImagePin, ServiceSpec
from liminal_libs.machines.dockerruntime import DockerRuntime, bridge_name

OCI = "liminal-oci"  # the one system container Docker runs in (decision 15 a)
OCI_MOUNTS = "/srv/liminal"  # where a service volume is attached inside liminal-oci
MARK = "== liminal-lxd-services 1"
READ_S = 60.0
CHANGE_S = 300.0
PULL_S = 1800.0
BACKUP_S = 3600.0
SETUP_S = 1800.0
SNAPSHOT = "liminal-"  # an update's snapshot of a custom volume: liminal-<the backup folder's UTC stamp>
ENV_SH = 'while IFS= read -r l; do export "$l"; done; exec "$@"'  # NAME=value lines on standard input, then the command
EXEC_MODE = "--mode=non-interactive"  # standard input, output and errors each a pipe of its own
ARCHES = {"x86_64": "amd64", "aarch64": "arm64"}
# what liminal-oci is made from, by the software (LXD lost images.linuxcontainers.org in 2024: Ubuntu's own remote there)
OCI_IMAGE = {"incus": "images:debian/13", "lxd": "ubuntu:24.04"}
OCI_SIZES = ("about 120 MB for the system image, about 450 MB for Docker's packages, and the room its services' images "
             "take")
OCI_CONFIG = ("security.nesting=true", "security.syscalls.intercept.mknod=true",
              "security.syscalls.intercept.setxattr=true", "boot.autostart=false")
# Docker from Docker's own repository (28.2.0 or later: a service network's trusted_host_interfaces), not the
# distribution's docker.io (the networking design's §5.7)
DOCKER_INSIDE = r"""set -eu
export DEBIAN_FRONTEND=noninteractive
i=0; until getent hosts download.docker.com >/dev/null 2>&1; do i=$((i+1)); [ $i -lt 60 ] || exit 3; sleep 1; done
. /etc/os-release
apt-get update -q
apt-get install -y -q ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io
docker version --format '{{.Server.Version}}'
"""


class IncusFailed(RuntimeError):
    """An incus (or LXD's lxc) command that failed, in its own last words."""


# -- the runners ---------------------------------------------------------------------------------------------------------
def _text(raw) -> str:
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else raw.decode("utf-8", "replace")


def _environment() -> dict:
    """A child's environment without Liminal's own variables (LIMINAL_*) and without the pairs the installed plugins
    declare (their keys), in the C locale."""
    try:
        from liminal_libs.sources.registry import variables

        declared = set(variables() or ())
    except Exception:  # noqa: BLE001 - a registry that cannot say: Liminal's own names still go
        declared = set()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("LIMINAL_") and k not in declared}
    env["LC_ALL"] = "C"
    return env


def service_run(argv, timeout: float, stdin: bytes | None = None, env: Mapping | None = None, binary: bool = False,
                out: str | None = None):
    """A service command: `stdin` given to it, its output as bytes (`binary`) or written to the file `out`. An
    environment is refused: through sudo it would be dropped, and a value belongs on standard input (the design's
    "values cross on standard input ... never on a command line"). (exit code, output, errors)."""
    if env:
        raise Refusal("a service command takes its values on standard input alone, never by environment: nothing ran")
    args = [str(a) for a in argv]
    feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        if out:
            with open(out, "wb") as sink:
                done = subprocess.run(args, stdout=sink, stderr=subprocess.PIPE, timeout=timeout, env=_environment(),
                                      **feed)
            return done.returncode, b"", _text(done.stderr)
        done = subprocess.run(args, capture_output=True, timeout=timeout, env=_environment(), **feed)
    except subprocess.TimeoutExpired:
        raise IncusFailed(f"{' '.join(shlex.quote(a) for a in args[:6])} took more than {timeout:g} s") from None
    except OSError as e:
        raise IncusFailed(f"{args[0] if args else 'the command'} could not run: {e}") from None
    return done.returncode, (done.stdout or b"") if binary else _text(done.stdout), _text(done.stderr)


def stream(argv, timeout: float, line: Callable[[str], None], stop: Callable[[], bool] | None = None):
    """A long command (a pull), each line it prints to `line` as it comes; ended once `stop` says so (looked at every
    0.25 s) or at `timeout`. (exit code, its last lines)."""
    args = [str(a) for a in argv]
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=_environment(), start_new_session=True)
    tail: list[str] = []

    def read() -> None:
        for raw in iter(proc.stdout.readline, b""):
            text = _text(raw).rstrip()
            if text:
                tail.append(text)
                del tail[:-20]
                line(text)
    reader = threading.Thread(target=read, name="liminal-lxd-stream", daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            if (stop is not None and stop()) or time.monotonic() > deadline:
                proc.kill()
                proc.wait(10)
                raise IncusFailed(f"{args[0]} was ended: " + ("asked to stop" if stop is not None and stop()
                                                              else f"it took more than {timeout:g} s"))
            time.sleep(0.25)
    finally:
        reader.join(5)
    return proc.returncode, "\n".join(tail)


def through_stdin(runner: Callable) -> Callable:
    """A runner for `incus exec liminal-oci -- docker ...` that carries an environment (a secret's value) across on
    standard input: the command becomes `sh -c ENV_SH liminal-env docker ...` inside, reading NAME=value lines first.
    A command that also needs standard input of its own, or a value a line cannot hold, is refused."""
    def run(argv, timeout, stdin=None, env=None, binary=False, out=None):
        argv = [str(a) for a in argv]
        if env:
            if stdin is not None:
                raise Refusal("a command inside liminal-oci takes either its values or its own input on standard "
                              "input, not both: nothing ran")
            for k, v in env.items():
                if not C.ENV.match(str(k)) or "\n" in str(v) or "\0" in str(v):
                    raise Refusal(f"{k}: a value handed inside liminal-oci is one line of text: nothing ran")
            cut = argv.index("--")
            argv = [*argv[:cut + 1], "sh", "-c", ENV_SH, "liminal-env", *argv[cut + 1:]]
            stdin = "".join(f"{k}={v}\n" for k, v in env.items()).encode("utf-8")
        return runner(argv, timeout, stdin=stdin, env=None, binary=binary, out=out)
    return run


# -- the words both share -----------------------------------------------------------------------------------------------
def user_keys(labels: Mapping) -> dict:
    """Liminal's labels as Incus' configuration keys (user.liminal, user.liminal.service, ...)."""
    return {f"user.{k}": str(v) for k, v in labels.items()}


def labels_of(config: Mapping) -> dict:
    return {k[5:]: v for k, v in dict(config or {}).items() if k.startswith("user.liminal")}


def labelled_spec(spec_json: str) -> str:
    """The spec as an instance's configuration keeps it: seeds' texts left out (written once, at install)."""
    d = ServiceSpec.of(json.loads(spec_json)).as_dict()
    for p in d["parts"]:
        for s in p.get("seeds") or []:
            s["text"] = ""
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def registry_of(ref: str) -> tuple[str, str]:
    """(the registry, the repository without its tag) of an image's reference: docker.io for one that names none."""
    first, _, rest = ref.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, rest
    else:
        registry, path = "docker.io", ref
    repo = path.rsplit(":", 1)[0] if ":" in path.rsplit("/", 1)[-1] else path
    return registry, repo


def remote_name(registry: str) -> str:
    """The OCI remote Liminal adds for a registry: liminal-docker for Docker Hub (the design's), liminal-<its name>."""
    if registry in ("docker.io", "registry-1.docker.io", "index.docker.io"):
        return "liminal-docker"
    return "liminal-" + re.sub(r"[^a-z0-9]+", "-", registry.lower()).strip("-")[:40]


def image_alias(pin: ImagePin) -> str:
    """The local alias an image fetched for a service keeps: liminal-image- and 16 hex characters of its digest."""
    return "liminal-image-" + pin.digest.split(":", 1)[1][:16]


def proxy_listens(protocol: str, host: int, lan: bool) -> list[str]:
    """Where a proxy device listens: every address (IPv4 and IPv6) for a port open to your network, this machine's
    loopback otherwise."""
    if lan:
        return [f"{protocol}:0.0.0.0:{host}", f"{protocol}:[::]:{host}"]
    return [f"{protocol}:127.0.0.1:{host}"]


def _flat(sid: str) -> str:
    owner, name = C.split_sid(sid)
    return f"{owner}-{name}"


def _sorted_json(out) -> object:
    try:
        return json.loads(_text(out) or "null")
    except ValueError:
        return None


# -- incus-oci -----------------------------------------------------------------------------------------------------------
class IncusRuntime(Runtime):
    """Incus' own OCI instances: one target's mechanism. `prefix` is the client as the platform runs it (["incus"],
    or ["sudo", "-n", "--", "/usr/bin/incus"]); `pool` the storage pool its volumes go to (the default profile's)."""

    mechanism = "incus-oci"

    def __init__(self, prefix, runner: Callable, streamer: Callable | None = None, *, pool: str, arch: str = "amd64",
                 version: str = "", lan=("direct", ""), where_data: str = ""):
        self.prefix = [str(a) for a in prefix]
        self._runner, self._streamer = runner, streamer
        self.pool, self.arch, self.version = str(pool), str(arch or "amd64"), str(version or "")
        self.lan_path, self.lan_words = lan
        self.where_data = where_data or f"in custom volumes of the storage pool {pool}"

    # -- the runner ----------------------------------------------------------------------------------------------------
    def run(self, *args, timeout: float = READ_S, check: bool = True, stdin: bytes | None = None, binary: bool = False):
        argv = [*self.prefix, *[str(a) for a in args]]
        code, out, err = self._runner(argv, timeout, stdin=stdin, env=None, binary=binary, out=None)
        if check and code != 0:
            words = _text(err).strip() or _text(out if not binary else b"").strip()
            raise IncusFailed(words.splitlines()[-1] if words else f"incus {args[0]} failed ({code})")
        return code, out, err

    def _json(self, *args):
        _, out, _ = self.run(*args)
        return _sorted_json(out)

    def _instances(self) -> list[dict]:
        got = self._json("list", "--format", "json")
        return [i for i in got if isinstance(i, dict)] if isinstance(got, list) else []

    def _instance(self, name: str) -> dict | None:
        return next((i for i in self._instances() if i.get("name") == name), None)

    @staticmethod
    def _config(inst: dict) -> dict:
        return dict(inst.get("config") or {})

    @staticmethod
    def _devices(inst: dict) -> dict:
        return dict(inst.get("expanded_devices") or inst.get("devices") or {})

    @staticmethod
    def _state_of(inst: dict) -> str:
        """Incus' status in the words the base reads (Docker's): a stopped instance is "exited" (its code not known)."""
        s = str(inst.get("status") or "").lower()
        return {"running": "running", "frozen": "paused", "starting": "restarting", "stopping": "running"}.get(s, "exited")

    def _part_row(self, inst: dict) -> dict:
        cfg = self._config(inst)
        name = str(inst.get("name") or "")
        return {"name": cfg.get("user.liminal.part", ""), "container": name,
                "role": "previous" if name.endswith("-previous") else "current", "state": self._state_of(inst),
                "exit": None, "health": "", "image": cfg.get("user.liminal.image", ""),
                "digest": cfg.get("user.liminal.digest", ""), "since": str(inst.get("last_used_at") or "")}

    def _volume_at(self, container: str, path: str) -> tuple[str, str]:
        """(the custom volume, the path inside it) a container's path lies in: refused when it lies in none."""
        inst = self._instance(container)
        if inst is None:
            raise Refusal(f"{container} is not there")
        best = None
        for d in self._devices(inst).values():
            if d.get("type") == "disk" and d.get("source") and d.get("pool"):
                mount = str(d.get("path") or "").rstrip("/")
                if mount and (path == mount or path.startswith(mount + "/")) and (best is None or len(mount) > len(best[1])):
                    best = (str(d["source"]), mount)
        if best is None:
            raise Refusal(f"{path} lies in none of {container}'s volumes")
        return best[0], path[len(best[1]):].lstrip("/")

    # -- what it reads -------------------------------------------------------------------------------------------------
    def fit(self, spec: ServiceSpec | None) -> Fit:
        aspects: dict[str, Aspect] = {}
        if spec is not None:
            for p in spec.parts:
                if p.image.system != "linux":
                    aspects["image"] = Aspect("cannot", "Incus runs Linux images alone")
                elif not any(x.split("/")[1] == self.arch for x in p.image.platforms):
                    aspects["image"] = Aspect("cannot", f"{p.image.ref} is not published for {self.arch}")
                if p.command:
                    aspects["command"] = Aspect("cannot", "an OCI instance here runs its image's own command: a part's "
                                                          "command is not set (Incus' oci.* keys: to check)")
            aspects.setdefault("image", Aspect("differs", "asked for by its digest through Incus' OCI remote (skopeo "
                                                          "checks the digest as it copies; Incus keeps a fingerprint of "
                                                          "its own beside it)"))
            aspects["ports"] = Aspect("exact", "a proxy device per port (a socket of this machine's own)")
            aspects["volumes"] = Aspect("exact", f"custom volumes in the storage pool {self.pool}")
            aspects["network"] = Aspect("differs", "its own Liminal bridge; Incus routes between its managed bridges on "
                                                   "one server, and no network ACL keeps the others out yet")
            aspects["logs"] = Aspect("differs", "its console log: a ring buffer, read once")
            aspects["lifecycle"] = Aspect("exact", "boot.autostart=false: it stays stopped when the daemon starts")
        return Fit.judged(aspects)

    def hosted(self) -> list[dict]:
        by: dict[str, dict] = {}
        for inst in self._instances():
            cfg = self._config(inst)
            if cfg.get("user.liminal") != "service" or not cfg.get("user.liminal.service"):
                continue
            sid = cfg["user.liminal.service"]
            row = by.setdefault(sid, {"sid": sid, "parts": [], "spec": None, "spec_sha": "", "platform": "",
                                      "lifecycle": "page", "made": ""})
            part = self._part_row(inst)
            row["parts"].append(part)
            if part["role"] == "current" or row["spec"] is None:
                row["spec"] = _sorted_json(cfg.get("user.liminal.spec") or "null")
                row.update({"spec_sha": cfg.get("user.liminal.spec-sha", ""),
                            "platform": cfg.get("user.liminal.platform", ""),
                            "lifecycle": cfg.get("user.liminal.lifecycle", "page"),
                            "made": cfg.get("user.liminal.made", "")})
        return list(by.values())

    def _custom(self) -> list[dict]:
        got = self._json("storage", "volume", "list", self.pool, "--format", "json")
        rows = got if isinstance(got, list) else []
        return [v for v in rows if isinstance(v, dict) and v.get("type") == "custom"]

    def volumes(self) -> list[dict]:
        out = []
        for v in self._custom():
            cfg = dict(v.get("config") or {})
            if cfg.get("user.liminal") != "volume":
                continue
            users = [str(u).split("?", 1)[0].rsplit("/", 1)[-1] for u in v.get("used_by") or []
                     if "/instances/" in str(u)]
            out.append({"name": str(v.get("name")), "sid": cfg.get("user.liminal.service", ""),
                        "volume": cfg.get("user.liminal.volume", ""), "made": cfg.get("user.liminal.made", ""),
                        "platform": cfg.get("user.liminal.platform", ""), "used_by": users, "size": cfg.get("size", "")})
        return out

    def facts(self, spec: ServiceSpec | None, sid: str) -> dict:
        images = {p.image.digest: self.has_image(p.image) for p in (spec.parts if spec else ())}
        containers = {}
        for r in self.hosted():
            if r["sid"] == sid:
                for part in r["parts"]:
                    containers[part["container"]] = {"state": part["state"], "role": part["role"],
                                                     "digest": part["digest"]}
        volumes = {v["name"]: {"sid": v["sid"], "used": bool(v["used_by"])} for v in self.volumes() if v["sid"] == sid}
        nets = [s["network"] for s in self.segments() if s.get("sid") == sid]
        return {"images": images, "containers": containers, "volumes": volumes, "networks": sorted(nets)}

    def has_image(self, pin: ImagePin) -> bool:
        alias = image_alias(pin)
        got = self._json("image", "list", "--format", "json")
        for im in got if isinstance(got, list) else []:
            if any(isinstance(a, dict) and a.get("name") == alias for a in im.get("aliases") or []):
                return True
        return False

    def state(self, container: str) -> dict | None:
        inst = self._instance(container)
        return self._part_row(inst) if inst is not None else None

    def segments(self) -> list[dict]:
        got = self._json("network", "list", "--format", "json")
        nets = [n for n in (got if isinstance(got, list) else []) if isinstance(n, dict)
                and dict(n.get("config") or {}).get("user.liminal") == "segment"]
        if not nets:
            return []
        instances = self._instances()
        rows = []
        for n in nets:
            cfg, bridge = dict(n.get("config") or {}), str(n.get("name") or "")
            network = cfg.get("user.liminal.network", bridge)
            address = str(cfg.get("ipv4.address") or "")
            try:
                iface = ipaddress.ip_interface(address)
                subnet, gateway = str(iface.network), str(iface.ip)
            except ValueError:
                subnet, gateway = "", ""
            members = []
            for inst in instances:
                for dname, d in self._devices(inst).items():
                    if d.get("type") == "nic" and d.get("network") == bridge:
                        icfg = self._config(inst)
                        name = str(inst.get("name") or "")
                        members.append({"sid": icfg.get("user.liminal.service", ""),
                                        "part": "" if name.endswith("-previous") else icfg.get("user.liminal.part", ""),
                                        "container": name, "mac": icfg.get(f"volatile.{dname}.hwaddr", ""),
                                        "ipv4": str(d.get("ipv4.address") or ""), "assigned": "reserved"})
            rows.append({"segment": network, "network": network, "kind": "incus-bridge", "bridge": bridge,
                         "sid": cfg.get("user.liminal.service", ""), "platform": cfg.get("user.liminal.platform", ""),
                         "subnets": [subnet] if subnet else [], "gateways": [gateway] if gateway else [],
                         "trusted": [], "assigns": "reserved", "names": "dnsmasq:incus", "members": members})
        return rows

    def logs(self, container: str, lines: int) -> list[str]:
        code, out, _ = self.run("console", container, "--show-log", check=False)
        if code != 0:
            return []
        return [x for x in _text(out).splitlines() if x.strip()][-int(lines):]

    def read(self, container: str, path: str, most: int) -> bytes:
        volume, inside = self._volume_at(container, path)
        _, out, _ = self.run("storage", "volume", "file", "pull", self.pool, f"{volume}/{inside}", "-", binary=True)
        return bytes(out or b"")[:most]

    def here(self, port: int) -> str:
        return f"127.0.0.1:{port}"

    def lan(self, port: int, protocol: str = "tcp") -> tuple[str, str]:
        return self.lan_path, self.lan_words or "a proxy device listens on every address of this machine"

    # -- what it changes, inside an approved plan's work alone ------------------------------------------------------------
    def pull(self, pin: ImagePin, line, cancelled) -> str:
        if self._streamer is None:
            raise Refusal("this target's pulls are not streamed: no image is fetched")
        registry, repo = registry_of(pin.ref)
        remote = remote_name(registry)
        known = self._json("remote", "list", "--format", "json")
        if not (isinstance(known, dict) and remote in known):
            self.run("remote", "add", remote, f"https://{registry}", "--protocol=oci", timeout=CHANGE_S)
            line(f"the OCI remote {remote} added ({registry})")
        argv = [*self.prefix, "image", "copy", f"{remote}:{repo}@{pin.digest}", "local:", "--alias", image_alias(pin)]
        try:
            code, errors = self._streamer(argv, PULL_S, line, cancelled)
        except Exception:
            if cancelled():
                from liminal_libs.machines.plans import Cancelled

                raise Cancelled(f"the copy of {pin.ref} was cancelled: its child ended") from None
            raise
        if code != 0:
            raise IncusFailed((errors or f"incus image copy {pin.ref} failed ({code})").strip().splitlines()[-1])
        if not self.has_image(pin):
            raise IncusFailed(f"{pin.ref} was copied, and no image is aliased {image_alias(pin)}")
        return pin.digest  # asked for by its digest: skopeo checks the manifest against it as it copies

    def make_volume(self, name: str, labels: Mapping) -> bool:
        return self.make_volume_for(name, labels, None)

    def make_volume_for(self, name: str, labels: Mapping, volume: C.Volume | None) -> bool:
        """0.4.1 (liminal-libs 1.95.0): the custom volume made for the spec's volume, owned inside by its owner uid
        (initial.uid, initial.gid), or found from before (the same service's alone)."""
        found = next((v for v in self._custom() if v.get("name") == name), None)
        if found is not None:
            had = dict(found.get("config") or {}).get("user.liminal.service", "")
            if had != labels.get("liminal.service"):
                raise Refusal(f"a volume named {name} is in {self.pool} and is not {labels.get('liminal.service')}'s: "
                              "nothing is made over it")
            return False
        owner = volume.owner if volume is not None else None
        extra = [f"initial.uid={owner}", f"initial.gid={owner}"] if owner is not None else []
        self.run("storage", "volume", "create", self.pool, name, *[f"{k}={v}" for k, v in user_keys(labels).items()],
                 *extra, timeout=CHANGE_S)
        return True

    def make_network(self, name: str, grant: Mapping, labels: Mapping) -> bool:
        bridge = bridge_name(name)
        got = self._json("network", "list", "--format", "json")
        if any(isinstance(n, dict) and n.get("name") == bridge for n in got or []):
            return False
        subnet, gateway = str(grant.get("subnet") or ""), str(grant.get("gateway") or "")
        if not subnet or not gateway:
            raise Refusal(f"{name}: no subnet was handed out for it")
        net = ipaddress.ip_network(subnet)
        base = gateway.rsplit(".", 1)[0]
        keys = user_keys({**labels, "liminal.network": name})
        self.run("network", "create", bridge, "--type=bridge", f"ipv4.address={gateway}/{net.prefixlen}", "ipv4.nat=true",
                 f"ipv4.dhcp.ranges={base}.128-{base}.254", "ipv6.address=none",
                 *[f"{k}={v}" for k, v in keys.items()], timeout=CHANGE_S)
        return True

    def create(self, c: Create) -> None:
        """The instance made, not started: its whole configuration on standard input (a secret's value with it, never
        on a command line), boot.autostart=false said outright."""
        part = c.part
        labels = dict(c.labels)
        if "liminal.spec" in labels:
            labels["liminal.spec"] = labelled_spec(labels["liminal.spec"])
        config = {"boot.autostart": "false", "limits.memory": str(part.memory), **user_keys(labels),
                  "user.liminal.image": part.image.ref, "user.liminal.digest": part.image.digest}
        if part.cpus:
            config["limits.cpu"] = str(max(1, math.ceil(part.cpus)))
        if c.address:
            config["user.liminal.address"] = c.address
        for k, v in {**dict(c.env), **dict(c.secret_env)}.items():
            config[f"environment.{k}"] = str(v)
        devices: dict[str, dict] = {}
        if c.network:
            devices["eth0"] = {"type": "nic", "name": "eth0", "network": bridge_name(c.network),
                               **({"ipv4.address": c.address} if c.address else {})}
        for volume, path in c.volumes:
            devices[volume] = {"type": "disk", "pool": self.pool, "source": volume, "path": path}
        for p in c.ports:
            for i, listen in enumerate(proxy_listens(p.protocol, p.host, p.lan)):
                devices[f"port-{p.name}-{i}"] = {"type": "proxy", "listen": listen,
                                                 "connect": f"{p.protocol}:127.0.0.1:{p.container}"}
        body = json.dumps({"config": config, "devices": devices}, sort_keys=True).encode("utf-8")
        self.run("init", image_alias(part.image), c.name, stdin=body, timeout=CHANGE_S)

    def seed(self, container: str, seeds, line) -> None:
        """Each seed into its custom volume before the first start, from standard input, owned by the volume's owner
        where one is named."""
        for path, seed, owner in seeds:
            volume, inside = self._volume_at(container, path)
            ids = ["--uid", str(owner), "--gid", str(owner)] if owner is not None else []
            self.run("storage", "volume", "file", "push", "--create-dirs", "--mode", f"{seed.mode:04o}", *ids, "-",
                     self.pool, f"{volume}/{inside}", stdin=seed.text.encode("utf-8"), timeout=CHANGE_S)

    def start(self, container: str) -> None:
        self.run("start", container, timeout=CHANGE_S)

    def stop(self, container: str, seconds: int) -> None:
        code, _, _ = self.run("stop", container, "--timeout", str(int(seconds)), timeout=float(seconds) + CHANGE_S,
                              check=False)
        if code != 0 and self._state_of(self._instance(container) or {}) != "exited":
            self.run("stop", container, "--force", timeout=CHANGE_S)

    def rename(self, container: str, new: str) -> None:
        """Renamed; the one kept as …-previous lets its pinned address go (Incus holds one address to one NIC of a
        network), and takes it back when named back."""
        inst = self._instance(container)
        self.run("rename", container, new, timeout=CHANGE_S)
        local = dict((inst or {}).get("devices") or {})
        if "eth0" not in local:
            return
        if new.endswith("-previous") and local["eth0"].get("ipv4.address"):
            self.run("config", "device", "unset", new, "eth0", "ipv4.address", timeout=CHANGE_S)
        elif container.endswith("-previous") and not new.endswith("-previous"):
            address = self._config(inst).get("user.liminal.address", "")
            if address:
                self.run("config", "device", "set", new, "eth0", f"ipv4.address={address}", timeout=CHANGE_S)

    def remove(self, container: str) -> None:
        self.run("delete", container, timeout=CHANGE_S)  # its custom volumes are never deleted with it

    def drop_volumes(self, names) -> None:
        for n in names:
            self.run("storage", "volume", "delete", self.pool, n, timeout=CHANGE_S)

    def drop_network(self, name: str) -> None:
        self.run("network", "delete", bridge_name(name), timeout=CHANGE_S)

    def _snapshots(self, volume: str) -> list[str]:
        got = self._json("storage", "volume", "snapshot", "list", self.pool, volume, "--format", "json")
        names = [str(s.get("name") or "").rsplit("/", 1)[-1] for s in got or [] if isinstance(s, dict)]
        return sorted(n for n in names if n.startswith(SNAPSHOT))

    def backup_kind(self, part) -> str:
        """0.4.1 (liminal-libs 1.95.0): an update's backup is Incus' own snapshot of each custom volume, whatever the
        image holds (no tar needed)."""
        return "snapshot"

    def backup(self, volume: str, pinned: str, into: Path, line, cancelled) -> str:
        """A snapshot of the custom volume (cheap on zfs and btrfs, a whole copy on dir), named after the backup
        folder's stamp: its name answered (0.4.1), which the base keeps in <volume>.snapshot.json for the rollback; the
        volume's older snapshots of Liminal's past the last BACKUPS_KEPT deleted. Nothing is written to `into`."""
        snap = SNAPSHOT + Path(into).parent.name
        self.run("storage", "volume", "snapshot", "create", self.pool, volume, snap, timeout=BACKUP_S)
        line(f"{volume}: the snapshot {snap} taken in {self.pool}")
        for old in self._snapshots(volume)[:-C.BACKUPS_KEPT]:
            self.run("storage", "volume", "snapshot", "delete", self.pool, volume, old, timeout=CHANGE_S)
            line(f"{volume}: the older snapshot {old} deleted")
        return snap

    def restore(self, volume: str, pinned: str, source: Path, line, cancelled) -> str:
        """The snapshot a backup's record names put back: the base's <volume>.snapshot.json (0.4.1), or a 0.4.0 backup's
        own record (kind incus-snapshot), each of this volume alone and of a snapshot of Liminal's."""
        try:
            record = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refusal(f"{source} is no record of a snapshot of {volume}") from None
        if not isinstance(record, dict):
            raise Refusal(f"{source} is no record of a snapshot of {volume}")
        if record.get("kind") == "snapshot":
            snap = str(record.get("said") or "")
        elif record.get("kind") == "incus-snapshot" and record.get("pool") == self.pool:
            snap = str(record.get("snapshot") or "")
        else:
            raise Refusal(f"{source} records another volume's snapshot")
        if record.get("volume") != volume:
            raise Refusal(f"{source} records another volume's snapshot")
        if not snap.startswith(SNAPSHOT) or not re.fullmatch(r"[A-Za-z0-9._-]{1,63}", snap):
            raise Refusal(f"{source} names no snapshot of Liminal's ({snap[:40]!r})")
        self.run("storage", "volume", "snapshot", "restore", self.pool, volume, snap, timeout=BACKUP_S)
        return f"the snapshot {snap}"

    def kept_secret_words(self, spec: ServiceSpec) -> str:
        """0.4.1 (liminal-libs 1.95.0): who can read a secret an instance is handed."""
        names = ", ".join(f"{s.variable} (as {s.env})" for s in spec.secrets())
        return (f"{names}: handed to the instance in its configuration, on standard input; the Incus daemon keeps it in "
                "its database, which anyone in incus-admin (or root) can read (incus config show)")


# -- lxd-docker ----------------------------------------------------------------------------------------------------------
class OciDocker(DockerRuntime):
    """Docker inside liminal-oci (decision 15 a): DockerRuntime exactly, through `incus exec liminal-oci -- docker`
    (LXD's `lxc exec`), its values on standard input; each service volume a custom volume attached to liminal-oci and
    bound into the container; each published port a proxy device from this machine to liminal-oci."""

    mechanism = "lxd-docker"

    def __init__(self, client, runner: Callable, streamer: Callable | None = None, *, pool: str, version: str = "",
                 arch: str = "amd64", lan=("direct", ""), where_data: str = "", initial: bool = False):
        self.client = [str(a) for a in client]
        self._raw = runner
        self.pool = str(pool)
        # 0.4.1: whether the daemon gives a custom volume its owner as it makes it (the API extension
        # storage_volumes_initial_values: initial.uid); otherwise the volume is chowned once inside liminal-oci
        self.initial = bool(initial)
        DockerRuntime.__init__(self, [*self.client, "exec", OCI, EXEC_MODE, "--", "docker"], through_stdin(runner),
                               streamer, version=version, arch=arch, wsl=False, trusted=("eth0",), lan=lan,
                               where_data=where_data or f"in custom volumes of the storage pool {pool}, bound into {OCI}")

    def incus(self, *args, timeout: float = READ_S, check: bool = True, stdin: bytes | None = None):
        argv = [*self.client, *[str(a) for a in args]]
        code, out, err = self._raw(argv, timeout, stdin=stdin, env=None, binary=False, out=None)
        if check and code != 0:
            words = _text(err).strip() or _text(out).strip()
            raise IncusFailed(words.splitlines()[-1] if words else f"{args[0]} failed ({code})")
        return code, out, err

    def _oci_devices(self) -> dict:
        got = _sorted_json(self.incus("list", "--format", "json")[1])
        inst = next((i for i in got or [] if isinstance(i, dict) and i.get("name") == OCI), None)
        if inst is None:
            raise Refusal(f"{OCI} is not there: Set up for services… on the LXD page makes it")
        return dict(inst.get("devices") or {})

    def fit(self, spec: ServiceSpec | None) -> Fit:
        fit = DockerRuntime.fit(self, spec)
        parts = dict(fit.parts)
        if spec is not None:
            parts.setdefault("ports", Aspect("exact"))
            if parts["ports"].fit == "exact":
                parts["ports"] = Aspect("exact", f"a proxy device per port, from this machine to {OCI}")
            parts["volumes"] = Aspect("exact", self.where_data)
        return Fit.judged(parts)

    def make_volume(self, name: str, labels: Mapping) -> bool:
        return self.make_volume_for(name, labels, None)

    def make_volume_for(self, name: str, labels: Mapping, volume: C.Volume | None) -> bool:
        """0.4.1 (liminal-libs 1.95.0): the custom volume made for the spec's volume (initial.uid/initial.gid from its
        owner, where the daemon takes them), attached to liminal-oci and bound into a Docker volume; one found from
        before, or made where the daemon takes no initial owner, is chowned once inside."""
        found = self._inspect("volume", name)
        if found:
            had = (found[0].get("Labels") or {}).get("liminal.service", "")
            if had != labels.get("liminal.service"):
                raise Refusal(f"a volume named {name} is there and is not {labels.get('liminal.service')}'s: "
                              "nothing is made over it")
            return False
        owner = volume.owner if volume is not None else None
        owned = False
        got = _sorted_json(self.incus("storage", "volume", "list", self.pool, "--format", "json")[1])
        if not any(isinstance(v, dict) and v.get("name") == name and v.get("type") == "custom" for v in got or []):
            extra = [f"initial.uid={owner}", f"initial.gid={owner}"] if owner is not None and self.initial else []
            self.incus("storage", "volume", "create", self.pool, name,
                       *[f"{k}={v}" for k, v in user_keys(labels).items()], *extra, timeout=CHANGE_S)
            owned = bool(extra)
        inside = f"{OCI_MOUNTS}/{name}"
        if name not in self._oci_devices():
            self.incus("config", "device", "add", OCI, name, "disk", f"pool={self.pool}", f"source={name}",
                       f"path={inside}", timeout=CHANGE_S)
        if owner is not None and not owned:  # made writable for the image's user once, inside
            self.incus("exec", OCI, EXEC_MODE, "--", "chown", f"{int(owner)}:{int(owner)}", inside, timeout=CHANGE_S)
        args = ["volume", "create", "--driver", "local", "--opt", "type=none", "--opt", "o=bind", "--opt",
                f"device={inside}"]
        for k, v in labels.items():
            args += ["--label", f"{k}={v}"]
        self.run(*args, name, timeout=CHANGE_S)
        return True

    def drop_volumes(self, names) -> None:
        DockerRuntime.drop_volumes(self, names)
        devices = self._oci_devices() if names else {}
        for n in names:
            if n in devices:
                self.incus("config", "device", "remove", OCI, n, timeout=CHANGE_S)
            self.incus("storage", "volume", "delete", self.pool, n, timeout=CHANGE_S)

    def create(self, c: Create) -> None:
        DockerRuntime.create(self, c)
        self._proxies(str(c.labels.get("liminal.service") or ""), c.ports)

    def _proxies(self, sid: str, ports) -> None:
        """Each published port's proxy devices on liminal-oci, listening as the port is open (every address) or not
        (this machine's loopback), connecting to the port Docker publishes inside."""
        devices = self._oci_devices()
        for p in ports:
            listens = proxy_listens(p.protocol, p.host, p.lan)
            for i in range(2):
                dev = f"liminal-{_flat(sid)}-{p.name}-{i}"
                want = listens[i] if i < len(listens) else None
                have = devices.get(dev)
                if have is not None and (want is None or have.get("listen") != want):
                    self.incus("config", "device", "remove", OCI, dev, timeout=CHANGE_S)
                    have = None
                if want is not None and have is None:
                    self.incus("config", "device", "add", OCI, dev, "proxy", f"listen={want}",
                               f"connect={p.protocol}:127.0.0.1:{p.host}", timeout=CHANGE_S)

    def oci_nic(self) -> dict:
        """liminal-oci's own place on Incus' bridge, as the daemon says it now: {"address", "bridge", "mac"} ("" each
        when it is not said: liminal-oci stopped, or no IPv4 address yet). Incus addresses it (its own DHCP)."""
        got = _sorted_json(self.incus("list", "--format", "json")[1])
        inst = next((i for i in got or [] if isinstance(i, dict) and i.get("name") == OCI), None) or {}
        devices = dict(inst.get("expanded_devices") or inst.get("devices") or {})
        nic, d = next(((k, v) for k, v in devices.items() if isinstance(v, dict) and v.get("type") == "nic"), ("", {}))
        state = ((inst.get("state") or {}).get("network") or {}).get(str(d.get("name") or nic) if nic else "") or {}
        address = next((str(a.get("address") or "") for a in state.get("addresses") or []
                        if isinstance(a, dict) and a.get("family") == "inet" and a.get("scope") == "global"), "")
        return {"address": address, "bridge": str(d.get("network") or ""), "mac": str(state.get("hwaddr") or "")}

    def network_want(self, spec: ServiceSpec | None, sid: str) -> dict:
        """0.4.1 (liminal-libs 1.95.0, L2): a service's Docker network sits behind liminal-oci's own address on Incus'
        bridge: the firewall's route to it goes through that address. Nothing said while it has none."""
        address = self.oci_nic()["address"]
        return {"behind": address} if address else {}

    def kept_secret_words(self, spec: ServiceSpec) -> str:
        """0.4.1 (liminal-libs 1.95.0): who can read a secret a container inside liminal-oci is handed."""
        names = ", ".join(f"{s.variable} (as {s.env})" for s in spec.secrets())
        return (f"{names}: handed to the container inside {OCI} by environment alone, crossing into it on standard "
                f"input; Docker there keeps it in the container's settings, which anyone who may run commands in {OCI} "
                "can read (docker inspect)")

    def segments(self) -> list[dict]:
        """Docker's networks inside liminal-oci, said as nested (the networking design's §5.7); and since 0.4.1 (L6)
        Incus' bridge liminal-oci sits on, liminal-oci a found member of it (no service's: sid "", Incus addresses it)."""
        rows = [{**s, "kind": "nested", "names": "docker"} for s in DockerRuntime.segments(self)]
        nic = self.oci_nic()
        if not nic["bridge"]:
            return rows
        nets = _sorted_json(self.incus("network", "list", "--format", "json")[1])
        n = next((x for x in nets or [] if isinstance(x, dict) and x.get("name") == nic["bridge"]), None) or {}
        try:
            iface = ipaddress.ip_interface(str(dict(n.get("config") or {}).get("ipv4.address") or ""))
            subnet, gateway = str(iface.network), str(iface.ip)
        except ValueError:
            subnet, gateway = "", ""
        rows.append({"segment": nic["bridge"], "network": nic["bridge"], "kind": "incus-bridge", "bridge": nic["bridge"],
                     "sid": "", "platform": "", "shared": True, "subnets": [subnet] if subnet else [],
                     "gateways": [gateway] if gateway else [], "trusted": [], "assigns": "incus",
                     "names": "dnsmasq:incus",
                     "members": [{"sid": "", "part": "", "container": OCI, "mac": nic["mac"], "ipv4": nic["address"],
                                  "assigned": "incus"}]})
        return rows

    def remove(self, container: str) -> None:
        """The container removed (never a volume); once no container of its service is left, its proxy devices go."""
        found = self._inspect("container", container)
        sid = str((((found[0] if found else {}).get("Config") or {}).get("Labels") or {}).get("liminal.service") or "")
        DockerRuntime.remove(self, container)
        if not sid or any(r["sid"] == sid for r in self.hosted()):
            return
        lead = f"liminal-{_flat(sid)}-"
        for dev, d in self._oci_devices().items():
            if dev.startswith(lead) and d.get("type") == "proxy":
                self.incus("config", "device", "remove", OCI, dev, timeout=CHANGE_S)


# -- what the plugin reads for its targets -------------------------------------------------------------------------------
def services_script(client: str) -> str:
    """One script (as the rights read): the server's API extensions and environment, the default profile (its pool),
    whether skopeo and umoci are here, liminal-oci and, while it runs, the Docker inside it. Starts nothing."""
    c = shlex.quote(client)
    return "\n".join([
        "LC_ALL=C; export LC_ALL", f"echo '{MARK}'",
        f"echo '== server'; {c} query /1.0 2>&1; echo \"== rc $?\"",
        f"echo '== profile'; {c} query /1.0/profiles/default 2>&1; echo \"== rc $?\"",
        "echo '== tools'; for t in skopeo umoci; do if command -v \"$t\" >/dev/null 2>&1; then echo \"$t\"; fi; done",
        f"o=$({c} query /1.0/instances/{OCI} 2>&1); r=$?; echo '== oci'; echo \"$o\"; echo \"== rc $r\"",
        f"if [ $r = 0 ] && printf '%s' \"$o\" | grep -q '\"status\": *\"Running\"'; then echo '== docker'; "
        f"{c} exec {OCI} {EXEC_MODE} -- docker version --format '{{{{json .}}}}' 2>&1; echo \"== rc $?\"; fi",
        "echo '== end'", ""])


def parse_services(text: str) -> dict:
    """The services script's answer: {"server": {...}, "pool": "", "tools": set, "oci": {...} | None, "oci_error",
    "docker": {...} | None, "docker_error", "problem"}."""
    from .system import sections

    if MARK not in (text or ""):
        return {"problem": "the services' read was not understood"}
    got: dict = {"server": {}, "pool": "", "tools": set(), "oci": None, "oci_error": "", "docker": None,
                 "docker_error": "", "problem": ""}
    for head, _rest, body in sections(text):
        lines = list(body)
        joined = "\n".join(lines).strip()
        if head == "rc":
            continue
        value = _sorted_json(joined) if joined.startswith(("{", "[")) else None
        if head == "server":
            if isinstance(value, dict):
                got["server"] = value
            else:
                got["problem"] = (lines[-1].strip() if lines else "the server did not answer") or "the server did not answer"
        elif head == "profile" and isinstance(value, dict):
            root = (value.get("devices") or {}).get("root") or {}
            got["pool"] = str(root.get("pool") or "")
        elif head == "tools":
            got["tools"] = {ln.strip() for ln in lines if ln.strip()}
        elif head == "oci":
            if isinstance(value, dict):
                got["oci"] = value
            else:
                got["oci_error"] = joined.splitlines()[-1] if joined else "not there"
        elif head == "docker":
            if isinstance(value, dict):
                got["docker"] = value
            else:
                got["docker_error"] = joined.splitlines()[-1] if joined else "it did not answer"
    return got

