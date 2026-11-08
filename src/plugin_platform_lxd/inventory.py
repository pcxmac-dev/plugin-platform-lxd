"""What Incus, LXD or classic LXC holds, read with one script: the client's JSON (`incus` or LXD's `lxc`, `--format
json`: the instances with their state, addresses and snapshots, the images, the profiles, the storage pools, the
networks), or classic LXC's `lxc-ls --fancy` and each container's `lxc-snapshot -L`. `read_script` and `LXC_SCRIPT`
are what runs (C locale, each command's exit code after it); `parse` and `parse_lxc` read their answers into an
`Inventory`, each failure in the client's own words."""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field

from .system import sections

MARK = "== liminal-lxd-read 1"
LXC_MARK = "== liminal-lxc-read 1"
PARTS = (("instances", "list --format json"), ("images", "image list --format json"), ("profiles", "profile list --format json"),
         ("storage", "storage list --format json"), ("networks", "network list --format json"))
DENIED = ("permission denied", "needed permissions", "not authorized", "forbidden", "access denied")
DOWN = ("doesn't appear to be started", "please check lxd is running", "no such file or directory", "connection refused")
NEVER = "0001-01-01"  # Go's zero time: a snapshot that never expires
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$")
_LXC_SNAP = re.compile(r"^(\S+) \((.*)\) (\d{4}):(\d{2}):(\d{2}) (\d{2}:\d{2})(?::\d{2})?$")


def read_script(client: str, all_projects: bool = True) -> str:
    """The client's JSON of everything this plugin shows (the instances of every project when the rights cover them)."""
    c = shlex.quote(client)
    lines = ["LC_ALL=C; export LC_ALL", f"echo '{MARK}'"]
    for part, words in PARTS:
        every = " --all-projects" if part == "instances" and all_projects else ""
        lines.append(f"echo '== {part}'; {c} {words}{every} 2>&1; echo \"== rc $?\"")
    lines.append("echo '== end'")
    return "\n".join(lines) + "\n"


LXC_SCRIPT = f"""LC_ALL=C; export LC_ALL
echo '{LXC_MARK}'
echo '== containers'; lxc-ls --fancy 2>&1; echo "== rc $?"
for n in $(lxc-ls -1 2>/dev/null); do echo "== snapshots $n"; lxc-snapshot -n "$n" -L 2>&1; done
echo '== end'
"""


def when(text) -> str:
    """An API time as written (its zone kept, Z as UTC): "2024-05-12 09:14 UTC"; "" for none or Go's zero time."""
    t = str(text or "").strip()
    if not t or t.startswith(NEVER):
        return ""
    m = _ISO.match(t)
    if not m:
        return t
    zone = m.group(3) or ""
    return f"{m.group(1)} {m.group(2)}" + (" UTC" if zone in ("Z", "+00:00", "+0000") else f" {zone}" if zone else "")


@dataclass(frozen=True)
class Snapshot:
    instance: str
    name: str
    created: str = ""
    expires: str = ""
    stateful: bool = False
    project: str = "default"


@dataclass(frozen=True)
class Instance:
    """An instance (a container or a virtual machine); classic LXC's containers also say whether they start at boot
    and run unprivileged."""
    name: str
    type: str = "container"  # container | virtual-machine
    state: str = "unknown"  # running | stopped | frozen | error | starting | stopping | aborting
    ipv4: tuple = ()  # "10.120.17.23 (eth0)"
    image: str = ""
    profiles: tuple = ()
    project: str = "default"
    description: str = ""
    snapshots: tuple = ()
    autostart: bool | None = None
    unprivileged: bool | None = None

    @property
    def running(self) -> bool:
        return self.state in ("running", "frozen", "starting", "stopping")

    @property
    def kind(self) -> str:
        return "virtual machine" if self.type == "virtual-machine" else "container"


@dataclass(frozen=True)
class Image:
    fingerprint: str
    aliases: tuple = ()
    description: str = ""
    type: str = "container"
    architecture: str = ""
    size: int | None = None
    uploaded: str = ""

    @property
    def label(self) -> str:
        return self.aliases[0] if self.aliases else self.fingerprint[:12]


@dataclass(frozen=True)
class Profile:
    name: str
    description: str = ""
    devices: tuple = ()  # "root (disk on default)"
    used_by: int = 0


@dataclass(frozen=True)
class Pool:
    name: str
    driver: str = ""
    source: str = ""
    status: str = ""
    description: str = ""
    used_by: int = 0


