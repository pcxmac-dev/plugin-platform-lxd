"""What the LXC / LXD plugin attaches to (plugin API 2.2; the user, 2026-11-11: "each plugin should reflect the the API
its attached to, and present links that provide documentation ... it should also expose more of what the API is
about"): Incus and LXD (each a REST API over its unix socket, driven through its command line), classic LXC's tools, and
the Linux services and rights the plugin reads and starts the daemons through. Each part is named with what the plugin
does with it, each command with what for, and the notable others; every address was read on linuxcontainers.org,
canonical.com/lxd, freedesktop.org, man7.org and the projects' GitHub on 2026-11-11."""
from __future__ import annotations

from liminal_libs.sources.apis import Api, Area, Call, Link

VERIFIED = "2026-11-11"
INCUS = "Incus"
LXD = "LXD"
LXC = "LXC (classic)"
SERVICES = "Linux services and rights (systemd, OpenRC, sudo)"

_I = "https://linuxcontainers.org/incus/docs/main/"
_IM = _I + "reference/manpages/incus/"
_L = "https://canonical.com/lxd/docs/latest/"
_LM = _L + "reference/manpages/lxc/"
_C = "https://linuxcontainers.org/lxc/manpages/man1/"
_SYSTEMCTL = "https://www.freedesktop.org/software/systemd/man/latest/systemctl.html"
_OPENRC = "https://github.com/OpenRC/openrc/blob/master/user-guide.md"


def _incus(cmd: str) -> str:
    return _IM + cmd + "/"


def _lxd(cmd: str) -> str:
    return _LM + cmd + "/"


def _lxc(tool: str) -> str:
    return f"{_C}{tool}.1.html"


