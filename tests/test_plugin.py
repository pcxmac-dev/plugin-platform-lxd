"""LXC / LXD over recorded outputs (nothing runs here): Incus, LXD's snap and classic LXC read and changed through a
stand-in for this machine's commands; systemd and OpenRC (Gentoo's incus, Alpine's incusd), the rights (incus-admin,
lxd, root, sudo -n, none), an idle daemon never woken by a read, a stopped one started only when asked; how each
distribution installs it; only on a native Linux machine: on Windows, macOS or in a WSL distribution nothing runs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from liminal_libs.machines import linux as linux_mod
from liminal_libs.machines.linux import Shell
from liminal_libs.machines.plugin import CommandError

from plugin_platform_lxd import MACHINE, LxdError, LxdPlugin, Refusal, inventory, system, why_not_native
from plugin_platform_lxd.__main__ import main

RECORDED = Path(__file__).parent / "recorded"
INCUS = {"instances": "incus-list.json", "images": "incus-image-list.json", "profiles": "incus-profile-list.json",
         "storage": "incus-storage-list.json", "networks": "incus-network-list.json"}
OS = {
    "fedora": 'ID=fedora\nNAME="Fedora Linux"\nPRETTY_NAME="Fedora Linux 41 (Server Edition)"\nVERSION_ID=41',
    "ubuntu": 'NAME="Ubuntu"\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04.1 LTS"\nID=ubuntu\nID_LIKE=debian',
    "gentoo": 'NAME=Gentoo\nID=gentoo\nPRETTY_NAME="Gentoo Linux"\nVERSION_ID=2.17',
    "alpine": 'NAME="Alpine Linux"\nID=alpine\nVERSION_ID=3.20.3\nPRETTY_NAME="Alpine Linux v3.20"',
    "debian": 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nNAME="Debian GNU/Linux"\nVERSION_ID="13"\nID=debian',
    "arch": 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch',
    "mint": 'NAME="Linux Mint"\nVERSION_ID="22"\nID=linuxmint\nID_LIKE="ubuntu debian"\nPRETTY_NAME="Linux Mint 22"',
    "rocky": 'NAME="Rocky Linux"\nVERSION_ID="9.4"\nID="rocky"\nID_LIKE="rhel centos fedora"\nPRETTY_NAME="Rocky Linux 9.4 (Blue Onyx)"',
    "nixos": 'NAME=NixOS\nID=nixos\nPRETTY_NAME="NixOS 24.05 (Uakari)"',
}
TOOLS = {"incus": {"incus": ("/usr/bin/incus", "6.0.4")}, "lxd": {"lxc": ("/snap/bin/lxc", "5.21.3 LTS")},
         "lxc": {"lxc-ls": ("/usr/bin/lxc-ls", "6.0.4")}, "none": {}}
RUNNING = ("loaded", "active", "running", "enabled")
UNITS = {  # systemd's units as `systemctl show` says them: (LoadState, ActiveState, SubState, UnitFileState)
    "incus": {"incus.service": RUNNING, "incus.socket": ("loaded", "active", "running", "enabled"),
              "incus-startup.service": ("loaded", "active", "exited", "enabled"),
              "incus-user.socket": ("loaded", "active", "listening", "enabled")},
    "incus-idle": {"incus.service": ("loaded", "inactive", "dead", "indirect"), "incus.socket": ("loaded", "active", "listening", "enabled"),
                   "incus-startup.service": ("loaded", "active", "exited", "enabled")},
    "incus-stopped": {"incus.service": ("loaded", "inactive", "dead", "disabled"), "incus.socket": ("loaded", "inactive", "dead", "disabled")},
    "lxd": {"snap.lxd.daemon.service": ("loaded", "active", "running", "static"),
            "snap.lxd.daemon.unix.socket": ("loaded", "active", "running", "enabled")},
    "lxc": {"lxc.service": ("loaded", "active", "exited", "enabled"), "lxc-net.service": ("loaded", "active", "exited", "enabled"),
            "lxc-monitord.service": ("loaded", "inactive", "dead", "static")},
}
DESCRIPTIONS = {"incus.service": "Incus - Daemon", "incus.socket": "Incus - Daemon (unix socket)",
                "incus-startup.service": "Incus - Startup check", "incus-user.socket": "Incus - Daemon (user unix socket)",
                "snap.lxd.daemon.service": "Service for snap application lxd.daemon",
                "snap.lxd.daemon.unix.socket": "Socket unix for snap application lxd.daemon"}


def recorded(name: str) -> str:
    return (RECORDED / name).read_text(encoding="utf-8")


def compact(name: str) -> str:
    """A recorded JSON as the client prints it: one line."""
    return json.dumps(json.loads(recorded(name)), separators=(",", ":"))


class FakeLinux:
    """Answers a Linux machine's commands for Incus, LXD's snap or classic LXC from recorded outputs: the probe (as the
    user), the read (as root when through sudo -n), and the changes, which it records."""

    def __init__(self, backend="incus", os="fedora", init="systemd", units="incus", rc=(), user="ciara", uid=1000,
                 groups=("ciara", "wheel", "incus-admin"), member=None, sudo=True, daemons=("incusd",),
                 sockets=("/var/lib/incus/unix.socket",), errors=None, also=()):
        self.backend, self.os, self.init, self.user, self.uid = backend, os, init, user, uid
        self.units = UNITS.get(units, {}) if isinstance(units, str) else dict(units)
        self.rc = list(rc)  # OpenRC's lines: "incus started default"
        self.groups, self.member = tuple(groups), tuple(member if member is not None else groups)
        self.sudo, self.daemons, self.sockets = sudo, tuple(daemons), tuple(sockets)
        self.errors = dict(errors or {})  # a part of the read failing: part -> the client's words
        self.tools = {**TOOLS[backend], **{k: v for b in also for k, v in TOOLS[b].items()}}
        self.calls: list[list[str]] = []
        self.reads: list[str] = []
        self.changes: list[list[str]] = []
        self.refuse: dict[str, str] = {}  # a change refused: its command's word -> stderr

    def probe(self) -> str:
        lines = [system.MARK, f"== user {self.user} {self.uid}", f"== groups {' '.join(self.groups)}",
                 f"== member {' '.join(self.member)}", f"== init {self.init} {'systemd' if self.init == 'systemd' else 'init'}"]
        for tool, (path, version) in self.tools.items():
            lines += [f"== tool {tool} {path}", f"== version {tool} {version}"]
        if "lxc" in self.tools and self.tools["lxc"][0].startswith("/snap/"):
            lines.append("== snap lxd   5.21.3-c5ae129  33110  5.21/stable  canonical✓  -")
        lines += ["== os", OS[self.os]]
        lines += [f"== daemon {d}" for d in self.daemons] + [f"== socket {s}" for s in self.sockets]
        if self.init == "systemd":
            lines.append("== units")
            for name in system.SYSTEMD_UNITS:
                load, active, sub, file_state = self.units.get(name, ("not-found", "inactive", "dead", ""))
                lines += [f"Id={name}", f"Description={DESCRIPTIONS.get(name, name)}", f"LoadState={load}",
                          f"ActiveState={active}", f"SubState={sub}", f"UnitFileState={file_state}", ""]
        elif self.init == "openrc":
            lines += ["== openrc", *self.rc]
        else:
            lines += ["== sysv", *self.rc]
        return "\n".join(lines + ["== end"]) + "\n"

    def read(self) -> str:
        files = INCUS if self.backend == "incus" else {**INCUS, "instances": "lxd-list.json"}
        parts = [inventory.MARK]
        for part, _ in inventory.PARTS:
            if part in self.errors:
                parts += [f"== {part}", f"Error: {self.errors[part]}", "== rc 1"]
            else:
                parts += [f"== {part}", compact(files[part]), "== rc 0"]
        return "\n".join(parts + ["== end"]) + "\n"

    def lxc_read(self) -> str:
        if "containers" in self.errors:
            return f"{inventory.LXC_MARK}\n== containers\n{self.errors['containers']}\n== rc 1\n== end\n"
        return (f"{inventory.LXC_MARK}\n== containers\n{recorded('lxc-ls-fancy.txt')}== rc 0\n"
                "== snapshots buildbox\nsnap0 (/var/lib/lxc/buildbox/snaps) 2024:05:02 18:20:11\n"
                "== snapshots legacy\nNo snapshots\n== end\n")

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        root = args[:2] == ["sudo", "-n"]
        cmd = args[2:] if root else args
        if root and not self.sudo:
            return 1, "", "sudo: a password is required\n"
        if cmd == ["true"]:
            return 0, "", ""
        if cmd[:2] == ["sh", "-c"]:
            script = cmd[2]
            if system.MARK in script:
                assert not root, "the probe never asks for root"
                return 0, self.probe(), ""
            if inventory.MARK in script or inventory.LXC_MARK in script:
                self.reads.append(("sudo -n " if root else "") + "sh -c " + script.splitlines()[2])
                return 0, (self.read() if inventory.MARK in script else self.lxc_read()), ""
            raise AssertionError(f"an unknown script: {script[:80]!r}")
        self.changes.append(cmd)
        word = cmd[1] if len(cmd) > 1 else cmd[0]
        if word in self.refuse:
            return 1, "", self.refuse[word]
        return 0, "", ""


@pytest.fixture(autouse=True)
def user(monkeypatch):
    """The platform runs as a user (uid 1000) here; a test that wants root says so."""
    monkeypatch.setattr(linux_mod.os, "geteuid", lambda: 1000, raising=False)


def plugin(fake) -> LxdPlugin:
    """The plugin on a native Linux machine (the test's word), its commands through the stand-in."""
    return LxdPlugin(shell=Shell("local", runner=fake), native="")


def ask_page(p, st: dict, button: dict, accepted=()):
    """A hook asked as the platform's page asks it (platform.js: openForm, argsOf): the button's form, its values over
    the fields' own, each field in order (an empty one as null, an accept field true only once accepted)."""
    form = next(f for f in st["forms"] if f["id"] == button["form"])
    args = []
    for f in form["fields"]:
        if f["type"] == "accept":
            args.append(f["name"] in accepted)
            continue
        v = button.get("values", {}).get(f["name"], f.get("value"))
        if v is None and f["type"] == "choice":
            v = f["choices"][0]["value"]
        args.append(None if v in (None, "") else v)
    return p.ask(form["hook"], *args)


def table(st: dict, title: str) -> dict:
    return next(t for t in st["tables"] if t["title"] == title)


def texts(row: list) -> list:
    return [c["text"] if isinstance(c, dict) and "text" in c else c.get("title") if isinstance(c, dict) else c for c in row]


def test_the_plugin_its_hooks_and_its_probe():
    assert (MACHINE.name, MACHINE.title, MACHINE.ICON, MACHINE.KIND, MACHINE.STEPS, MACHINE.options) == (
        "lxd", "LXC / LXD", "lxd", "machine", (), ())
    assert set(LxdPlugin(native="").interface()["hooks"]) == {"present", "status", "run", "power", "snapshot", "restore",
                                                              "delete", "service", "wake"}
    assert "setup" not in MACHINE.interface()["hooks"] and "instances" not in MACHINE.interface()["hooks"]  # nothing to set up
    for word in ("incus", "lxc-ls", "incus.service", "snap.lxd.daemon.service", "incusd", "lxc-net", "/run/openrc/",
                 "/etc/runlevels/", "pgrep -x", "/snap/bin/", "LC_ALL=C"):
        assert word in system.PROBE
    assert "sudo" not in system.PROBE and not re.search(r"start", system.PROBE)  # asks nothing of root, starts nothing


def test_incus_under_systemd_read_as_a_member_of_incus_admin():
    fake = FakeLinux()
    st = plugin(fake).status()
    assert (st["present"], st["valid"], st["why"]) == (True, True, "") and "problem" not in st
    assert st["summary"] == "Incus 6.0.4: 4 instances, 2 running"
    assert st["rights"] == "ciara is in incus-admin: commands as ciara, no sudo"
    assert (st["software"], st["client"], st["daemon"], st["initSystem"], st["distribution"]) == (
        "Incus 6.0.4", "/usr/bin/incus", "running (incus.service)", "systemd", "Fedora Linux 41 (Server Edition)")
    assert st["instances"] == "4: 2 running · 3 containers, 1 virtual machine" and st["snapshots"] == "2"
    assert (st["projects"], st["images"], st["storagePools"], st["networks"]) == (
        "default, dev", "2, 390 MB", "default (zfs)", "incusbr0 (10.120.17.1/24 NAT)")
    instances = table(st, "Instances")
    assert [texts(r)[:6] for r in instances["rows"]] == [
        ["db", "virtual machine", "stopped", "–", "Ubuntu noble amd64 (20240510_07:42)", "default, vm-large"],
        ["old-test", "container", "stopped", "–", "Alpine 3.20 amd64 (20240511_13:00)", "default"],
        ["web", "container", "running", "10.120.17.23 (eth0)", "Debian bookworm amd64 (20240512_05:24)", "default"],
        ["ci-runner", "container", "running", "172.17.0.1 (docker0), 10.120.17.88 (eth0)", "Debian bookworm", "default, ci"]]
    web = instances["rows"][2]
    assert web[0] == {"text": "web", "sub": "nginx in front of the apps"}
    assert web[6] == {"form": "power", "title": "Stop…", "values": {"name": "web", "action": "stop"}, "label": "Start, stop or restart web"}
    assert web[7]["form"] == "snapshot" and web[8] == {"text": "–", "sub": "stop it first"}  # deleted only once stopped
    ci = instances["rows"][3]
    assert ci[0] == {"text": "ci-runner", "sub": "project dev · GitLab runner"} and ci[6]["values"] == {"name": "ci-runner", "project": "dev", "action": "stop"}
    assert instances["rows"][0][8] == {"form": "delete", "title": "Delete…", "values": {"name": "db"}, "label": "Delete db"}
    snaps = table(st, "Snapshots")
    assert [texts(r)[:5] for r in snaps["rows"]] == [["web", "snap0", "2024-05-12 09:14 UTC", "no", "never"],
                                                      ["web", "before-upgrade", "2024-06-01 18:02 UTC", "no", "2024-07-01 18:02 UTC"]]
    assert snaps["rows"][0][5]["values"] == {"name": "web", "snapshot": "snap0"}
    assert [texts(r) for r in table(st, "Images")["rows"]] == [
        ["3f1e9c7d5b3a", "virtual machine", "x86_64", "298 MB", "2024-05-20 16:30 UTC"],
        ["debian/12", "container", "x86_64", "92.3 MB", "2024-05-12 09:10 UTC"]]
    assert [texts(r) for r in table(st, "Profiles")["rows"]] == [
        ["default", "eth0 (nic on incusbr0), root (disk on default)", "4"], ["vm-large", "root (disk on default)", "1"]]
    assert [texts(r) for r in table(st, "Storage pools")["rows"]] == [["default", "zfs", "/var/lib/incus/disks/default.img", "created", "6"]]
    networks = table(st, "Networks")
    assert [texts(r) for r in networks["rows"]] == [["incusbr0", "bridge", "10.120.17.1/24 NAT", "fd42:7c1a:9b2e:51e1::1/64 NAT", "3"]]
    assert networks["note"] == "the machine's own interfaces, not Incus's: docker0, enp3s0, lo"
    daemons = table(st, "Daemons")
    assert [texts(r)[:3] for r in daemons["rows"]] == [["incus.service", "running", "enabled"], ["incus.socket", "running", "enabled"],
                                                       ["incus-startup.service", "on", "enabled"], ["incus-user.socket", "listening", "enabled"]]
    assert all(r[4] == "" for r in daemons["rows"])  # nothing to start
    assert {f["id"] for f in st["forms"]} == {"power", "snapshot", "restore", "delete", "service", "wake"} and st["actions"] == []
    accept = {f["id"]: [x["name"] for x in f["fields"] if x["type"] == "accept"] for f in st["forms"]}
    assert accept == {"power": [], "snapshot": [], "restore": ["accept_restore"], "delete": ["accept_delete"], "service": [], "wake": []}
    assert fake.reads == ["sh -c echo '== instances'; /usr/bin/incus list --format json --all-projects 2>&1; echo \"== rc $?\""]
    assert not any(c[0] == "sudo" for c in fake.calls) and fake.changes == []  # as the user; nothing changed by a read


def test_instances_changed_only_through_their_hooks_restoring_and_deleting_asked_twice():
    fake = FakeLinux()
    p = plugin(fake)
    assert p.power("web", "stop")["said"] == "web: stopped"
    p.power("old-test", "start")
    p.power("ci-runner", "restart", "dev")
    assert fake.changes == [["/usr/bin/incus", "stop", "web", "--timeout", "60"], ["/usr/bin/incus", "start", "old-test"],
                            ["/usr/bin/incus", "restart", "ci-runner", "--timeout", "60", "--project", "dev"]]
    fake.changes.clear()
    p.snapshot("web", "nightly")
    p.snapshot("web", "")
    assert fake.changes == [["/usr/bin/incus", "snapshot", "create", "web", "nightly"], ["/usr/bin/incus", "snapshot", "create", "web"]]
    fake.changes.clear()
    with pytest.raises(Refusal) as asked:
        p.restore("web", "snap0")
    assert asked.value.accept == "accept_restore" and "lost for good" in str(asked.value) and fake.changes == []
    assert p.restore("web", "snap0", accept_restore=True)["said"] == "web: restored to snap0"
    assert fake.changes == [["/usr/bin/incus", "snapshot", "restore", "web", "snap0"]]
    with pytest.raises(Refusal, match="no snapshot named nope: snap0, before-upgrade"):
        p.restore("web", "nope", accept_restore=True)
    fake.changes.clear()
    with pytest.raises(Refusal, match="web is running: stop it first"):
        p.delete("web", accept_delete=True)
    with pytest.raises(Refusal) as asked:
        p.delete("db")
    assert asked.value.accept == "accept_delete" and "0 snapshots for good" in str(asked.value)
    assert p.delete("db", accept_delete="true")["said"] == "db: deleted"  # the page sends true
    assert fake.changes == [["/usr/bin/incus", "delete", "db"]]
    with pytest.raises(Refusal, match="no instance named web in the project dev"):
        p.delete("web", accept_delete=True, project="dev")
    for bad in (lambda: p.power("web", "destroy"), lambda: p.power("--force", "stop"), lambda: p.snapshot("web", "a/b"),
                lambda: p.power("web; rm -rf /", "stop"), lambda: p.delete("web", project="-x")):
        with pytest.raises(Refusal):
            bad()
    fake.refuse["start"] = "Error: Failed to start device \"eth0\": Parent device \"incusbr0\" doesn't exist\n"
    with pytest.raises(LxdError, match='^Failed to start device "eth0"'):
        p.power("old-test", "start")


def test_the_page_asks_each_hook_with_its_forms_fields_in_order():
    """What the platform's page sends: the row's button fills its form, the fields go in order; a refusal naming an
    accept field is asked a second time, then sent again with it true (Incus's forms and classic LXC's, which have no
    project field, alike)."""
    fake = FakeLinux()
    p = plugin(fake)
    st = p.status()
    rows = {texts(r)[0]: r for r in table(st, "Instances")["rows"]}
    ask_page(p, st, rows["ci-runner"][6])  # Stop… in the project dev
    ask_page(p, st, {**rows["web"][7], "values": {**rows["web"][7]["values"], "snapshot": "nightly"}})
    with pytest.raises(Refusal) as asked:
        ask_page(p, st, rows["db"][8])  # Delete…: refused, naming what accepts it
    assert asked.value.accept == "accept_delete"
    ask_page(p, st, rows["db"][8], accepted=("accept_delete",))
    restore = table(st, "Snapshots")["rows"][0][5]
    with pytest.raises(Refusal) as asked:
        ask_page(p, st, restore)
    ask_page(p, st, restore, accepted=(asked.value.accept,))
    start = {"form": "service", "values": {"unit": "incus.service", "action": "start"}}
    assert ask_page(p, st, start)["said"] == "incus.service is running already"
    assert fake.changes == [["/usr/bin/incus", "stop", "ci-runner", "--timeout", "60", "--project", "dev"],
                            ["/usr/bin/incus", "snapshot", "create", "web", "nightly"], ["/usr/bin/incus", "delete", "db"],
                            ["/usr/bin/incus", "snapshot", "restore", "web", "snap0"]]
    assert ask_page(p, st, {"form": "wake"})["said"].startswith("Incus read")
    lxc = FakeLinux("lxc", os="debian", units="lxc", groups=("ciara", "sudo"), daemons=(), sockets=())
    q = plugin(lxc)
    st = q.status()
    rows = {texts(r)[0]: r for r in table(st, "Containers")["rows"]}
    with pytest.raises(Refusal) as asked:
        ask_page(q, st, rows["legacy"][7])
    ask_page(q, st, rows["legacy"][7], accepted=(asked.value.accept,))
    ask_page(q, st, rows["buildbox"][5])  # Stop…
    ask_page(q, st, rows["buildbox"][6])  # Snapshot…: no name field, classic LXC names it
    with pytest.raises(Refusal, match="restores a stopped container"):
        ask_page(q, st, table(st, "Snapshots")["rows"][0][3], accepted=("accept_restore",))
    assert lxc.changes == [["lxc-destroy", "-n", "legacy", "-s"], ["lxc-stop", "-n", "buildbox", "-t", "60"],
                           ["lxc-snapshot", "-n", "buildbox"]]


def test_lxd_from_the_snap_on_ubuntu_with_the_lxd_group():
    fake = FakeLinux("lxd", os="ubuntu", units="lxd", groups=("ciara", "adm", "sudo", "lxd"), daemons=("lxd",),
                     sockets=("/var/snap/lxd/common/lxd/unix.socket",))
    p = plugin(fake)
    st = p.status()
    assert st["software"] == "LXD 5.21.3 LTS (the snap, 5.21/stable)" and st["rights"] == "ciara is in lxd: commands as ciara, no sudo"
    assert st["summary"] == "LXD 5.21.3 LTS (the snap, 5.21/stable): 1 instance, 1 running"
    assert st["daemon"] == "running (snap.lxd.daemon.service)" and st["distribution"] == "Ubuntu 24.04.1 LTS"
    assert texts(table(st, "Instances")["rows"][0])[:4] == ["noble", "container", "running", "10.33.41.12 (eth0)"]
    assert [r[0] for r in table(st, "Daemons")["rows"]] == ["snap.lxd.daemon.service", "snap.lxd.daemon.unix.socket"]
    p.snapshot("noble", "pre-release")
    p.restore("noble", "snap0", accept_restore=True)
    p.power("noble", "stop")
    assert fake.changes == [["/snap/bin/lxc", "snapshot", "noble", "pre-release"], ["/snap/bin/lxc", "restore", "noble", "snap0"],
                            ["/snap/bin/lxc", "stop", "noble", "--timeout", "60"]]
    both = FakeLinux("incus", also=("lxd",))  # lxd-to-incus left LXD behind: Incus is driven, LXD named
    assert plugin(both).status()["alsoInstalled"] == "LXD 5.21.3 LTS (the snap, 5.21/stable)"


def test_an_idle_daemon_is_read_only_when_asked_since_connecting_starts_it():
    fake = FakeLinux(units="incus-idle", daemons=(), sockets=("/var/lib/incus/unix.socket",))
    p = plugin(fake)
    st = p.status()
    assert fake.reads == [] and st["valid"] and "problem" not in st  # its socket was never touched
    assert st["summary"] == "Incus 6.0.4: the daemon is idle (Read now… reads it)"
    assert st["daemon"].startswith("idle: incus.socket listens") and st["actions"] == [
        {"form": "wake", "title": "Read now…", "note": "connects to the idle daemon, which starts it"}]
    assert table(st, "Instances")["empty"].startswith("Not read: the Incus daemon is idle (incus.socket listens)")
    assert [t["title"] for t in st["tables"]] == ["Instances", "Snapshots", "Daemons"]
    assert p.wake()["said"] == "Incus read: 4 instances, 2 running" and len(fake.reads) == 1
    st = p.status()
    assert len(fake.reads) == 2 and st["summary"] == "Incus 6.0.4: 4 instances, 2 running" and st["actions"] == []
    p.detach()  # the platform let it go: idle again until asked
    p.status()
    assert len(fake.reads) == 2


def test_a_stopped_daemon_is_started_only_when_asked_under_systemd_openrc_and_alpine(monkeypatch):
    fake = FakeLinux(units="incus-stopped", daemons=(), sockets=())
    p = plugin(fake)
    st = p.status()
    assert st["valid"] and st["problem"] == "the Incus daemon is stopped (incus.service): Start… under Daemons starts it"
    assert fake.reads == [] and st["summary"] == "Incus 6.0.4: the daemon is stopped"
    rows = {r[0]: r for r in table(st, "Daemons")["rows"]}
    assert rows["incus.service"][4] == {"form": "service", "title": "Start…", "values": {"unit": "incus.service", "action": "start"},
                                        "label": "Start incus.service"}
    service = next(f for f in st["forms"] if f["id"] == "service")
    assert service["confirm"]["words"].startswith("systemctl start {unit}, as root")
    assert p.service("incus.service", "start")["said"] == "incus.service started (systemctl start incus.service)"
    assert fake.calls[-1] == ["sudo", "-n", "systemctl", "start", "incus.service"]  # root through sudo -n
    for bad, words in ((("incus.service", "stop"), "never stopped"), (("sshd.service", "start"), "not one of Incus's services"),
                       (("lxd.service", "start"), "not one of Incus's services")):
        with pytest.raises(Refusal, match=words):
            p.service(*bad)
    nosudo = FakeLinux(units="incus-stopped", daemons=(), sockets=(), sudo=False)
    with pytest.raises(LxdError, match="or start it yourself: sudo systemctl start incus.service") as e:
        plugin(nosudo).service("incus.service", "start")
    assert e.value.denied
    gentoo = FakeLinux(os="gentoo", init="openrc", rc=["incus stopped -", "incus-user stopped -", "lxcfs started default"],
                       daemons=(), sockets=())
    st = plugin(gentoo).status()
    assert st["initSystem"] == "OpenRC (PID 1: init)" and st["distribution"] == "Gentoo Linux"
    assert [texts(r)[:3] for r in table(st, "Daemons")["rows"]] == [["incus", "stopped", "–"], ["incus-user", "stopped", "–"],
                                                                    ["lxcfs", "running", "default"]]
    monkeypatch.setattr(linux_mod.os, "geteuid", lambda: 0, raising=False)  # the platform as root: no sudo
    plugin(gentoo).service("incus", "start")
    assert gentoo.calls[-1] == ["rc-service", "incus", "start"]
    alpine = FakeLinux(os="alpine", init="openrc", rc=["incusd stopped -"], daemons=(), sockets=(), uid=0, user="root", groups=("root",))
    st = plugin(alpine).status()
    assert st["daemon"] == "stopped (incusd): Start… under Daemons starts it" and st["rights"] == "the platform runs as root"
    plugin(alpine).service("incusd", "start")
    assert alpine.calls[-1] == ["rc-service", "incusd", "start"]
    running = FakeLinux(os="gentoo", init="openrc", rc=["incus started default"], daemons=("incusd",))
    assert plugin(running).status()["daemon"] == "running (incus)"
    devuan = FakeLinux(init="sysvinit", rc=["incus 3"], daemons=(), sockets=())
    plugin(devuan).service("incus", "start")
    assert devuan.calls[-1] == ["service", "incus", "start"]


def test_the_rights_root_sudo_the_incus_group_and_none(monkeypatch):
    sudo = FakeLinux(groups=("ciara", "wheel"))
    st = plugin(sudo).status()
    assert st["valid"] and st["rights"] == "ciara is not in incus-admin: commands as root through sudo -n"
    assert sudo.calls[1] == ["sudo", "-n", "true"] and sudo.reads[0].startswith("sudo -n sh -c")
    kept = FakeLinux(groups=("ciara", "wheel"), sudo=False)
    again = plugin(kept)
    again.status()
    again.status()
    assert kept.calls.count(["sudo", "-n", "true"]) == 1  # sudo's answer kept: a refusal is not logged by sudo every read
    plugin(sudo).power("web", "stop")
    assert sudo.calls[-1] == ["sudo", "-n", "/usr/bin/incus", "stop", "web", "--timeout", "60"]
    none = FakeLinux(groups=("ciara", "wheel"), sudo=False)
    st = plugin(none).status()
    assert (st["present"], st["valid"]) == (True, False) and none.reads == []
    assert st["why"] == ("ciara is neither root nor in incus-admin, and sudo asks for a password: add ciara to incus-admin "
                         "(sudo usermod -aG incus-admin ciara) and log in again, so the platform runs with the group; or run "
                         "the platform as root; or let sudo run commands for ciara without a password")
    with pytest.raises(LxdError, match="sudo usermod -aG incus-admin ciara") as e:
        plugin(none).power("web", "stop")
    assert e.value.denied and none.changes == []
    later = FakeLinux(groups=("ciara", "wheel"), member=("ciara", "wheel", "incus-admin"), sudo=False)
    assert plugin(later).status()["why"].startswith("ciara was added to incus-admin after this login, and sudo asks for a password: log in again")
    restricted = FakeLinux(groups=("ciara", "incus"), sudo=False)
    st = plugin(restricted).status()
    assert st["valid"] and st["rights"].startswith("ciara is in incus, not incus-admin: ciara's own project only (incus-user)")
    assert "--all-projects" not in restricted.reads[0] and table(st, "Instances")["note"].startswith("your own project")
    denied = FakeLinux(errors={p: "You don't have the needed permissions to talk to the incus daemon (socket path: /var/lib/incus/unix.socket)"
                               for p, _ in inventory.PARTS})
    st = plugin(denied).status()
    assert not st["valid"] and st["why"].startswith("You don't have the needed permissions") and "usermod -aG incus-admin" in st["why"]
    monkeypatch.setattr(linux_mod.os, "geteuid", lambda: 0, raising=False)
    root = FakeLinux(uid=0, user="root", groups=("root",))
    assert plugin(root).status()["rights"] == "the platform runs as root" and not any(c[0] == "sudo" for c in root.calls)


def test_classic_lxc_through_its_tools_as_root():
    fake = FakeLinux("lxc", os="debian", units="lxc", groups=("ciara", "sudo"), daemons=(), sockets=())
    p = plugin(fake)
    st = p.status()
    assert st["summary"] == "LXC 6.0.4 (classic LXC's tools): 2 containers, 1 running" and st["valid"]
    assert (st["daemon"], st["client"], st["rights"]) == ("none: classic LXC's tools work on their own", "/usr/bin/lxc-*",
                                                           "commands as root through sudo -n")
    rows = table(st, "Containers")["rows"]
    assert [texts(r)[:5] for r in rows] == [["buildbox", "running", "yes", "10.0.3.114, 172.17.0.1", "no"],
                                            ["legacy", "stopped", "no", "–", "no"]]
    assert [texts(r)[:3] for r in table(st, "Snapshots")["rows"]] == [["buildbox", "snap0", "2024-05-02 18:20"]]
    assert [t["title"] for t in st["tables"]] == ["Containers", "Snapshots", "Daemons"]
    assert [r[0] for r in table(st, "Daemons")["rows"]] == ["lxc-monitord.service", "lxc-net.service", "lxc.service"]
    assert fake.reads == ["sudo -n sh -c echo '== containers'; lxc-ls --fancy 2>&1; echo \"== rc $?\""]  # root's containers
    forms = {f["id"]: f for f in st["forms"]}
    assert [x["name"] for x in forms["snapshot"]["fields"]] == ["name"] and "project" not in [x["name"] for x in forms["power"]["fields"]]
    p.power("legacy", "start")
    p.power("buildbox", "stop")
    p.power("buildbox", "restart")
    p.snapshot("buildbox")
    p.delete("legacy", accept_delete=True)
    assert fake.changes == [["lxc-start", "-n", "legacy"], ["lxc-stop", "-n", "buildbox", "-t", "60"],
                            ["lxc-stop", "-n", "buildbox", "-r", "-t", "60"], ["lxc-snapshot", "-n", "buildbox"],
                            ["lxc-destroy", "-n", "legacy", "-s"]]
    with pytest.raises(Refusal, match="names its snapshots itself"):
        p.snapshot("buildbox", "mine")
    with pytest.raises(Refusal, match="restores a stopped container"):
        p.restore("buildbox", "snap0", accept_restore=True)
    with pytest.raises(Refusal, match="classic LXC's own tools only"):
        p.run("rm", "-rf", "/")
    assert p.run("lxc-info", "-n", "buildbox") == "" and fake.changes[-1] == ["lxc-info", "-n", "buildbox"]


def test_not_installed_says_how_each_distribution_installs_it():
    def why(os):
        st = plugin(FakeLinux("none", os=os, units={}, daemons=(), sockets=())).status()
        assert (st["present"], st["valid"], st["summary"]) == (False, False, "not installed")
        return st["why"]
    assert "emerge --ask app-containers/incus" in why("gentoo") and "rc-update add incus default" in why("gentoo")
    assert "sudo snap install lxd" in why("ubuntu") and "sudo apt install incus" in why("mint")  # Mint is Ubuntu's
    assert "sudo dnf install incus" in why("fedora") and "neelc/incus COPR" in why("rocky")  # Rocky is RHEL's, not Fedora's
    assert "pacman -S incus" in why("arch") and "rc-update add incusd" in why("alpine") and "Debian 13" in why("debian")
    assert "virtualisation.incus.enable" in why("nixos")
    assert why("fedora").endswith("then add ciara to incus-admin (lxd for LXD: sudo usermod -aG incus-admin ciara), log in again, "
                                  "and set it up once with incus admin init (lxd init)")
    assert system.family({"ID": "manjaro", "ID_LIKE": "arch"}) == "arch" and system.family({"ID": "plan9"}) == ""


def test_parsing_the_answers():
    assert inventory.when("2024-05-12T09:14:03.123456789Z") == "2024-05-12 09:14 UTC"
    assert inventory.when("2024-05-12T09:14:03-04:00") == "2024-05-12 09:14 -04:00" and inventory.when("0001-01-01T00:00:00Z") == ""
    assert inventory.words_of(["Error: Instance not found"]) == "Instance not found"
    assert system.parse("something else").problem == "the probe's answer was not understood"
    assert inventory.parse("nothing").problems == ["the read's answer was not understood"]
    warned = (f"{inventory.MARK}\n== instances\nIf this is your first time running Incus on this machine, you should also run: incus admin init\n"
              f"{compact('incus-list.json')}\n== rc 0\n== end\n")
    assert [i.name for i in inventory.parse(warned).instances] == ["db", "old-test", "web", "ci-runner"]  # a warning beside the JSON
    down = inventory.parse(f"{inventory.MARK}\n== instances\nError: The incus daemon doesn't appear to be started (socket path: "
                           f"/var/lib/incus/unix.socket)\n== rc 1\n== end\n")
    assert down.down and not down.denied and down.problems == ["The incus daemon doesn't appear to be started (socket path: /var/lib/incus/unix.socket)"]
    lxd_down = inventory.parse(f'{inventory.MARK}\n== instances\nError: LXD unix socket "/var/snap/lxd/common/lxd/unix.socket" not '
                               f'accessible: permission denied\n== rc 1\n== end\n')
    assert lxd_down.denied
    assert inventory.fancy("") == [] and inventory.parse_lxc(f"{inventory.LXC_MARK}\n== containers\n== rc 0\n== end\n").instances == []
    st = plugin(FakeLinux(errors={"images": "not authorized"})).status()  # one part failing: said, the rest shown
    assert st["problem"].startswith("not authorized") and table(st, "Instances")["rows"] and not st["valid"]
    refused = FakeLinux(groups=("ciara", "wheel"))
    refused.sudo = True
    real = refused.__call__

    def no_sh(args, timeout):  # sudo -n lets true run, not sh: the read is refused, and said
        if args[:4] == ["sudo", "-n", "sh", "-c"]:
            return 1, "", "Sorry, user ciara is not allowed to execute '/bin/sh -c ...' as root on box.\n"
        return real(args, timeout)
    st = LxdPlugin(shell=Shell("local", runner=no_sh), native="").status()
    assert st["present"] and st["problem"].startswith("Sorry, user ciara is not allowed") and table(st, "Instances")["rows"] == []


def test_the_command_line(capsys):
    fake = FakeLinux()
    p = plugin(fake)
    assert main(["status"], machine=p) == 0 and json.loads(capsys.readouterr().out)["summary"] == "Incus 6.0.4: 4 instances, 2 running"
    assert main(["restore", "web", "snap0"], machine=p) == 1
    err = capsys.readouterr().err
    assert err.startswith("refused: restoring snap0 replaces web's disk") and err.rstrip().endswith("(again with --yes to go on)")
    assert fake.changes == []
    assert main(["restore", "web", "snap0", "--yes"], machine=p) == 0 and capsys.readouterr().out == "web: restored to snap0\n"
    assert main(["stop", "ci-runner", "--project", "dev"], machine=p) == 0
    assert main(["delete", "db", "--yes"], machine=p) == 0
    assert main(["snapshot", "web"], machine=p) == 0 and main(["service", "incus.service", "start"], machine=p) == 0
    assert main(["run", "--", "info", "web"], machine=p) == 0
    assert fake.changes == [["/usr/bin/incus", "snapshot", "restore", "web", "snap0"],
                            ["/usr/bin/incus", "stop", "ci-runner", "--timeout", "60", "--project", "dev"],
                            ["/usr/bin/incus", "delete", "db"], ["/usr/bin/incus", "snapshot", "create", "web"],
                            ["/usr/bin/incus", "info", "web"]]  # incus.service runs already: not started again
    capsys.readouterr()
    assert main(["frobnicate"], machine=p) == 2 and main(["run"], machine=p) == 2
    assert main(["start", "--force"], machine=p) == 2  # a flag is not a name


def test_only_a_native_linux_machine_is_driven():
    """The user, 2026-11-08: "the scope of linux administration is only on native linux O/S"; "it's not for administrating
    WSL instances": on Windows, macOS or in a WSL distribution no command runs at all, the status says why with applies
    False (the platform shows "Not for this machine", no setup), and every hook is refused."""
    windows = why_not_native("win32", "10", {})
    assert windows == "LXC / LXD is for a native Linux machine, and this one runs Windows; Liminal's WSL distributions are the WSL plugins'"
    wsl = why_not_native("linux", "6.6.87.2-microsoft-standard-WSL2", {"WSL_DISTRO_NAME": "Liminal-Ubuntu"})
    assert wsl.endswith("not a WSL instance, and this is WSL's Liminal-Ubuntu on Windows")
    mac = why_not_native("darwin", "24.1.0", {})
    assert "this one runs macOS" in mac and why_not_native("linux", "6.11.5-gentoo", {}) == ""
    if sys.platform == "win32":
        assert MACHINE.not_for_here() == windows and not MACHINE.present()  # this PC: asked nothing
    for note in (windows, wsl, mac):
        ran: list = []
        p = LxdPlugin(shell=Shell("local", runner=lambda args, timeout: ran.append(args) or (0, "", "")), native=note)
        st = p.status()
        assert (st["present"], st["valid"], st["applies"], st["summary"], st["why"]) == (False, False, False, "not for this machine", note)
        assert (st["tables"], st["forms"], st["actions"]) == ([], [], []) and not p.present() and p.setup(log=lambda m: None) == []
        for ask in (lambda: p.power("web", "stop"), lambda: p.snapshot("web"), lambda: p.restore("web", "snap0", accept_restore=True),
                    lambda: p.delete("web", accept_delete=True), lambda: p.service("incus.service"), p.wake, lambda: p.run("list")):
            with pytest.raises(CommandError, match="native Linux machine"):
                ask()
        assert ran == []  # nothing ran anywhere
    in_wsl = LxdPlugin(shell=Shell("wsl:Liminal-Ubuntu", runner=lambda args, timeout: (0, "", "")), native="")
    with pytest.raises(LxdError, match="never WSL's Liminal-Ubuntu"):
        in_wsl.power("web", "stop")
