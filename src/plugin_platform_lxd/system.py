"""What this Linux machine has for LXC / LXD, read with one script as the platform's own user (no root, nothing
started): who the platform runs as and its groups (the process's, and the group database's: a group given after this
login is not the process's yet), the init system (systemd, OpenRC, or SysV scripts through the service command, told
as the library's services scan tells them), the tools (incus, LXD's lxc, classic LXC's lxc-ls; /snap/bin's too) and
their versions, LXD's snap, the distribution (/etc/os-release), the daemons running (incusd, lxd) and the sockets they
listen on, and each service of theirs the init system knows (systemd's units; OpenRC's /etc/init.d scripts, their
state and runlevels; the SysV scripts' status). `parse` reads that into a `Probe`; `install_words` says how this
distribution installs Incus, LXD or LXC."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from liminal_libs.machines.services import INIT_WORDS

MARK = "== liminal-lxd-probe 1"  # the probe's first line: anything else was not this script
TOOLS = ("incus", "lxc", "lxc-ls")
DAEMONS = ("incusd", "lxd")  # the daemons' process names
SOCKETS = {"incus": ("/var/lib/incus/unix.socket", "/run/incus/unix.socket"),
           "lxd": ("/var/snap/lxd/common/lxd/unix.socket", "/var/lib/lxd/unix.socket")}
PROCESS = {"incus": "incusd", "lxd": "lxd"}
GROUPS = ("incus-admin", "incus", "lxd")
UP = ("running", "listening", "on", "starting")
STARTABLE = ("stopped", "failed", "inactive")


@dataclass(frozen=True)
class Known:
    """A service of Incus, LXD or LXC as an init system names it: `inits` the ones that do (systemd; openrc and
    sysvinit share /etc/init.d's names), `backend` whose it is (* for LXCFS, which all three use)."""
    name: str
    backend: str
    inits: tuple[str, ...]
    role: str
    daemon: bool = False  # the daemon itself
    socket: bool = False  # its socket: systemd starts the daemon when a client connects


SYSTEMD = ("systemd",)
RC = ("openrc", "sysvinit")
KNOWN = (
    Known("incus.service", "incus", SYSTEMD, "the Incus daemon", daemon=True),
    Known("incus.socket", "incus", SYSTEMD, "its socket: systemd starts the daemon when a client connects", socket=True),
    Known("incus-startup.service", "incus", SYSTEMD, "starts the instances set to start at boot, stops them at shutdown"),
    Known("incus-user.socket", "incus", SYSTEMD, "the incus group's API (incus-user): each member's own project"),
    Known("incus-lxcfs.service", "incus", SYSTEMD, "LXCFS: the containers' own /proc figures"),
    Known("snap.lxd.daemon.service", "lxd", SYSTEMD, "the LXD daemon (the snap)", daemon=True),
    Known("snap.lxd.daemon.unix.socket", "lxd", SYSTEMD, "its socket: systemd starts the daemon when a client connects", socket=True),
    Known("lxd.service", "lxd", SYSTEMD, "the LXD daemon", daemon=True),
    Known("lxd.socket", "lxd", SYSTEMD, "its socket: systemd starts the daemon when a client connects", socket=True),
    Known("lxd-containers.service", "lxd", SYSTEMD, "starts the instances set to start at boot"),
    Known("lxcfs.service", "*", SYSTEMD, "LXCFS: the containers' own /proc figures"),
    Known("lxc.service", "lxc", SYSTEMD, "starts the containers set to start at boot (lxc.start.auto)"),
    Known("lxc-net.service", "lxc", SYSTEMD, "LXC's bridge (lxcbr0) and its DHCP"),
    Known("lxc-monitord.service", "lxc", SYSTEMD, "LXC's state monitor"),
    Known("incus", "incus", RC, "the Incus daemon", daemon=True),
    Known("incusd", "incus", RC, "the Incus daemon (Alpine's name for it)", daemon=True),
    Known("incus-user", "incus", RC, "the incus group's API (incus-user): each member's own project"),
    Known("lxd", "lxd", RC, "the LXD daemon", daemon=True),
    Known("lxcfs", "*", RC, "LXCFS: the containers' own /proc figures"),
    Known("lxc-net", "lxc", RC, "LXC's bridge (lxcbr0) and its DHCP"),
    Known("lxc-containers", "lxc", ("openrc",), "starts the containers set to start at boot (lxc.start.auto)"),
    Known("lxc", "lxc", ("sysvinit",), "starts the containers set to start at boot (lxc.start.auto)"),
)
SYSTEMD_UNITS = tuple(k.name for k in KNOWN if "systemd" in k.inits)
RC_NAMES = tuple(dict.fromkeys(k.name for k in KNOWN if "systemd" not in k.inits))

# POSIX sh, as the platform's user: nothing in it needs root or starts anything
SCRIPT = r"""LC_ALL=C; export LC_ALL
echo '@MARK@'
echo "== user $(id -un 2>/dev/null) $(id -u 2>/dev/null)"
echo "== groups $(id -nG 2>/dev/null)"
echo "== member $(id -nG "$(id -un 2>/dev/null)" 2>/dev/null)"
p1=$(cat /proc/1/comm 2>/dev/null || echo unknown)
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then init=systemd
elif command -v rc-status >/dev/null 2>&1 && { [ -d /run/openrc ] || [ "$p1" = openrc-init ]; }; then init=openrc
elif command -v service >/dev/null 2>&1; then init=sysvinit
else init=none; fi
echo "== init $init $p1"
for t in @TOOLS@; do
  p=$(command -v "$t" 2>/dev/null)
  if [ -z "$p" ] && [ -x "/snap/bin/$t" ]; then p="/snap/bin/$t"; fi
  if [ -n "$p" ]; then
    echo "== tool $t $p"
    echo "== version $t $("$p" --version 2>/dev/null | head -n 1)"
  fi
done
if command -v snap >/dev/null 2>&1; then
  s=$(snap list lxd 2>/dev/null | sed -n 2p)
  if [ -n "$s" ]; then echo "== snap $s"; fi
fi
echo '== os'
if [ -r /etc/os-release ]; then grep -E '^(ID|ID_LIKE|NAME|PRETTY_NAME|VERSION_ID)=' /etc/os-release; fi
for d in @DAEMONS@; do
  if pgrep -x "$d" >/dev/null 2>&1 || ps -eo comm= 2>/dev/null | grep -qx "$d"; then echo "== daemon $d"; fi
done
for s in @SOCKETS@; do
  if [ -S "$s" ]; then echo "== socket $s"; fi
done
case $init in
systemd)
  echo '== units'
  systemctl show -p Id,LoadState,ActiveState,SubState,UnitFileState,Description @UNITS@ 2>/dev/null
  ;;
openrc)
  echo '== openrc'
  for n in @RC@; do
    [ -e "/etc/init.d/$n" ] || continue
    st=stopped
    for d in started starting stopping inactive failed; do
      if [ -e "/run/openrc/$d/$n" ]; then st=$d; fi
    done
    lv=
    for r in /etc/runlevels/*; do
      if [ -e "$r/$n" ]; then lv="$lv${lv:+,}${r##*/}"; fi
    done
    echo "$n $st ${lv:--}"
  done
  ;;
sysvinit)
  echo '== sysv'
  for n in @RC@; do
    [ -e "/etc/init.d/$n" ] || continue
    service "$n" status >/dev/null 2>&1
    echo "$n $?"
  done
  ;;
esac
echo '== end'
"""
PROBE = (SCRIPT.replace("@MARK@", MARK).replace("@TOOLS@", " ".join(TOOLS)).replace("@DAEMONS@", " ".join(DAEMONS))
         .replace("@SOCKETS@", " ".join(p for paths in SOCKETS.values() for p in paths))
         .replace("@UNITS@", " ".join(SYSTEMD_UNITS)).replace("@RC@", " ".join(RC_NAMES)))

OPENRC_STATES = {"started": "running", "starting": "starting", "stopping": "stopping", "inactive": "inactive",
                 "failed": "failed", "stopped": "stopped"}


@dataclass(frozen=True)
class Unit:
    """A service as its init system says it is: `state` running | listening (a socket) | on (done, staying on) |
    starting | stopping | failed | stopped | inactive | masked | unknown; `boot` systemd's unit file state (enabled,
    disabled, static...) or OpenRC's runlevels."""
    name: str
    state: str
    boot: str = ""
    description: str = ""
    known: Known | None = None

    @property
    def up(self) -> bool:
        return self.state in UP

    @property
    def startable(self) -> bool:
        return self.state in STARTABLE

    @property
    def role(self) -> str:
        return self.known.role if self.known else self.description


@dataclass(frozen=True)
class Probe:
    """What the probe found; `problem` when its answer was not the probe's."""
    user: str = ""
    uid: int = -1
    groups: frozenset = frozenset()  # the platform process's own
    member: frozenset = frozenset()  # the user's in the group database (a new one is the process's after a new login)
    init: str = "unknown"
    pid1: str = ""
    tools: dict = field(default_factory=dict)  # tool -> its path
    versions: dict = field(default_factory=dict)  # tool -> what --version said
    snap: tuple = ()  # LXD's snap: (version, revision, tracking)
    os: dict = field(default_factory=dict)  # /etc/os-release's ID, ID_LIKE, NAME, PRETTY_NAME, VERSION_ID
    daemons: frozenset = frozenset()  # the daemons running: incusd, lxd
    sockets: tuple = ()  # the daemons' sockets there
    units: dict = field(default_factory=dict)  # name -> Unit, those the init system has
    problem: str = ""

    @property
    def root(self) -> bool:
        return self.uid == 0

    def unit(self, name: str) -> Unit | None:
        return self.units.get(name)


def sections(text: str) -> list[tuple[str, str, list[str]]]:
    """`== head rest` headed sections of a script's answer: (head, rest, the lines beneath)."""
    out: list[tuple[str, str, list[str]]] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r")
        if line.startswith("== "):
            head, _, rest = line[3:].partition(" ")
            out.append((head, rest.strip(), []))
        elif out:
            out[-1][2].append(line)
    return out


def _systemd_state(load: str, active: str, sub: str) -> str:
    if load in ("not-found", "") or load.startswith("error") or load == "bad-setting":
        return "absent"
    if load == "masked":
        return "masked"
    if active == "active":
        return {"running": "running", "listening": "listening", "exited": "on"}.get(sub, "on")
    if active in ("activating", "reloading"):
        return "starting"
    if active == "deactivating":
        return "stopping"
    if active == "failed":
        return "failed"
    return "stopped"


def systemd_units(lines: list[str], known=KNOWN) -> dict[str, Unit]:
    """`systemctl show -p Id,...` blocks (an empty line between units): the units loaded, by name."""
    by = {k.name: k for k in known if "systemd" in k.inits}
    out: dict[str, Unit] = {}
    block: dict[str, str] = {}

    def flush() -> None:
        name = block.get("Id", "")
        if name:
            state = _systemd_state(block.get("LoadState", ""), block.get("ActiveState", ""), block.get("SubState", ""))
            if state != "absent":
                out[name] = Unit(name, state, block.get("UnitFileState", ""), block.get("Description", ""), by.get(name))
        block.clear()

    for line in lines:
        if not line.strip():
            flush()
        elif "=" in line:
            k, _, v = line.partition("=")
            if k == "Id" and block.get("Id"):
                flush()
            block[k.strip()] = v.strip()
    flush()
    return out


def parse(text: str, known=KNOWN) -> Probe:
    """The probe's answer as a Probe; one that is not the probe's is a problem, never a guess."""
    if MARK not in (text or ""):
        return Probe(problem="the probe's answer was not understood")
    found: dict = {"tools": {}, "versions": {}, "os": {}, "units": {}}
    daemons, sockets = set(), []
    init_known = {}
    for head, rest, body in sections(text):
        if head == "user":
            name, _, uid = rest.partition(" ")
            found["user"] = name
            found["uid"] = int(uid) if uid.strip().isdecimal() else -1
        elif head in ("groups", "member"):
            found[head] = frozenset(rest.split())
        elif head == "init":
            init, _, pid1 = rest.partition(" ")
            found["init"], found["pid1"] = init or "unknown", pid1.strip()
        elif head == "tool":
            tool, _, path = rest.partition(" ")
            if path.strip():
                found["tools"][tool] = path.strip()
        elif head == "version":
            tool, _, said = rest.partition(" ")
            found["versions"][tool] = said.strip()
        elif head == "snap":
            words = rest.split()
            if len(words) >= 4 and words[0] == "lxd":
                found["snap"] = (words[1], words[2], words[3].rstrip("…"))
        elif head == "os":
            for line in body:
                k, _, v = line.partition("=")
                if k.strip() and v:
                    found["os"][k.strip()] = v.strip().strip('"').strip("'")
        elif head == "daemon":
            daemons.add(rest)
        elif head == "socket":
            sockets.append(rest)
        elif head == "units":
            found["units"].update(systemd_units(body, known))
        elif head in ("openrc", "sysv"):
            init_known = {k.name: k for k in known if ("openrc" if head == "openrc" else "sysvinit") in k.inits}
            for line in body:
                words = line.split()
                if len(words) < 2 or words[0] not in init_known:
                    continue
                if head == "openrc":
                    state, boot = OPENRC_STATES.get(words[1], "unknown"), (words[2] if len(words) > 2 and words[2] != "-" else "")
                else:
                    state, boot = {"0": "running", "3": "stopped"}.get(words[1], "unknown"), ""
                found["units"][words[0]] = Unit(words[0], state, boot, "", init_known[words[0]])
    return Probe(user=found.get("user", ""), uid=found.get("uid", -1), groups=found.get("groups", frozenset()),
                 member=found.get("member", frozenset()), init=found.get("init", "unknown"), pid1=found.get("pid1", ""),
                 tools=found["tools"], versions=found["versions"], snap=found.get("snap", ()), os=found["os"],
                 daemons=frozenset(daemons), sockets=tuple(sockets), units=found["units"])


def init_words(p: Probe) -> str:
    """The init system in words, with PID 1 when it says more (openrc-init, a container's)."""
    words = INIT_WORDS.get(p.init, p.init)
    return words + (f" (PID 1: {p.pid1})" if p.pid1 and p.pid1 not in ("systemd", "unknown", "") else "")


# -- the distribution, and how it installs these ---------------------------------------------------------------
FAMILIES = {"gentoo": "gentoo", "funtoo": "gentoo", "calculate": "gentoo", "alpine": "alpine", "nixos": "nixos",
            "void": "void", "ubuntu": "ubuntu", "debian": "debian", "fedora": "fedora", "rhel": "rhel", "centos": "rhel",
            "rocky": "rhel", "almalinux": "rhel", "arch": "arch", "opensuse": "opensuse", "suse": "opensuse",
            "opensuse-tumbleweed": "opensuse", "opensuse-leap": "opensuse", "sles": "opensuse"}


def family(os_release: dict) -> str:
    """The distribution's family (gentoo, debian, ubuntu, fedora, rhel, arch, opensuse, alpine, nixos, void): its own ID
    first, then those it is like, nearest first (Linux Mint: ubuntu; Rocky: rhel; Manjaro: arch)."""
    for word in [os_release.get("ID", ""), *os_release.get("ID_LIKE", "").split()]:
        if word.lower() in FAMILIES:
            return FAMILIES[word.lower()]
    return ""


def distribution(p: Probe) -> str:
    return p.os.get("PRETTY_NAME") or p.os.get("NAME") or "a Linux distribution /etc/os-release does not name"


INSTALL = {
    "gentoo": ("emerge --ask app-containers/incus (or app-containers/lxd; classic LXC: app-containers/lxc), then under "
               "OpenRC rc-update add incus default and rc-service incus start, under systemd systemctl enable --now incus"),
    "ubuntu": "sudo apt install incus (Ubuntu 24.04 and later; for virtual machines also qemu-system), or LXD: sudo snap install lxd",
    "debian": "sudo apt install incus (Debian 13; on Debian 12 Zabbly's packages, or LXD: sudo apt install lxd)",
    "fedora": "sudo dnf install incus",
    "rhel": "Incus from the neelc/incus COPR with EPEL (sudo dnf copr enable neelc/incus; sudo dnf install incus incus-tools), or LXD: sudo snap install lxd",
    "arch": "sudo pacman -S incus",
    "opensuse": "sudo zypper install incus",
    "alpine": "apk add incus incus-client (incus-vm for virtual machines), then rc-update add incusd and rc-service incusd start",
    "nixos": "virtualisation.incus.enable = true; in configuration.nix",
    "void": "xbps-install incus incus-client, then ln -s /etc/sv/incus /var/service",
}
INSTALL_ELSE = "Incus (linuxcontainers.org/incus/docs/main/installing) or LXD (sudo snap install lxd)"


def install_words(p: Probe) -> str:
    """How this distribution installs Incus, LXD or LXC, and what follows (the group, the first initialization)."""
    how = INSTALL.get(family(p.os), INSTALL_ELSE)
    user = p.user or "your user"
    return (f"neither Incus, LXD nor LXC is installed: {how}; then add {user} to incus-admin (lxd for LXD: "
            f"sudo usermod -aG incus-admin {user}), log in again, and set it up once with incus admin init (lxd init)")


_VERSION = re.compile(r"^\d+(\.\d+)+")


def looks_like_version(text: str) -> bool:
    return bool(_VERSION.match((text or "").strip()))
