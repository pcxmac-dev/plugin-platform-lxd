"""LXC / LXD for Liminal (the user, 2026-11-08: "LXC/LXD plugins and LIBVIRT plugin for virtualization"; of the Linux
plugins: "make sure its compatable with systemd/openrc ... make sure its compatible with major distributions and gentoo
linux"): Incus (LXD's community fork), LXD or classic LXC, whichever this machine has, on the native Linux machine the
platform runs on and nowhere else (the user: "the scope of linux administration is only on native linux O/S"; "it's
not for administrating WSL instances"): on Windows, on macOS and inside a WSL distribution it runs no command at all
(the library's machines.linux.why_not_native), asks nothing and offers no setup.

Read through the client's JSON (`incus`, or LXD's `lxc`, `--format json`): the instances (containers and virtual
machines: state, IPv4, image, profiles, snapshots), the images, the profiles, the storage pools and the networks;
classic LXC through `lxc-ls --fancy` and `lxc-snapshot -L`. Changed only when asked, through the platform page's
confirmed forms or this command line: an instance started, stopped or restarted, a snapshot taken; a snapshot restored
and an instance deleted, each asked a second time (accept). The daemon's services under systemd, OpenRC or SysV scripts
(incus, Alpine's incusd, lxd, the snap's snap.lxd.daemon, LXCFS, classic LXC's lxc and lxc-net) are shown, and started
only when asked; none is ever stopped. Nothing is started by a read either: a daemon that is idle (systemd's socket
listening, the daemon not running) is read once the user asks (Read now), since connecting starts it.

The commands run as the platform's user when it is in incus-admin (Incus) or lxd (LXD), plainly when the platform is
root, else as root through the library's linux.Shell (sudo -n, never a password asked); when none of that works the
status says how to give the rights. Classic LXC's containers are root's: root, or sudo -n.

    python -m plugin_platform_lxd status [--wake] | wake | start|stop|restart NAME [--project P]
        | snapshot NAME [SNAPSHOT] [--project P] | restore NAME SNAPSHOT [--project P] [--yes]
        | delete NAME [--project P] [--yes] | service UNIT start | run -- ARGS...
"""
from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass

from liminal_libs.machines import linux
from liminal_libs.machines.linux import Shell
from liminal_libs.machines.plugin import CommandError, MachinePlugin

from . import inventory as inv_mod
from .inventory import Inventory, parse as parse_read, parse_lxc, read_script
from .system import (PROBE, PROCESS, SOCKETS, Probe, distribution, init_words, install_words, looks_like_version,
                     parse as parse_probe)

__version__ = "0.1.0"
SUDO_S = 300.0  # sudo -n's answer kept this long: a refusal is logged by sudo each time it is asked
NAMES = {"incus": "Incus", "lxd": "LXD", "lxc": "LXC"}
TOOL = {"incus": "incus", "lxd": "lxc", "lxc": "lxc-ls"}
GROUP = {"incus": "incus-admin", "lxd": "lxd"}  # the group whose members drive the daemon without root
POWER = ("start", "stop", "restart")
DONE = {"start": "started", "stop": "stopped", "restart": "restarted"}
STOP_S = 60  # a clean shutdown waited for this long
TIMEOUTS = {"start": 180.0, "stop": STOP_S + 60.0, "restart": 2 * STOP_S + 60.0}
CHANGE_S = 600.0  # a snapshot, a restore or a delete (a dir pool copies the whole disk)
READ_S = 120.0
SERVICE_S = 120.0
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._-]{0,127}$")
DENIED = ("permission denied", "needed permissions", "not authorized", "a password is required", "forbidden")


class LxdError(CommandError):
    """LXC / LXD refused or failed, in words; `denied`: for want of the rights."""


class Refusal(ValueError):
    """A change not made, and why: nothing ran. `accept`: the argument that, set, makes it anyway (the platform's page
    asks it as a second, explicit confirmation; the command line's --yes)."""

    def __init__(self, message: str, accept: str | None = None):
        super().__init__(message)
        self.accept = accept


def why_not_native(system: str | None = None, release: str | None = None, env=None) -> str:
    """Why the machine the platform runs on is not a native Linux one, in words ("" when it is): the library's check
    (liminal-libs 1.27.0, shared with the Linux administration and firewall plugins)."""
    return linux.why_not_native("LXC / LXD", system, release, env)


def yes(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "yes", "1", "on")


def given(value) -> bool:
    return value is not None and str(value).strip() != ""


def checked(value, what: str) -> str:
    """A name as Incus, LXD and LXC take it (letters, digits, dots, dashes, underscores; never a leading dash)."""
    name = str(value if value is not None else "").strip()
    if not NAME.match(name):
        raise Refusal(f"{name!r} is not {what}: letters, digits, dots, dashes and underscores, at most 63, starting with a letter or digit")
    return name