INCUS_API = Api(
    name=INCUS, maker="Linux Containers (the Incus project)", kind="command",
    about=("Incus, LXD's community fork: system containers and virtual machines, their images, profiles, storage pools and "
           "networks, and projects and clusters, all served by the Incus daemon as a REST API over its unix socket (and "
           "over HTTPS with a certificate). The plugin drives it through the incus command, its answers as JSON; each "
           "command is a REST call, named beside it."),
    base="incus (its REST API at /var/lib/incus/unix.socket, or /run/incus/unix.socket)",
    version="Incus 6.0 LTS and later (REST API 1.0; Incus 7.6 is the newest release)",
    auth=("the unix socket's group: incus-admin (the whole daemon) or incus (each member's own project); root through "
          "sudo -n otherwise; remote clients by TLS certificate or OIDC"),
    limits="none (a local daemon)",
    costs="",
    links=(
        Link("Incus documentation", _I, "docs"),
        Link("incus command reference", _IM, "reference"),
        Link("REST API", _I + "rest-api/", "reference"),
        Link("REST API specification", _I + "rest-api-spec/", "spec"),
        Link("API extensions", _I + "api-extensions/", "changelog"),
        Link("Remote API authentication", _I + "authentication/", "auth"),
        Link("Security", _I + "security/", "auth"),
        Link("How to manage instances", _I + "howto/instances_manage/", "guide"),
        Link("Events", _I + "events/", "guide"),
        Link("How to install Incus", _I + "installing/", "guide"),
        Link("Incus news (releases)", "https://linuxcontainers.org/incus/news/", "changelog"),
        Link("lxc/incus", "https://github.com/lxc/incus", "source"),
    ),
    areas=(
        Area("Instances", "containers and virtual machines: made, started, stopped, copied, moved, deleted (/1.0/instances)",
             _incus("list"), covered="listed in every project the rights cover, with their state, addresses, image, profiles "
                                     "and snapshots; started, stopped, restarted, deleted once confirmed"),
        Area("Snapshots", "an instance's snapshots: taken, restored, renamed, deleted", _incus("snapshot"),
             covered="listed; one taken; an instance restored to one (asked twice)"),
        Area("Images", "images and their aliases, local and remote (/1.0/images)", _incus("image"), covered="listed"),
        Area("Profiles", "sets of configuration and devices instances take (/1.0/profiles)", _incus("profile"),
             covered="listed with their devices and users"),
        Area("Storage", "storage pools, volumes and buckets (/1.0/storage-pools)", _incus("storage"),
             covered="the pools listed with their driver and source"),
        Area("Networks", "networks, their ACLs, address sets, forwards, load balancers, peers and zones (/1.0/networks …)",
             _incus("network"), covered="the managed networks listed with their addresses"),
        Area("The server", "the daemon's configuration and its environment (/1.0)", _incus("version"),
             covered="the client's version (incus --version)"),
        Area("Projects", "projects: separate sets of instances, images and profiles (/1.0/projects)", _incus("project")),
        Area("Cluster", "cluster members and groups (/1.0/cluster)", _incus("cluster")),
        Area("Trust", "certificates and identities allowed in (/1.0/certificates)", _incus("config")),
        Area("Operations and events", "background operations, the event stream (/1.0/operations, /1.0/events)",
             _incus("operation")),
        Area("Exec, console and files", "a command run in an instance, its console, files pushed and pulled",
             _incus("exec")),
        Area("Warnings, metrics and resources", "the daemon's warnings, its metrics, the host's resources",
             _incus("warning")),
    ),
    calls=(
        Call("incus --version", "the client's version", "The server", used="the software fact (Incus 6.0.4)",
             docs=_incus("version")),
        Call("incus list --format json --all-projects", "every instance with its state and snapshots "
             "(GET /1.0/instances?recursion=2)", "Instances", used="the Instances and Snapshots tables", docs=_incus("list")),
        Call("incus image list --format json", "the images (GET /1.0/images?recursion=1)", "Images", used="the Images table",
             docs=_IM + "image/list/"),
        Call("incus profile list --format json", "the profiles (GET /1.0/profiles?recursion=1)", "Profiles",
             used="the Profiles table", docs=_IM + "profile/list/"),
        Call("incus storage list --format json", "the storage pools (GET /1.0/storage-pools?recursion=1)", "Storage",
             used="the Storage pools table", docs=_IM + "storage/list/"),
        Call("incus network list --format json", "the networks (GET /1.0/networks?recursion=1)", "Networks",
             used="the Networks table", docs=_IM + "network/list/"),
        Call("incus start NAME", "an instance started (PUT /1.0/instances/{name}/state)", "Instances", used="Start…",
             docs=_incus("start"), writes=True),
        Call("incus stop NAME --timeout 60", "an instance shut down cleanly, 60 s at most", "Instances", used="Stop…",
             docs=_incus("stop"), writes=True),
        Call("incus restart NAME --timeout 60", "an instance restarted", "Instances", used="Restart…",
             docs=_incus("restart"), writes=True),
        Call("incus snapshot create NAME [SNAPSHOT]", "a snapshot taken (POST /1.0/instances/{name}/snapshots)",
             "Snapshots", used="Snapshot…", docs=_IM + "snapshot/create/", writes=True),
        Call("incus snapshot restore NAME SNAPSHOT", "an instance put back as it was in a snapshot", "Snapshots",
             used="Restore… (asked twice)", docs=_IM + "snapshot/restore/", writes=True),
        Call("incus delete NAME", "a stopped instance deleted with its snapshots (DELETE /1.0/instances/{name})", "Instances",
             used="Delete… (asked twice)", docs=_incus("delete"), writes=True),
        Call("incus launch IMAGE NAME", "an instance made and started", "Instances", docs=_incus("launch"), writes=True),
        Call("incus exec NAME -- COMMAND", "a command run in an instance", "Exec, console and files", docs=_incus("exec"),
             writes=True),
        Call("incus copy SOURCE TARGET", "an instance copied", "Instances", docs=_incus("copy"), writes=True),
        Call("incus config set NAME KEY=VALUE", "an instance's configuration changed", "Instances", docs=_incus("config"),
             writes=True),
        Call("incus monitor", "the daemon's events", "Operations and events", docs=_incus("monitor")),
        Call("incus query /1.0", "any REST call, as is", "The server", docs=_incus("query")),
    ),
    verified=VERIFIED)