@dataclass(frozen=True)
class Network:
    name: str
    type: str = ""
    managed: bool = False
    ipv4: str = ""
    ipv6: str = ""
    description: str = ""
    used_by: int = 0


@dataclass
class Inventory:
    instances: list = field(default_factory=list)
    images: list = field(default_factory=list)
    profiles: list = field(default_factory=list)
    pools: list = field(default_factory=list)
    networks: list = field(default_factory=list)
    problems: list = field(default_factory=list)  # each part that failed, in the client's words
    denied: bool = False  # a part failed for want of the rights
    down: bool = False  # the daemon did not answer

    @property
    def snapshots(self) -> list:
        return [s for i in self.instances for s in i.snapshots]

    def instance(self, name: str, project: str | None = None) -> Instance | None:
        return next((i for i in self.instances if i.name == name and (project is None or i.project == project)), None)


def words_of(lines: list[str]) -> str:
    """A client's error in its own words, without the Error: it starts with."""
    text = " ".join(line.strip() for line in lines if line.strip())
    return re.sub(r"^(Error|error):\s*", "", text)


def _parts(text: str) -> dict[str, tuple[object, list[str], int]]:
    """Each part's JSON (or None), the lines beside it (a warning, an error), its exit code."""
    out: dict[str, tuple[object, list[str], int]] = {}
    names = {part for part, _ in PARTS}
    current, lines = None, []
    for head, rest, body in sections(text):
        if head in names:
            current, lines = head, body
        elif head == "rc" and current is not None:
            data, other = None, []
            for line in lines:
                s = line.strip()
                if data is None and s[:1] in ("[", "{"):
                    try:
                        data = json.loads(s)
                        continue
                    except ValueError:
                        pass
                if s:
                    other.append(s)
            out[current] = (data, other, int(rest) if rest.isdecimal() else 1)
            current = None
    return out


def parse(text: str) -> Inventory:
    """The read script's answer as an Inventory."""
    inv = Inventory()
    if MARK not in (text or ""):
        inv.problems.append("the read's answer was not understood")
        return inv
    for part, (data, other, code) in _parts(text).items():
        if code != 0 or data is None:
            said = words_of(other) or f"{part}: no answer"
            low = said.lower()
            inv.denied = inv.denied or any(d in low for d in DENIED)
            inv.down = inv.down or any(d in low for d in DOWN)
            if said not in inv.problems:
                inv.problems.append(said)
            continue
        items = data if isinstance(data, list) else []
        if part == "instances":
            inv.instances = sorted((instance_of(d) for d in items if isinstance(d, dict)),
                                   key=lambda i: (i.project != "default", i.project, i.name))
        elif part == "images":
            inv.images = sorted((image_of(d) for d in items if isinstance(d, dict)), key=lambda i: i.label)
        elif part == "profiles":
            inv.profiles = [profile_of(d) for d in items if isinstance(d, dict)]
        elif part == "storage":
            inv.pools = [pool_of(d) for d in items if isinstance(d, dict)]
        elif part == "networks":
            inv.networks = sorted((network_of(d) for d in items if isinstance(d, dict)), key=lambda n: (not n.managed, n.name))
    return inv


def instance_of(d: dict) -> Instance:
    name = str(d.get("name") or "")
    project = str(d.get("project") or "default")
    state = d.get("state") if isinstance(d.get("state"), dict) else {}
    status = str(d.get("status") or state.get("status") or "unknown").lower()
    ipv4 = []
    for dev, net in sorted((state.get("network") or {}).items()):
        if dev == "lo" or not isinstance(net, dict):
            continue
        for a in net.get("addresses") or []:
            if isinstance(a, dict) and a.get("family") == "inet" and a.get("scope", "global") == "global" and a.get("address"):
                ipv4.append(f"{a['address']} ({dev})")
    config = d.get("config") if isinstance(d.get("config"), dict) else {}
    image = config.get("image.description") or " ".join(x for x in (config.get("image.os"), config.get("image.release")) if x)
    snaps = tuple(Snapshot(name, str(s.get("name") or "").rsplit("/", 1)[-1], when(s.get("created_at")), when(s.get("expires_at")),
                           bool(s.get("stateful")), project)
                  for s in (d.get("snapshots") or []) if isinstance(s, dict) and s.get("name"))
    return Instance(name, str(d.get("type") or "container"), status, tuple(ipv4), str(image or ""),
                    tuple(str(p) for p in d.get("profiles") or ()), project, str(d.get("description") or ""), snaps)