def project_of(value) -> str | None:
    """A project named (None: the default one)."""
    if not given(value) or str(value).strip() == "default":
        return None
    return checked(value, "a project's name")


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def size(n) -> str:
    if n is None:
        return "–"
    v, i, units = float(n), 0, ("B", "KB", "MB", "GB", "TB")
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{v:.0f} {units[i]}" if i == 0 or v >= 100 else f"{v:.1f} {units[i]}"


def cell(text, sub: str | None = None):
    return {"text": text, "sub": sub} if sub else text


def answer(said: str, out: str = "", ok: bool = True) -> dict:
    """A hook's answer as the platform's page says it: `said`, the command's own lines beneath."""
    return {"said": said, "lines": [line.strip() for line in (out or "").splitlines() if line.strip()][:20], "ok": ok}


def backends(p: Probe) -> list[str]:
    """Incus, LXD (its client is lxc), classic LXC (lxc-ls), in that order: those this machine has."""
    found = []
    if p.tools.get("incus"):
        found.append("incus")
    if p.tools.get("lxc") and looks_like_version(p.versions.get("lxc", "")):
        found.append("lxd")
    if p.tools.get("lxc-ls"):
        found.append("lxc")
    return found


def software(p: Probe, backend: str) -> str:
    words = f"{NAMES[backend]} {p.versions.get(TOOL[backend], '')}".strip()
    if backend == "lxd":
        snap = bool(p.snap) or p.tools.get("lxc", "").startswith("/snap/")
        words += (f" (the snap, {p.snap[2]})" if p.snap else " (the snap)") if snap else " (packages)"
    elif backend == "lxc":
        words += " (classic LXC's tools)"
    return words


def daemon_state(p: Probe, backend: str) -> tuple[str, str]:
    """The daemon's state and what says it: running | idle (its socket listens, the daemon is not running: the first
    client starts it) | stopped | unknown (none of its services known here) | none (classic LXC has no daemon)."""
    if backend == "lxc":
        return "none", ""
    mains = [u for u in p.units.values() if u.known and u.known.backend == backend and u.known.daemon]
    up = [u.name for u in mains if u.state in ("running", "starting")]
    if up or PROCESS[backend] in p.daemons:
        return "running", up[0] if up else PROCESS[backend]
    listening = [u.name for u in p.units.values() if u.known and u.known.backend == backend and u.known.socket and u.state == "listening"]
    listening += [s for s in SOCKETS[backend] if s in p.sockets]
    if listening:
        return "idle", listening[0]
    if mains:
        return "stopped", mains[0].name
    return "unknown", ""


@dataclass(frozen=True)
class Access:
    """How the commands run: `kind` root | group | sudo | restricted (Incus's incus group: the user's own project)
    | none; `as_root` through sudo -n (when the platform is not root); `words` it in words; `how` the rights given."""
    kind: str
    as_root: bool
    words: str
    how: str = ""


def access_of(p: Probe, backend: str, sudo) -> Access:
    """The rights the platform has over `backend` here; `sudo()` asks whether sudo -n works (only when it matters)."""
    user = p.user or "the platform's user"
    group = GROUP.get(backend)
    if group:
        how = (f"add {user} to {group} (sudo usermod -aG {group} {user}) and log in again, so the platform runs with the "
               f"group; or run the platform as root; or let sudo run commands for {user} without a password")
    else:
        how = f"classic LXC's containers are root's: run the platform as root, or let sudo run commands for {user} without a password"
    if p.root:
        return Access("root", False, "the platform runs as root", how)
    if group and group in p.groups:
        return Access("group", False, f"{user} is in {group}: commands as {user}, no sudo", how)
    later = bool(group) and group in p.member  # given after this login: the process has it once started anew
    after = f"; {user} was added to {group} after this login: started from a new login, the platform needs no sudo" if later else ""
    if sudo():
        return Access("sudo", True, (f"{user} is not in {group}: commands as root through sudo -n" if group
                                     else "commands as root through sudo -n") + after, how)
    if backend == "incus" and "incus" in p.groups:
        return Access("restricted", False, f"{user} is in incus, not incus-admin: {user}'s own project only (incus-user); "
                                           f"incus-admin gives the machine's" + after, how)
    if later:
        return Access("none", True, f"{user} was added to {group} after this login, and sudo asks for a password: log in "
                                    "again and start the platform from that login, so it runs with the group", how)
    lead = (f"{user} is neither root nor in {group}, and sudo asks for a password" if group
            else f"{user} is not root, and sudo asks for a password")
    return Access("none", True, f"{lead}: {how}", how)


@dataclass(frozen=True)
class Context:
    """One look at this machine: its probe, the software driven (`backend`, None when none is installed) and the
    others installed, the rights, the daemon's state."""
    shell: Shell
    probe: Probe
    backend: str | None
    others: tuple = ()
    access: Access | None = None
    daemon: str = "none"
    daemon_by: str = ""

    @property
    def client(self) -> str:
        return self.probe.tools.get(TOOL[self.backend], TOOL[self.backend]) if self.backend else ""

    @property
    def title(self) -> str:
        return NAMES.get(self.backend or "", "LXC / LXD")


