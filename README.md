# plugin-platform-lxd

LXC / LXD for Liminal: Incus, LXD or classic LXC on the native Linux machine the platform runs on, its containers
and virtual machines, snapshots, images, profiles, storage pools, networks and the daemon's services; changed only
when asked.

The user's words (2026-11-08): "LXC/LXD plugins and LIBVIRT plugin for virtualization"; of the Linux plugins: "make
sure its compatable with systemd/openrc ... make sure its compatible with major distributions and gentoo linux"; "the
scope of linux administration is only on native linux O/S"; "it's not for administrating WSL instances".

## What it does

A machine plugin of the platform (`liminal_libs.machines.MachinePlugin`, plugin API 2.1, its icon LXD's logo: `lxd`,
a cube of small cubes, in the library's icons.js since 1.33.0), through the library's `machines.linux.Shell` on this
machine:

- **Which**: Incus (LXD's community fork, `incus`), LXD (its client `lxc`, from the snap or packages) or classic LXC
  (`lxc-ls`, `lxc-start`...), whichever is installed; Incus first, then LXD, then LXC, the others named among the
  facts (a machine `lxd-to-incus` moved keeps both).
- **Read** through the client's JSON (`incus list --format json --all-projects`, `image list`, `profile list`,
  `storage list`, `network list`): the instances (containers and virtual machines: their state, IPv4 addresses,
  image, profiles, project), their snapshots (taken, stateful, expiry), the images, the profiles and their devices, the
  storage pools, the networks Incus or LXD manages (the machine's own interfaces named apart). Classic LXC: root's
  containers through `lxc-ls --fancy` (state, start at boot, IPv4, unprivileged) and each one's `lxc-snapshot -L`.
- **Changed** only when asked, through the platform page's confirmed forms or the command line:
  - start, stop (a clean shutdown, waited for 60 s; classic LXC stops it hard after that), restart;
  - a snapshot taken (named, or the next free name: snap0, snap1...; classic LXC names its own);
  - a snapshot restored: asked a second time, since what changed since the snapshot is lost (`accept_restore`);
  - a stopped instance deleted with its snapshots: asked a second time (`accept_delete`).
- **The daemon's services** with their state and whether they start at boot (Daemons), each stopped one started only
  when asked (Start…), none ever stopped:

  | | systemd | OpenRC / SysV scripts |
  |---|---|---|
  | Incus | `incus.service`, `incus.socket`, `incus-startup.service`, `incus-user.socket`, `incus-lxcfs.service` | `incus` (Gentoo), `incusd` (Alpine), `incus-user` |
  | LXD | `snap.lxd.daemon.service` and `.unix.socket` (the snap), `lxd.service`, `lxd.socket`, `lxd-containers.service` | `lxd` (Gentoo, Alpine) |
  | LXC | `lxc.service`, `lxc-net.service`, `lxc-monitord.service` | `lxc-net`, `lxc-containers` (Gentoo), `lxc` (SysV) |
  | all | `lxcfs.service` | `lxcfs` |

  The init system is told as the library's services scan tells it (systemd when `/run/systemd/system` is there;
  OpenRC with `rc-status` and `/run/openrc` or openrc-init as PID 1; else SysV scripts through `service`). OpenRC's
  state is read from `/run/openrc` and its runlevels from `/etc/runlevels`, as the platform's user.
- **Nothing is started by a read.** An idle daemon (systemd's socket listening, the daemon not running: a socket
  start) would be started by the first client, so it is read only once asked (**Read now…**), and from then on until
  the platform lets the plugin go. A stopped daemon is said as the problem, with Start… beside its service.

## The rights

- In `incus-admin` (Incus) or `lxd` (LXD): every command as the platform's user, no sudo. That group's members drive
  the daemon fully (root's equal: it can mount the host's paths), as Incus and LXD say.
- The platform run as root: plainly.
- Otherwise as root through `sudo -n` (never a password asked), when sudo allows it without one.
- Incus's `incus` group without `incus-admin`: the user's own project only (incus-user).
- None of these: not valid, and the status says how to give them: `sudo usermod -aG incus-admin USER` (or `lxd`) and a
  new login, so the platform runs with the group; or the platform as root; or sudo without a password. A group given
  after this login is named as such: the platform has it once started from a new login.
- Starting a service needs root (`systemctl start`, `rc-service ... start`, `service ... start`): through `sudo -n`
  when the platform is not root; refused, it says the command to run.
- Classic LXC's containers are root's: root, or `sudo -n`.

## Distributions

Not installed, the status says how this distribution installs it (from `/etc/os-release`'s ID and ID_LIKE): Gentoo
(`emerge --ask app-containers/incus`, or `app-containers/lxd`, `app-containers/lxc`; `rc-update add incus default`
under OpenRC, `systemctl enable --now incus` under systemd), Ubuntu (`apt install incus` from 24.04, or `snap install
lxd`) and its derivatives, Debian (`apt install incus` on 13), Fedora (`dnf install incus`), RHEL and its rebuilds
(the neelc/incus COPR, or LXD's snap), Arch (`pacman -S incus`), openSUSE (`zypper install incus`), Alpine (`apk add
incus incus-client`, `rc-update add incusd`), NixOS (`virtualisation.incus.enable`), Void (`xbps-install incus`). The
snap's client is found in `/snap/bin` too.

## Native Linux only

On Windows, on macOS and inside a WSL distribution (its kernel's release names Microsoft or WSL, or WSL's variables
are set) it runs no command at all: its status says why with `applies` False, so the platform shows it as not for
this machine and offers no setup; every hook is refused. The check is the library's (`machines.linux.why_not_native`).

## Its hooks

`present`, `status` (the facts; `tables`: Instances or Containers, Snapshots, Images, Profiles, Storage pools,
Networks, Daemons; `forms`; `actions`), `power(name, action, project)` (`start`, `stop`, `restart`),
`snapshot(name, snapshot, project)`, `restore(name, snapshot, project, accept_restore)`, `delete(name, project,
accept_delete)`, `service(unit, action)` (`start` only), `wake()` (Read now), `run(*args)` (the client with these
arguments, or one of classic LXC's `lxc-*` tools). A refusal (a bad name, a running instance to delete, a change not
accepted yet) is a `ValueError` naming the argument that accepts it, which the page asks as a second confirmation.

## From the command line

```
python -m plugin_platform_lxd status            # the status as JSON; --wake reads an idle daemon too
python -m plugin_platform_lxd wake
python -m plugin_platform_lxd stop web
python -m plugin_platform_lxd restart ci-runner --project dev
python -m plugin_platform_lxd snapshot web before-upgrade
python -m plugin_platform_lxd restore web before-upgrade --yes
python -m plugin_platform_lxd delete old-test --yes
python -m plugin_platform_lxd service incus.service start
python -m plugin_platform_lxd run -- info web
```

Restoring and deleting are refused until asked again with `--yes`, as the page asks a second time.