LXD_API = Api(
    name=LXD, maker="Canonical", kind="command",
    about=("LXD: system containers and virtual machines, their images, profiles, storage pools and networks, projects and "
           "clusters, served by the LXD daemon as a REST API over its unix socket (and over HTTPS). Canonical ships it as a "
           "snap. The plugin drives it through LXD's lxc command, its answers as JSON; each command is a REST call, named "
           "beside it."),
    base="lxc (LXD's client; its REST API at /var/snap/lxd/common/lxd/unix.socket, or /var/lib/lxd/unix.socket)",
    version="LXD 5.21 LTS and later (REST API 1.0; the snap's 6/stable is 6.10)",
    auth="the unix socket's group: lxd; root through sudo -n otherwise; remote clients by TLS certificate or OIDC",
    limits="none (a local daemon)",
    costs="",
    links=(
        Link("LXD documentation", _L, "docs"),
        Link("lxc command reference", _LM, "reference"),
        Link("REST API", _L + "restapi_landing/", "reference"),
        Link("Main API overview", _L + "rest-api/", "reference"),
        Link("Main API specification", _L + "api/", "spec"),
        Link("API extensions", _L + "api-extensions/", "changelog"),
        Link("Remote API authentication", _L + "authentication/", "auth"),
        Link("Security", _L + "explanation/security/", "auth"),
        Link("How to manage instances", _L + "howto/instances_manage/", "guide"),
        Link("How to install LXD", _L + "installing/", "guide"),
        Link("The LXD snap", "https://snapcraft.io/lxd", "tool"),
        Link("canonical/lxd", "https://github.com/canonical/lxd", "source"),
    ),
    areas=(
        Area("Instances", "containers and virtual machines: made, started, stopped, copied, moved, deleted (/1.0/instances)",
             _lxd("list"), covered="listed with their state, addresses, image, profiles and snapshots; started, stopped, "
                                   "restarted, deleted once confirmed"),
        Area("Snapshots", "an instance's snapshots: taken and restored", _lxd("snapshot"),
             covered="listed; one taken; an instance restored to one (asked twice)"),
        Area("Images", "images and their aliases (/1.0/images)", _lxd("image"), covered="listed"),
        Area("Profiles", "sets of configuration and devices (/1.0/profiles)", _lxd("profile"), covered="listed"),
        Area("Storage", "storage pools and volumes (/1.0/storage-pools)", _lxd("storage"), covered="the pools listed"),
        Area("Networks", "networks, ACLs, forwards, load balancers, peers and zones (/1.0/networks …)", _lxd("network"),
             covered="the managed networks listed"),
        Area("The server", "the daemon's configuration and environment (/1.0)", _lxd("version"),
             covered="the client's version (lxc --version) and the snap's"),
        Area("Projects", "projects (/1.0/projects)", _lxd("project")),
        Area("Cluster", "cluster members and groups (/1.0/cluster)", _lxd("cluster")),
        Area("Authentication and groups", "identities, groups and permissions (/1.0/auth)", _lxd("auth")),
        Area("Placement groups and replicators", "where instances go, and their replication (LXD 7)", _lxd("placement-group")),
        Area("Operations and events", "background operations, the event stream", _lxd("operation")),
        Area("Exec, console and files", "a command run in an instance, its console, its files", _lxd("exec")),
        Area("Warnings, metrics and resources", "the daemon's warnings, its metrics, the host's resources", _lxd("warning")),
    ),
    calls=(
        Call("lxc --version", "the client's version", "The server", used="the software fact", docs=_lxd("version")),
        Call("snap list lxd", "the snap's version, revision and tracking channel", "The server",
             used="the software fact (the snap)", docs="https://snapcraft.io/lxd"),
        Call("lxc list --format json --all-projects", "every instance (GET /1.0/instances?recursion=2)", "Instances",
             used="the Instances and Snapshots tables", docs=_lxd("list")),
        Call("lxc image list --format json", "the images", "Images", used="the Images table", docs=_LM + "image/list/"),
        Call("lxc profile list --format json", "the profiles", "Profiles", used="the Profiles table",
             docs=_LM + "profile/list/"),
        Call("lxc storage list --format json", "the storage pools", "Storage", used="the Storage pools table",
             docs=_LM + "storage/list/"),
        Call("lxc network list --format json", "the networks", "Networks", used="the Networks table",
             docs=_LM + "network/list/"),
        Call("lxc start NAME", "an instance started", "Instances", used="Start…", docs=_lxd("start"), writes=True),
        Call("lxc stop NAME --timeout 60", "an instance shut down cleanly, 60 s at most", "Instances", used="Stop…",
             docs=_lxd("stop"), writes=True),
        Call("lxc restart NAME --timeout 60", "an instance restarted", "Instances", used="Restart…", docs=_lxd("restart"),
             writes=True),
        Call("lxc snapshot NAME [SNAPSHOT]", "a snapshot taken", "Snapshots", used="Snapshot…", docs=_lxd("snapshot"),
             writes=True),
        Call("lxc restore NAME SNAPSHOT", "an instance put back as it was in a snapshot", "Snapshots",
             used="Restore… (asked twice)", docs=_lxd("restore"), writes=True),
        Call("lxc delete NAME", "a stopped instance deleted with its snapshots", "Instances", used="Delete… (asked twice)",
             docs=_lxd("delete"), writes=True),
        Call("lxc launch IMAGE NAME", "an instance made and started", "Instances", docs=_lxd("launch"), writes=True),
        Call("lxc exec NAME -- COMMAND", "a command run in an instance", "Exec, console and files", docs=_lxd("exec"),
             writes=True),
        Call("lxc copy SOURCE TARGET", "an instance copied", "Instances", docs=_lxd("copy"), writes=True),
        Call("lxc monitor", "the daemon's events", "Operations and events", docs=_lxd("monitor")),
        Call("lxc query /1.0", "any REST call, as is", "The server", docs=_lxd("query")),
    ),
    verified=VERIFIED)