def start_command(init: str, unit: str) -> list[str]:
    if init == "openrc":
        return ["rc-service", unit, "start"]
    if init == "sysvinit":
        return ["service", unit, "start"]
    return ["systemctl", "start", unit]


def daemon_words(ctx: Context) -> str:
    state, by = ctx.daemon, ctx.daemon_by
    if state == "running":
        return f"running ({by})" if by else "running"
    if state == "idle":
        return f"idle: {by} listens and starts the daemon for the first client; read when asked (Read now)"
    if state == "stopped":
        return f"stopped ({by}): Start… under Daemons starts it"
    if state == "none":
        return "none: classic LXC's tools work on their own"
    return "not known: none of its services was found here, so it is read directly"


class LxdPlugin(MachinePlugin):
    """Incus, LXD or classic LXC on this native Linux machine: read, and changed only when asked."""

    name = "lxd"
    title = "LXC / LXD"
    about = ("Incus, LXD or classic LXC on the native Linux machine the platform runs on, never a WSL distribution: the "
             "containers and virtual machines, their snapshots, the images, profiles, storage pools, networks and the "
             "daemon's services; changed only when asked")
    api = "2.1"
    order = 705  # the machine's own instances: WSL's on Windows (700-702), LXC / LXD and libvirt on Linux
    ICON = "lxd"  # LXD's logo, a cube of small cubes (icons.js, liminal-libs 1.33.0)
    HOOKS = (*MachinePlugin.HOOKS, "power", "snapshot", "restore", "delete", "service", "wake")

    def __init__(self, shell: Shell | None = None, runner=None, native: str | None = None):
        self._shell = shell
        self._runner = runner
        self._native = native  # a test's word for why_not_native(): "" on a native Linux machine, else why not
        self._woken = False  # an idle daemon read once asked (Read now), until the platform lets the plugin go
        self._sudo: tuple[float, bool] | None = None  # (when, whether sudo -n ran without a password)

    def not_for_here(self) -> str:
        """Why the machine the platform runs on is not a native Linux one, in words ("" when it is)."""
        return why_not_native() if self._native is None else self._native

    @property
    def shell(self) -> Shell:
        """This native Linux machine's commands (root through sudo -n); refused anywhere else, and for a WSL target."""
        note = self.not_for_here()
        if note:
            raise LxdError(note)
        sh = self._shell if self._shell is not None else Shell("local", runner=self._runner)
        if sh.target != "local":
            raise LxdError(f"LXC / LXD is for this native Linux machine, never {sh.where}")
        return sh

    def detach(self) -> None:
        """Let go by the platform: an idle daemon is no longer read until asked again."""
        self._woken = False
        super().detach()

    # -- looking --------------------------------------------------------------------------------------------------
    def _probe(self, sh: Shell) -> Probe:
        found = parse_probe(sh.sh(PROBE, root=False, timeout=60))
        if found.problem:
            raise LxdError(found.problem)
        return found

    def _context(self) -> Context:
        sh = self.shell
        p = self._probe(sh)
        found = backends(p)
        if not found:
            return Context(sh, p, None)
        backend = found[0]

        def sudo() -> bool:
            now = time.monotonic()
            if self._sudo is None or now - self._sudo[0] > SUDO_S:
                try:
                    sh.run("true", timeout=30)  # sudo -n true: never a password asked
                    self._sudo = (now, True)
                except CommandError:
                    self._sudo = (now, False)
            return self._sudo[1]
        state, by = daemon_state(p, backend)
        return Context(sh, p, backend, tuple(found[1:]), access_of(p, backend, sudo), state, by)

    def _ready(self) -> Context:
        """The context for a change or a read asked: refused when nothing is installed or the rights are missing."""
        ctx = self._context()
        if ctx.backend is None:
            raise LxdError(install_words(ctx.probe))
        if ctx.access.kind == "none":
            raise LxdError(ctx.access.words, denied=True)
        return ctx

    def _run(self, ctx: Context, *args: str, timeout: float = READ_S) -> str:
        """A command with the platform's rights over it; a refusal for want of them says how they are given."""
        try:
            return ctx.shell.run(*args, root=ctx.access.as_root, timeout=timeout)
        except CommandError as e:
            text = inv_mod.words_of([str(e)])
            if e.denied or any(d in text.lower() for d in DENIED):
                raise LxdError(f"{text}; {ctx.access.how}", denied=True) from None
            raise LxdError(text) from None

    def _inventory(self, ctx: Context) -> Inventory:
        if ctx.backend == "lxc":
            return parse_lxc(ctx.shell.sh(inv_mod.LXC_SCRIPT, root=ctx.access.as_root, timeout=READ_S))
        script = read_script(ctx.client, all_projects=ctx.access.kind != "restricted")
        return parse_read(ctx.shell.sh(script, root=ctx.access.as_root, timeout=READ_S))

    def present(self) -> bool:
        """Incus, LXD or classic LXC installed on a native Linux machine: never on Windows, macOS or in WSL."""
        if self.not_for_here():
            return False
        try:
            return bool(backends(self._probe(self.shell)))
        except CommandError:
            return False

    # -- the status -----------------------------------------------------------------------------------------------
    def status(self) -> dict:
        note = self.not_for_here()
        if note:  # not for this machine at all: asked nothing, no setup offered
            return self.not_here_status(note, tables=[], forms=[], actions=[])
        try:
            ctx = self._context()
        except CommandError as e:
            return {"present": False, "summary": f"cannot be read: {e}", "valid": False, "why": f"cannot be read: {e}",
                    "tables": [], "forms": [], "actions": []}
        p = ctx.probe
        if ctx.backend is None:
            return {"present": False, "summary": "not installed", "distribution": distribution(p), "initSystem": init_words(p),
                    "valid": False, "why": install_words(p), "tables": [], "forms": [], "actions": []}
        soft = software(p, ctx.backend)
        facts = {"software": soft}
        if ctx.others:
            facts["alsoInstalled"] = ", ".join(software(p, b) for b in ctx.others)
        facts.update({"client": ctx.client if ctx.backend != "lxc" else ctx.client.rsplit("/", 1)[0] + "/lxc-*",
                      "daemon": daemon_words(ctx), "rights": ctx.access.words, "initSystem": init_words(p),
                      "distribution": distribution(p)})
        daemons = self._daemons_table(ctx)
        if ctx.access.kind == "none":
            return {"present": True, "summary": f"{soft}: no rights to it here", **facts, "valid": False, "why": ctx.access.words,
                    "tables": [daemons], "forms": [], "actions": []}
        read = ctx.daemon in ("running", "unknown", "none") or (ctx.daemon == "idle" and self._woken)
        try:
            inv = self._inventory(ctx) if read else None
        except CommandError as e:  # the read itself refused (sudo -n for sh, a timeout): said, never raised
            inv = Inventory(problems=[str(e)], denied=e.denied)
        problems = []
        if ctx.daemon == "stopped":
            problems.append(f"the {ctx.title} daemon is stopped ({ctx.daemon_by}): Start… under Daemons starts it")
        denied = inv is not None and inv.denied
        if inv is not None:
            problems += inv.problems + ([ctx.access.how] if denied else [])
            facts.update(self._counts(ctx, inv))
        if ctx.daemon == "idle" and not self._woken:
            empty = (f"Not read: the {ctx.title} daemon is idle ({ctx.daemon_by} listens) and a read would start it. "
                     "Read now… reads it; Start… under Daemons starts its service.")
        elif ctx.daemon == "stopped":
            empty = f"Not read: the {ctx.title} daemon is stopped. Start… under Daemons starts it."
        elif inv is not None and inv.problems and not inv.instances:
            empty = f"Not read: {inv.problems[0]}"
        else:
            empty = {"lxc": "No containers yet: lxc-create makes one.",
                     "lxd": "No instances yet: lxc launch ubuntu:24.04 web makes one."}.get(
                         ctx.backend, "No instances yet: incus launch images:debian/12 web makes one.")
        tables = [self._instances_table(ctx, inv, empty), self._snapshots_table(ctx, inv)]
        if ctx.backend != "lxc" and inv is not None:
            tables += [self._images_table(inv), self._profiles_table(inv), self._pools_table(inv), self._networks_table(ctx, inv)]
        tables.append(daemons)
        out = {"present": True, "summary": self._summary(ctx, soft, inv), **facts, "valid": not denied,
               "why": "; ".join(problems) if denied else "", "tables": tables, "forms": self._forms(ctx),
               "actions": [{"form": "wake", "title": "Read now…", "note": "connects to the idle daemon, which starts it"}]
               if ctx.daemon == "idle" and not self._woken else []}
        if problems:
            out["problem"] = "; ".join(problems)
        return out

    @staticmethod
    def _summary(ctx: Context, soft: str, inv: Inventory | None) -> str:
        what = "container" if ctx.backend == "lxc" else "instance"
        if ctx.daemon == "stopped":
            return f"{soft}: the daemon is stopped"
        if inv is None:
            return f"{soft}: the daemon is idle (Read now… reads it)"
        if inv.problems and not inv.instances:
            return f"{soft}: {inv.problems[0]}"
        n, up = len(inv.instances), sum(i.running for i in inv.instances)
        return f"{soft}: {plural(n, what)}, {up} running" if n else f"{soft}: no {what}s yet"

    @staticmethod
    def _counts(ctx: Context, inv: Inventory) -> dict:
        out = {}
        if inv.problems and not inv.instances:
            return out
        n, up = len(inv.instances), sum(i.running for i in inv.instances)
        kinds = Counter(i.kind for i in inv.instances)
        out["instances"] = f"{n}: {up} running" + (" · " + ", ".join(plural(c, k) for k, c in sorted(kinds.items())) if n else "")
        out["snapshots"] = str(len(inv.snapshots))
        projects = sorted({i.project for i in inv.instances} - {"default"})
        if projects:
            out["projects"] = "default, " + ", ".join(projects)
        if ctx.backend != "lxc":
            sizes = [i.size for i in inv.images if i.size]
            out["images"] = f"{len(inv.images)}, {size(sum(sizes))}" if inv.images else "none"
            out["storagePools"] = (", ".join(f"{p.name} ({p.driver})" for p in inv.pools)
                                   or "none yet: incus admin init (lxd init for LXD) sets one up")
            managed = [n for n in inv.networks if n.managed]
            out["networks"] = ", ".join(f"{n.name} ({n.ipv4 or n.type})" for n in managed) or "none of its own"
        return out

    # -- the tables -----------------------------------------------------------------------------------------------
    @staticmethod
    def _values(i) -> dict:
        return {"name": i.name, **({"project": i.project} if i.project != "default" else {})}

    def _instances_table(self, ctx: Context, inv: Inventory | None, empty: str) -> dict:
        lxc = ctx.backend == "lxc"
        rows = []
        for i in (inv.instances if inv else []):
            values = self._values(i)
            sub = " · ".join(x for x in (f"project {i.project}" if i.project != "default" else "", i.description) if x) or None
            power = {"form": "power", "title": "Stop…" if i.running else "Start…",
                     "values": {**values, "action": "stop" if i.running else "start"}, "label": f"Start, stop or restart {i.name}"}
            snap = {"form": "snapshot", "title": "Snapshot…", "values": values, "label": f"Take a snapshot of {i.name}"}
            delete = ({"form": "delete", "title": "Delete…", "values": values, "label": f"Delete {i.name}"} if not i.running
                      else cell("–", "stop it first"))
            ips = ", ".join(i.ipv4) or "–"
            if lxc:
                rows.append([cell(i.name, sub), i.state, "yes" if i.autostart else "no", ips,
                             {True: "yes", False: "no"}.get(i.unprivileged, "–"), power, snap, delete])
            else:
                rows.append([cell(i.name, sub), i.kind, i.state, ips, i.image or "–", ", ".join(i.profiles) or "–", power, snap, delete])
        if lxc:
            columns = [{"title": "Container"}, {"title": "State"}, {"title": "At boot"}, {"title": "IPv4"}, {"title": "Unprivileged"}]
        else:
            columns = [{"title": "Instance"}, {"title": "Type"}, {"title": "State"}, {"title": "IPv4"}, {"title": "Image"}, {"title": "Profiles"}]
        note = ("root's containers (lxc-ls --fancy); a stop waits up to 60 s, then stops it hard" if lxc else
                f"{'every project' if ctx.access.kind != 'restricted' else 'your own project'} ({ctx.client} list --format json); "
                "a stop waits up to 60 s for a clean shutdown")
        return {"title": "Containers" if lxc else "Instances", "note": note,
                "columns": columns + [{"title": ""}, {"title": ""}, {"title": ""}], "rows": rows, "empty": empty}

    def _snapshots_table(self, ctx: Context, inv: Inventory | None) -> dict:
        lxc = ctx.backend == "lxc"
        rows = []
        for s in (inv.snapshots if inv else []):
            values = {"name": s.instance, "snapshot": s.name, **({"project": s.project} if s.project != "default" else {})}
            restore = {"form": "restore", "title": "Restore…", "values": values, "label": f"Restore {s.instance} to {s.name}"}
            owner = cell(s.instance, f"project {s.project}" if s.project != "default" else None)
            rows.append([owner, s.name, s.created or "–", restore] if lxc else
                        [owner, s.name, s.created or "–", "yes" if s.stateful else "no", s.expires or "never", restore])
        columns = ([{"title": "Container"}, {"title": "Snapshot"}, {"title": "Taken"}] if lxc else
                   [{"title": "Instance"}, {"title": "Snapshot"}, {"title": "Taken"}, {"title": "Stateful"}, {"title": "Expires"}])
        return {"title": "Snapshots", "columns": columns + [{"title": ""}], "rows": rows,
                "empty": "No snapshots: Snapshot… beside an instance takes one." if inv is not None else "Not read."}

    @staticmethod
    def _images_table(inv: Inventory) -> dict:
        rows = [[cell(im.label, im.description or None), "virtual machine" if im.type == "virtual-machine" else "container",
                 im.architecture or "–", size(im.size), im.uploaded or "–"] for im in inv.images]
        return {"title": "Images", "columns": [{"title": "Image"}, {"title": "Type"}, {"title": "Architecture"}, {"title": "Size", "num": True},
                                               {"title": "Uploaded"}], "rows": rows, "empty": "No images: a launch fetches one."}

    @staticmethod
    def _profiles_table(inv: Inventory) -> dict:
        rows = [[cell(pr.name, pr.description or None), ", ".join(pr.devices) or "–", str(pr.used_by)] for pr in inv.profiles]
        return {"title": "Profiles", "columns": [{"title": "Profile"}, {"title": "Devices"}, {"title": "Used by", "num": True}],
                "rows": rows, "empty": "No profiles."}

    @staticmethod
    def _pools_table(inv: Inventory) -> dict:
        rows = [[cell(po.name, po.description or None), po.driver or "–", po.source or "–", po.status or "–", str(po.used_by)]
                for po in inv.pools]
        return {"title": "Storage pools", "columns": [{"title": "Pool"}, {"title": "Driver"}, {"title": "Source"}, {"title": "State"},
                                                      {"title": "Used by", "num": True}],
                "rows": rows, "empty": "No storage pool yet: incus admin init (lxd init for LXD) sets one up."}

    @staticmethod
    def _networks_table(ctx: Context, inv: Inventory) -> dict:
        managed = [n for n in inv.networks if n.managed]
        others = [n.name for n in inv.networks if not n.managed]
        rows = [[cell(n.name, n.description or None), n.type or "–", n.ipv4 or "–", n.ipv6 or "–", str(n.used_by)] for n in managed]
        return {"title": "Networks", "note": f"the machine's own interfaces, not {ctx.title}'s: {', '.join(others)}" if others else "",
                "columns": [{"title": "Network"}, {"title": "Type"}, {"title": "IPv4"}, {"title": "IPv6"}, {"title": "Used by", "num": True}],
                "rows": rows, "empty": f"No network of {ctx.title}'s own: incus admin init (lxd init) makes one."}

    @staticmethod
    def _daemons_table(ctx: Context) -> dict:
        p = ctx.probe
        units = sorted((u for u in p.units.values() if u.known and u.known.backend in (ctx.backend, "*")),
                       key=lambda u: (not u.known.daemon, not u.known.socket, u.name))
        rows = [[u.name, u.state, u.boot or "–", u.role,
                 {"form": "service", "title": "Start…", "values": {"unit": u.name, "action": "start"}, "label": f"Start {u.name}"}
                 if u.startable else ""] for u in units]
        return {"title": "Daemons", "note": f"{init_words(p)}; a service is started here only when asked, and never stopped",
                "columns": [{"title": "Service"}, {"title": "State"}, {"title": "At boot"}, {"title": "What it is"}, {"title": ""}],
                "rows": rows, "empty": f"None of {ctx.title}'s services is known to {init_words(p)} here."}

    # -- the forms (the platform's page: each change confirmed; restoring and deleting asked a second time). A form's
    # fields are its hook's arguments in order: the project, which classic LXC's forms leave out, always last ------
    def _forms(self, ctx: Context) -> list[dict]:
        lxc = ctx.backend == "lxc"
        what = "container" if lxc else "instance"
        name = {"name": "name", "label": what.capitalize(), "type": "fixed"}
        project = [] if lxc else [{"name": "project", "label": "Project", "type": "fixed", "empty": "default"}]
        stop_note = ("asks it to shut down, and stops it hard after 60 s" if lxc else
                     "a clean shutdown, waited for up to 60 s (one that does not shut down by then keeps running)")
        power = {"id": "power", "hook": "power", "title": f"Start, stop or restart a {what}",
                 "fields": [name, {"name": "action", "label": "Do", "type": "choice", "value": "start", "choices": [
                     {"value": "start", "title": "Start", "note": "boots it"},
                     {"value": "stop", "title": "Stop", "note": stop_note},
                     {"value": "restart", "title": "Restart", "note": "a stop, then a start"}]}, *project],
                 "submit": "Review",
                 "confirm": {"title": "{action} {name}?", "words": f"Asked of {ctx.title} now; nothing else changes.",
                             "yes": "Yes, do it"}}
        snapshot = {"id": "snapshot", "hook": "snapshot", "title": f"Take a snapshot of a {what}",
                    "fields": [name, *([] if lxc else [{"name": "snapshot", "label": "Its name", "type": "text",
                                                        "placeholder": "snap0, before-upgrade",
                                                        "note": "letters, digits, dots, dashes, underscores; empty: the next free name (snap0, snap1...)"}]),
                               *project],
                    "submit": "Review",
                    "confirm": {"title": "Take a snapshot of {name}?",
                                "words": ("lxc-snapshot copies the container (an overlay, btrfs or zfs one is snapshotted instead) "
                                          "and names the snapshot itself (snap0, snap1...)." if lxc else
                                          "Its disk is kept as it is now (not a running instance's memory). On a dir storage pool "
                                          "the snapshot is a full copy, and takes a while."),
                                "yes": "Take it"}}
        restore = {"id": "restore", "hook": "restore", "title": f"Restore a {what} to a snapshot",
                   "fields": [name, {"name": "snapshot", "label": "Snapshot", "type": "fixed"},
                              {"name": "accept_restore", "type": "accept", "title": "Lose what changed since the snapshot?",
                               "yes": "Restore it",
                               "words": "{name}'s disk is replaced by {snapshot}'s: whatever changed in it since that snapshot is lost "
                                        "for good. A snapshot taken first keeps it."}, *project],
                   "confirm": {"title": "Restore {name} to {snapshot}?",
                               "words": ("{name} is put back as it was in {snapshot}." + (" It must be stopped." if lxc else
                                         " A running one is stopped for it and started again.")),
                               "yes": "Restore", "danger": True}}
        delete = {"id": "delete", "hook": "delete", "title": f"Delete a {what}",
                  "fields": [name,
                             {"name": "accept_delete", "type": "accept", "title": "Delete it for good?", "yes": "Delete it for good",
                              "words": "Deleting {name} cannot be undone: its disk and every snapshot of it are gone for good."}, *project],
                  "confirm": {"title": "Delete {name}?", "words": f"{{name}} and its snapshots are removed from {ctx.title}.",
                              "yes": "Delete", "danger": True}}
        how = {"openrc": "rc-service {unit} start", "sysvinit": "service {unit} start"}.get(ctx.probe.init, "systemctl start {unit}")
        service = {"id": "service", "hook": "service", "title": "Start a service",
                   "fields": [{"name": "unit", "label": "Service", "type": "fixed"}, {"name": "action", "label": "Do", "type": "fixed", "value": "start"}],
                   "confirm": {"title": "Start {unit}?", "words": f"{how}, as root (sudo -n when the platform is not root). "
                                                                  "Whether it starts at boot is left as it is.", "yes": "Start it"}}
        wake = {"id": "wake", "hook": "wake", "title": "Read now", "fields": [],
                "confirm": {"title": f"Read {ctx.title} now?",
                            "words": (f"The {ctx.title} daemon is idle: {ctx.daemon_by or 'its socket'} listens, and connecting to it starts "
                                      "the daemon. Read now connects, and the plugin reads it from then on, until the platform lets it go."),
                            "yes": "Read now"}}
        return [power, snapshot, restore, delete, service, wake]

    # -- the hooks: changes, each asked ---------------------------------------------------------------------------
    def _project_args(self, ctx: Context, project: str | None) -> list[str]:
        return ["--project", project] if project and ctx.backend != "lxc" else []

    def _find(self, ctx: Context, name: str, project: str | None):
        """The instance asked of (read now): refused when there is none of that name."""
        inv = self._inventory(ctx)
        if inv.problems and not inv.instances:
            raise LxdError(inv.problems[0], denied=inv.denied)
        wanted = project or (None if ctx.access.kind == "restricted" or ctx.backend == "lxc" else "default")
        found = inv.instance(name, wanted)
        if found is None:
            raise Refusal(f"no {'container' if ctx.backend == 'lxc' else 'instance'} named {name}"
                          + (f" in the project {project}" if project else "") + " here")
        return found

    def power(self, name, action, project=None) -> dict:
        """Start, stop (a clean shutdown, waited for STOP_S) or restart an instance."""
        act = str(action or "").strip().lower()
        if act not in POWER:
            raise Refusal(f"{act!r} is none of {', '.join(POWER)}")
        name, project = checked(name, "an instance's name"), project_of(project)
        ctx = self._ready()
        if ctx.backend == "lxc":
            args = {"start": ["lxc-start", "-n", name], "stop": ["lxc-stop", "-n", name, "-t", str(STOP_S)],
                    "restart": ["lxc-stop", "-n", name, "-r", "-t", str(STOP_S)]}[act]
        else:
            args = [ctx.client, act, name, *(["--timeout", str(STOP_S)] if act != "start" else []), *self._project_args(ctx, project)]
        return answer(f"{name}: {DONE[act]}", self._run(ctx, *args, timeout=TIMEOUTS[act]))

    def snapshot(self, name, snapshot=None, project=None) -> dict:
        """A snapshot of an instance taken now (named, or the next free name)."""
        name, project = checked(name, "an instance's name"), project_of(project)
        snap = checked(snapshot, "a snapshot's name") if given(snapshot) else None
        ctx = self._ready()
        if ctx.backend == "lxc":
            if snap:
                raise Refusal("classic LXC names its snapshots itself (snap0, snap1...): leave the name empty")
            args = ["lxc-snapshot", "-n", name]
        elif ctx.backend == "incus":
            args = [ctx.client, "snapshot", "create", name, *([snap] if snap else []), *self._project_args(ctx, project)]
        else:
            args = [ctx.client, "snapshot", name, *([snap] if snap else []), *self._project_args(ctx, project)]
        return answer(f"{name}: a snapshot taken" + (f" ({snap})" if snap else ""), self._run(ctx, *args, timeout=CHANGE_S))

    def restore(self, name, snapshot, accept_restore=False, project=None) -> dict:
        """An instance put back as it was in one of its snapshots: what changed since is lost, so asked twice. The
        project comes last, so that classic LXC's form (which has none) asks the same arguments in the same places."""
        name, project = checked(name, "an instance's name"), project_of(project)
        snap = checked(snapshot, "a snapshot's name")
        ctx = self._ready()
        inst = self._find(ctx, name, project)
        if not any(s.name == snap for s in inst.snapshots):
            raise Refusal(f"{name} has no snapshot named {snap}: {', '.join(s.name for s in inst.snapshots) or 'it has none'}")
        if ctx.backend == "lxc" and inst.running:
            raise Refusal(f"{name} is {inst.state}: classic LXC restores a stopped container, so stop it first")
        if not yes(accept_restore):
            raise Refusal(f"restoring {snap} replaces {name}'s disk: what changed in it since that snapshot is lost for good",
                          accept="accept_restore")
        if ctx.backend == "lxc":
            args = ["lxc-snapshot", "-n", name, "-r", snap]
        elif ctx.backend == "incus":
            args = [ctx.client, "snapshot", "restore", name, snap, *self._project_args(ctx, project)]
        else:
            args = [ctx.client, "restore", name, snap, *self._project_args(ctx, project)]
        return answer(f"{name}: restored to {snap}", self._run(ctx, *args, timeout=CHANGE_S))

    def delete(self, name, accept_delete=False, project=None) -> dict:
        """A stopped instance deleted with its snapshots: for good, so asked twice (the project last, as restore's)."""
        name, project = checked(name, "an instance's name"), project_of(project)
        ctx = self._ready()
        inst = self._find(ctx, name, project)
        if inst.running:
            raise Refusal(f"{name} is {inst.state}: stop it first, then delete it")
        if not yes(accept_delete):
            raise Refusal(f"deleting {name} removes its disk and its {plural(len(inst.snapshots), 'snapshot')} for good: "
                          "nothing brings it back", accept="accept_delete")
        args = ["lxc-destroy", "-n", name, "-s"] if ctx.backend == "lxc" else [ctx.client, "delete", name, *self._project_args(ctx, project)]
        return answer(f"{name}: deleted", self._run(ctx, *args, timeout=CHANGE_S))

    def service(self, unit, action="start") -> dict:
        """One of the daemon's services started (as root: sudo -n when the platform is not); none is ever stopped."""
        if str(action or "start").strip().lower() != "start":
            raise Refusal("only start: a service is started here when asked, and never stopped")
        unit = str(unit or "").strip()
        if not UNIT.match(unit):
            raise Refusal(f"{unit!r} is not a service's name")
        ctx = self._context()
        if ctx.backend is None:
            raise LxdError(install_words(ctx.probe))
        mine = {u.name: u for u in ctx.probe.units.values() if u.known and u.known.backend in (ctx.backend, "*")}
        u = mine.get(unit)
        if u is None:
            raise Refusal(f"{unit} is not one of {ctx.title}'s services here: {', '.join(sorted(mine)) or 'none is known'}")
        if u.up:
            return answer(f"{unit} is {u.state} already")
        if not u.startable:
            raise Refusal(f"{unit} is {u.state}: not started from here")
        cmd = start_command(ctx.probe.init, unit)
        try:
            out = ctx.shell.run(*cmd, timeout=SERVICE_S)
        except CommandError as e:
            if e.denied:
                raise LxdError(f"{e}; or start it yourself: sudo {' '.join(cmd)}", denied=True) from None
            raise
        return answer(f"{unit} started ({' '.join(cmd)})", out)

    def wake(self) -> dict:
        """An idle daemon read now (connecting starts it), and from then on until the platform lets the plugin go."""
        ctx = self._ready()
        self._woken = True
        inv = self._inventory(ctx)
        if inv.problems:
            return answer(f"read, with trouble: {inv.problems[0]}", "\n".join(inv.problems[1:]), ok=False)
        return answer(f"{ctx.title} read: {plural(len(inv.instances), 'instance')}, {sum(i.running for i in inv.instances)} running")

    def run(self, *command: str, **options) -> str:
        """The client with these arguments (incus ..., lxc ...), or one of classic LXC's lxc-* tools, its output."""
        ctx = self._ready()
        if not command:
            raise Refusal("nothing to run")
        if ctx.backend == "lxc":
            if not str(command[0]).startswith("lxc-"):
                raise Refusal("classic LXC's own tools only: lxc-ls, lxc-info, lxc-start, ...")
            return self._run(ctx, *command)
        return self._run(ctx, ctx.client, *command)


MACHINE = LxdPlugin()
