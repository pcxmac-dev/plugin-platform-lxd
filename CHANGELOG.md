# Changelog

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