LXC_TOOLS = Api(
    name=LXC, maker="Linux Containers (the LXC project)", kind="command",
    about=("Classic LXC: system containers made and run by liblxc's own tools (lxc-create, lxc-start, lxc-attach …), each "
           "container a directory with its configuration file, no daemon in between. The plugin lists root's containers "
           "and their snapshots, starts, stops and restarts them, takes and restores snapshots, and destroys one, as root."),
    base="the lxc-* tools (/usr/bin), as root",
    version="the lxc-* tools as LXC 6.0 LTS and 7.0 ship them (7.0 is the newest release)",
    auth="root (sudo -n when the platform is not); unprivileged containers are their user's own",
    limits="none (local tools)",
    costs="",
    links=(
        Link("LXC manual pages", "https://linuxcontainers.org/lxc/manpages/", "reference"),
        Link("LXC", "https://linuxcontainers.org/lxc/", "docs"),
        Link("Getting started", "https://linuxcontainers.org/lxc/getting-started/", "guide"),
        Link("LXC documentation (liblxc's API)", "https://linuxcontainers.org/lxc/documentation/", "sdk"),
        Link("Security", "https://linuxcontainers.org/lxc/security/", "auth"),
        Link("A container's configuration (lxc.container.conf)",
             "https://linuxcontainers.org/lxc/manpages/man5/lxc.container.conf.5.html", "reference"),
        Link("LXC news (releases)", "https://linuxcontainers.org/lxc/news/", "changelog"),
        Link("lxc/lxc", "https://github.com/lxc/lxc", "source"),
    ),
    areas=(
        Area("Listing", "the containers and their state (lxc-ls)", _lxc("lxc-ls"),
             covered="root's containers with their state, autostart, addresses and whether unprivileged"),
        Area("Starting and stopping", "a container started, stopped, rebooted (lxc-start, lxc-stop)", _lxc("lxc-start"),
             covered="started, stopped (60 s, then hard) and restarted once confirmed"),
        Area("Snapshots", "a container's snapshots taken, listed, restored, destroyed (lxc-snapshot)", _lxc("lxc-snapshot"),
             covered="listed; one taken; a stopped container restored to one (asked twice)"),
        Area("Destroying", "a container and its snapshots destroyed (lxc-destroy)", _lxc("lxc-destroy"),
             covered="a stopped container destroyed with its snapshots (asked twice)"),
        Area("Making", "a container made from a template (lxc-create), copied (lxc-copy)", _lxc("lxc-create")),
        Area("Inside a container", "a command run in it (lxc-attach), its console (lxc-console)", _lxc("lxc-attach")),
        Area("Information and monitoring", "a container's state and figures (lxc-info, lxc-top, lxc-monitor)", _lxc("lxc-info")),
        Area("Freezing", "a container's processes frozen and thawed (lxc-freeze, lxc-unfreeze)", _lxc("lxc-freeze")),
        Area("Configuration and checks", "lxc-config, lxc-checkconfig, lxc-update-config, lxc-cgroup", _lxc("lxc-checkconfig")),
        Area("Autostart", "the containers started at boot (lxc-autostart)", _lxc("lxc-autostart")),
    ),
    calls=(
        Call("lxc-ls --version", "the tools' version", "Listing", used="the software fact", docs=_lxc("lxc-ls")),
        Call("lxc-ls --fancy", "the containers with their state, addresses, autostart and kind", "Listing",
             used="the Containers table", docs=_lxc("lxc-ls")),
        Call("lxc-ls -1", "the containers' names, one a line", "Listing", used="each container's snapshots read",
             docs=_lxc("lxc-ls")),
        Call("lxc-snapshot -n NAME -L", "a container's snapshots", "Snapshots", used="the Snapshots table",
             docs=_lxc("lxc-snapshot")),
        Call("lxc-start -n NAME", "a container started", "Starting and stopping", used="Start…", docs=_lxc("lxc-start"),
             writes=True),
        Call("lxc-stop -n NAME -t 60", "a container stopped, killed after 60 s", "Starting and stopping", used="Stop…",
             docs=_lxc("lxc-stop"), writes=True),
        Call("lxc-stop -n NAME -r -t 60", "a container rebooted", "Starting and stopping", used="Restart…",
             docs=_lxc("lxc-stop"), writes=True),
        Call("lxc-snapshot -n NAME", "a snapshot taken (named by LXC: snap0, snap1 …)", "Snapshots", used="Snapshot…",
             docs=_lxc("lxc-snapshot"), writes=True),
        Call("lxc-snapshot -n NAME -r SNAPSHOT", "a stopped container restored to a snapshot", "Snapshots",
             used="Restore… (asked twice)", docs=_lxc("lxc-snapshot"), writes=True),
        Call("lxc-destroy -n NAME -s", "a container destroyed with its snapshots", "Destroying", used="Delete… (asked twice)",
             docs=_lxc("lxc-destroy"), writes=True),
        Call("lxc-create -n NAME -t TEMPLATE", "a container made from a template", "Making", docs=_lxc("lxc-create"),
             writes=True),
        Call("lxc-attach -n NAME -- COMMAND", "a command run in a container", "Inside a container", docs=_lxc("lxc-attach"),
             writes=True),
        Call("lxc-info -n NAME", "a container's state, PID, addresses and use", "Information and monitoring",
             docs=_lxc("lxc-info")),
        Call("lxc-freeze -n NAME", "a container's processes frozen", "Freezing", docs=_lxc("lxc-freeze"), writes=True),
    ),
    verified=VERIFIED)