def image_of(d: dict) -> Image:
    props = d.get("properties") if isinstance(d.get("properties"), dict) else {}
    size = d.get("size")
    return Image(str(d.get("fingerprint") or ""), tuple(str(a.get("name")) for a in d.get("aliases") or [] if isinstance(a, dict) and a.get("name")),
                 str(props.get("description") or ""), str(d.get("type") or "container"), str(d.get("architecture") or ""),
                 int(size) if isinstance(size, (int, float)) else None, when(d.get("uploaded_at")))


def profile_of(d: dict) -> Profile:
    devices = []
    for dev, spec in sorted((d.get("devices") or {}).items()):
        spec = spec if isinstance(spec, dict) else {}
        kind = str(spec.get("type") or "device")
        where = spec.get("pool") if kind == "disk" else (spec.get("network") or spec.get("parent")) if kind == "nic" else None
        devices.append(f"{dev} ({kind}{f' on {where}' if where else ''})")
    return Profile(str(d.get("name") or ""), str(d.get("description") or ""), tuple(devices), len(d.get("used_by") or []))


def pool_of(d: dict) -> Pool:
    config = d.get("config") if isinstance(d.get("config"), dict) else {}
    return Pool(str(d.get("name") or ""), str(d.get("driver") or ""), str(config.get("source") or ""),
                str(d.get("status") or "").lower(), str(d.get("description") or ""), len(d.get("used_by") or []))


def network_of(d: dict) -> Network:
    config = d.get("config") if isinstance(d.get("config"), dict) else {}

    def address(version: str) -> str:
        a = str(config.get(f"{version}.address") or "")
        if not a or a == "none":
            return ""
        return a + (" NAT" if str(config.get(f"{version}.nat")) == "true" else "")
    return Network(str(d.get("name") or ""), str(d.get("type") or ""), bool(d.get("managed")), address("ipv4"), address("ipv6"),
                   str(d.get("description") or ""), len(d.get("used_by") or []))


# -- classic LXC ------------------------------------------------------------------------------------------------
def fancy(text: str) -> list[dict[str, str]]:
    """`lxc-ls --fancy`'s rows by its header's columns (each padded to its widest value, IPv4's joined by ", ")."""
    lines = [line.rstrip("\r") for line in (text or "").splitlines() if line.strip()]
    header = next((i for i, line in enumerate(lines) if line.startswith("NAME ")), None)
    if header is None:
        return []
    cols = [(m.group(0), m.start()) for m in re.finditer(r"\S+", lines[header])]
    rows = []
    for line in lines[header + 1:]:
        row = {}
        for i, (title, start) in enumerate(cols):
            end = cols[i + 1][1] if i + 1 < len(cols) else len(line)
            row[title] = line[start:end].strip()
        if row.get("NAME"):
            rows.append(row)
    return rows


def parse_lxc(text: str) -> Inventory:
    """The classic LXC script's answer: the containers and their snapshots."""
    inv = Inventory()
    if LXC_MARK not in (text or ""):
        inv.problems.append("the read's answer was not understood")
        return inv
    snaps: dict[str, list[Snapshot]] = {}
    listing, code = [], 0
    for head, rest, body in sections(text):
        if head == "containers":
            listing = body
        elif head == "rc":
            code = int(rest) if rest.isdecimal() else 1
        elif head == "snapshots":
            for line in body:
                m = _LXC_SNAP.match(line.strip())
                if m:
                    snaps.setdefault(rest, []).append(Snapshot(rest, m.group(1), f"{m.group(3)}-{m.group(4)}-{m.group(5)} {m.group(6)}"))
    if code != 0:
        said = words_of(listing) or "lxc-ls failed"
        inv.denied = any(d in said.lower() for d in DENIED)
        inv.problems.append(said)
        return inv
    for row in fancy("\n".join(listing)):
        name = row["NAME"]
        ips = [a.strip() for a in row.get("IPV4", "").split(",") if a.strip() and a.strip() != "-"]
        inv.instances.append(Instance(name, "container", row.get("STATE", "unknown").lower(), tuple(ips), "", (), "default", "",
                                      tuple(snaps.get(name, ())), row.get("AUTOSTART") == "1",
                                      {"true": True, "false": False}.get(row.get("UNPRIVILEGED", "").lower())))
    inv.instances.sort(key=lambda i: i.name)
    return inv
