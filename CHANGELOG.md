# Changelog

## 0.2.0 (2026-11-11)

Its API on its page (plugin API 2.2, liminal-libs 1.49.0; the user: "each plugin should reflect the the API its attached
to, and present links that provide documentation, the plugin interface should more or less contain what it already has,
BUT, it should also expose more of what the API is about"). Needs liminal-libs>=1.49.0.

- `apis` (apis.py): four cards, read on linuxcontainers.org, canonical.com/lxd, freedesktop.org, man7.org and the
  projects' GitHub on 2026-11-11. **Incus** and **LXD**: each its documentation, command reference, REST API overview
  and specification, API extensions, authentication and security, its install page and its releases; every part of the
  REST API (instances, snapshots, images, profiles, storage, networks, the server used; projects, clusters, trust or
  auth, operations and events, exec and files, warnings and metrics not), and each command the plugin runs beside the
  REST call it is (`list --format json --all-projects`, the four other listings, start, stop, restart, the snapshots,
  delete), with the notable others (launch, exec, copy, config, monitor, query). **LXC (classic)**: its manual pages,
  liblxc's documentation, a container's configuration; the ten lxc-* commands it runs and the tools it does not.
  **Linux services and rights (systemd, OpenRC, sudo)**: systemctl, socket activation, OpenRC, Gentoo's pages, sudo -n,
  os-release; the probe's systemctl show, service status, pgrep and id, sudo -n true, and the three ways a daemon's
  service is started (systemctl, rc-service, service), none ever stopped.
- `api_facts()`: what its last look and read said, asking nothing (kept in one dict its copies share, forgotten when
  let go): the software and its version (the snap's too), the daemon and the rights, the last read's counts and
  troubles; the init system, the distribution, the daemons' services and whether sudo -n answered.
- Tests: the cards are sound; they name every command the plugin runs (the probe's and the read's as their scripts
  write them, each change's as the stand-in machine was given it, under systemd, OpenRC and SysV scripts, as a member of
  incus-admin and through sudo -n) and no other; the facts ask nothing; with LIMINAL_VENDOR_PAGES=1, every address on
  the cards answers (all did on 2026-11-11).

## 0.1.0 (2026-11-08)

The first release (the user: "LXC/LXD plugins and LIBVIRT plugin for virtualization"; of the Linux plugins: "make sure
its compatable with systemd/openrc ... make sure its compatible with major distributions and gentoo linux"), on
liminal-libs 1.33.0 (its icon, LXD's logo: `lxd` in icons.js):

- Incus, LXD (the snap or packages) or classic LXC, whichever this machine has (Incus first, then LXD, then LXC; the
  others named): its instances (containers and virtual machines: state, IPv4, image, profiles, every project's),
  snapshots, images, profiles, storage pools and networks, read through the client's `--format json`; classic LXC's
  containers through `lxc-ls --fancy` and `lxc-snapshot -L`.
- Changes through the platform page's confirmed forms (and the command line): start, stop (a clean shutdown, waited
  for 60 s), restart; a snapshot taken; a snapshot restored and a stopped instance deleted, each asked a second time
  (`accept_restore`, `accept_delete`; `--yes`).
- The daemon's services under systemd, OpenRC (Gentoo's `incus`, Alpine's `incusd`) or SysV scripts, with their state
  and whether they start at boot; a stopped one started only when asked (Start…), none ever stopped. An idle daemon
  (systemd's socket listening, the daemon not running) is not read until asked (Read now…), since connecting starts it.
- The rights: commands as the platform's user when it is in `incus-admin` (Incus) or `lxd` (LXD), plainly as root,
  else through `sudo -n`; otherwise not valid, with how to give them (the group and a new login, root, or sudo
  without a password). Incus's `incus` group: the user's own project only.
- Not installed: how this distribution installs it (Gentoo's emerge and rc-update, Ubuntu's snap or apt, Debian,
  Fedora, RHEL's COPR, Arch, openSUSE, Alpine, NixOS, Void).
- Native Linux only (the library's `machines.linux.why_not_native`): on Windows, macOS or in WSL it runs nothing, its
  status says `applies` False, and every hook is refused.