LINUX_SERVICES = Api(
    name=SERVICES, maker="systemd, OpenRC, sudo (their projects)", kind="command",
    about=("What the plugin asks of the Linux machine itself, as the platform's user: the init system (systemd, OpenRC or "
           "SysV scripts) and the state of the daemons' services and sockets, the processes running, the user's groups, "
           "the distribution; and root, only through sudo -n (never a password asked). A daemon's service is started only "
           "when asked, never stopped; an idle one (a socket listening) is not connected to unless asked, since that "
           "starts it."),
    base="sh -s as the platform's user: systemctl, rc-service, service, pgrep, id, /etc/os-release; sudo -n for root",
    version="whichever is PID 1: systemd, OpenRC (Gentoo, Alpine) or SysV init scripts (Devuan)",
    auth="the platform's user; root through sudo -n alone (a refusal says how the rights are given)",
    limits="none (local)",
    costs="",
    links=(
        Link("systemctl", _SYSTEMCTL, "reference"),
        Link("systemd.socket (a daemon started when a client connects)",
             "https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html", "reference"),
        Link("OpenRC user guide", _OPENRC, "docs"),
        Link("OpenRC (Gentoo wiki)", "https://wiki.gentoo.org/wiki/OpenRC", "guide"),
        Link("Incus on Gentoo", "https://wiki.gentoo.org/wiki/Incus", "guide"),
        Link("LXD on Gentoo", "https://wiki.gentoo.org/wiki/LXD", "guide"),
        Link("LXC on Gentoo", "https://wiki.gentoo.org/wiki/LXC", "guide"),
        Link("sudo (its -n)", "https://www.sudo.ws/docs/man/sudo.man/", "auth"),
        Link("os-release", "https://www.freedesktop.org/software/systemd/man/latest/os-release.html", "reference"),
        Link("OpenRC/openrc", "https://github.com/OpenRC/openrc", "source"),
    ),
    areas=(
        Area("systemd units", "services and sockets: their load, active and file states", _SYSTEMCTL,
             covered="the daemons' units read (incus.service, its socket, snap.lxd.daemon, lxcfs, lxc …) and one started"),
        Area("OpenRC services", "the services' state (/run/openrc) and runlevels (/etc/runlevels)", _OPENRC,
             covered="state and runlevels read; one started (rc-service NAME start)"),
        Area("SysV scripts", "/etc/init.d scripts asked their status", "https://wiki.gentoo.org/wiki/OpenRC",
             covered="status read; one started (service NAME start)"),
        Area("Processes", "whether a daemon runs (pgrep)", "https://man7.org/linux/man-pages/man1/pgrep.1.html",
             covered="incusd and lxd looked for"),
        Area("Users and groups", "the user and its groups (id)", "https://man7.org/linux/man-pages/man1/id.1.html",
             covered="the platform's user, its groups now and in the group database (incus-admin, incus, lxd)"),
        Area("The distribution", "/etc/os-release", "https://www.freedesktop.org/software/systemd/man/latest/os-release.html",
             covered="the distribution named, and how it installs Incus, LXD or LXC"),
        Area("Root through sudo", "a command run as root, -n: never asking a password", "https://www.sudo.ws/docs/man/sudo.man/",
             covered="sudo -n true asked (kept 5 minutes), then the reads and changes as root"),
        Area("Enabling at boot", "a service enabled (systemctl enable, rc-update add)", _SYSTEMCTL),
    ),
    calls=(
        Call("systemctl show -p Id,LoadState,ActiveState,SubState,UnitFileState,Description UNITS", "the units' states",
             "systemd units", used="the Daemons table", docs=_SYSTEMCTL),
        Call("systemctl start UNIT", "a unit started (as root)", "systemd units", used="Start… a daemon's service",
             docs=_SYSTEMCTL, writes=True),
        Call("rc-service NAME start", "an OpenRC service started (as root)", "OpenRC services", used="Start… under OpenRC",
             docs=_OPENRC, writes=True),
        Call("service NAME status", "a SysV script's status", "SysV scripts", used="the Daemons table (SysV)"),
        Call("service NAME start", "a SysV script started (as root)", "SysV scripts", used="Start… under SysV scripts",
             writes=True),
        Call("pgrep -x DAEMON", "whether incusd or lxd runs", "Processes", used="the daemon fact",
             docs="https://man7.org/linux/man-pages/man1/pgrep.1.html"),
        Call("id -nG", "the user's groups", "Users and groups", used="the rights fact",
             docs="https://man7.org/linux/man-pages/man1/id.1.html"),
        Call("sudo -n true", "whether root is had without a password", "Root through sudo", used="the rights fact",
             docs="https://www.sudo.ws/docs/man/sudo.man/"),
        Call("systemctl stop UNIT", "a unit stopped", "systemd units", docs=_SYSTEMCTL, writes=True),
        Call("systemctl enable --now UNIT", "a unit enabled at boot and started", "Enabling at boot", docs=_SYSTEMCTL,
             writes=True),
        Call("rc-update add NAME default", "an OpenRC service added to the default runlevel", "Enabling at boot",
             docs=_OPENRC, writes=True),
    ),
    verified=VERIFIED)

APIS = (INCUS_API, LXD_API, LXC_TOOLS, LINUX_SERVICES)
