# Changelog

## 0.4.1 (2026-11-25)

On liminal-libs 1.95.0's hooks (SERVICE-INTERFACE.md §9.2); needs liminal-libs>=1.95.0.
- **The stack as every container plugin names it** (`containers.stack_of`, decided 2026-11-25): "wsl" inside a WSL
  distribution (every Linux there shares WSL 2's one VM; the Linux platform's firewall-linux there owns "wsl"),
  "linux" on a Linux machine of its own, in place of a fixed "linux".
- **Volumes made for their spec** (`make_volume_for`) in both runtimes: `initial.uid`/`initial.gid` from the volume's
  owner. Inside liminal-oci the custom volume takes them where the daemon lists `storage_volumes_initial_values`; one
  found from before, or on a daemon without it, is chowned once inside as before. The runtimes keep no owners of
  their own any more.
- **Snapshots as the library's backup kind**: incus-oci answers `backup_kind` "snapshot", so an image without tar
  needs no "no backup" accept; `backup` answers the snapshot's name (`liminal-<stamp>`), which the base keeps in
  `<volume>.snapshot.json`, and `restore` reads it back (a 0.4.0 backup's own record still read); no "N bytes" words.
- **Who can read a secret**: each runtime's `kept_secret_words` in place of the plugin's `_kept_secret_words`.
- **lxd-docker's networks behind liminal-oci**: `network_want` answers `{"behind": <liminal-oci's address on Incus'
  bridge>}`, so the firewall routes its bridges through liminal-oci; `segments()` lists that bridge with liminal-oci a
  found member (sid "", assigned "incus").
- Not changed: its page reads no targets of its own for its status (the base's reads them), so there is nothing to
  hand `service_status(targets=...)`.

## 0.4.0 (2026-11-25)

LXC / LXD hosts Liminal's services: liminal-libs 1.93.0's one "run a service" interface (`ContainerHost`) on the Incus
or LXD of the Linux the platform runs on. service-containers-design §4.4 and §4.5, the networking design's §5.6 and
§5.7, decision 15 (a) and N25 (a). Needs liminal-libs>=1.93.0.
- **Two mechanisms** (`services.py`). `incus-oci` where the daemon lists `instance_oci` and skopeo and umoci are here:
  each part an Incus instance from its OCI image, fetched by its digest through the registry's OCI remote
  (`liminal-docker`, added once inside the first approved plan that needs it), made with `incus init IMAGE NAME` and
  its whole configuration on standard input (`environment.*` with its secrets, `user.liminal.*`, `limits.memory`,
  `boot.autostart=false` said outright), custom volumes in the default profile's pool (`initial.uid` for the volume's
  owner), seeds and readable files through `incus storage volume file`, a proxy device per port (every address, or
  127.0.0.1 alone while the port is closed to your network), its console log, an update's backup as a snapshot of each
  custom volume (the last two kept), and the previous instance letting its pinned address go until named back.
  Otherwise `lxd-docker`: Docker inside one system container, `liminal-oci`, through the library's `DockerRuntime` and
  `incus exec liminal-oci --mode=non-interactive -- docker` (LXD's `lxc exec`), a secret's value crossing on standard
  input (`sh -c` reads NAME=value lines, then runs docker), each service volume a custom volume attached to
  liminal-oci at `/srv/liminal/<volume>` and bound into the container (made writable for the image's user), each
  published port a proxy device on liminal-oci.
- **A Liminal bridge per segment** (N25 a): `incus network create lim-<11 hex>` with the subnet the firewall plugin hands
  out, NAT, DHCP from .128, no IPv6, labelled `user.liminal.*`; each part's address pinned by its NIC. Docker's
  networks inside liminal-oci are said as nested segments.
- **Set up for services…** (`setup_oci`, a job with its Cancel): liminal-oci from `images:debian/13` (Incus) or
  `ubuntu:24.04` (LXD), nesting on, `boot.autostart=false`, Docker from Docker's own repository; asked twice
  (`accept_download`, its sizes said first); refused where Incus runs OCI images itself, and when liminal-oci is there.
- **Its target**, one per machine: ready once the daemon runs and its mechanism answers. An idle daemon is "idle" (never
  woken), a stopped one "stopped", liminal-oci stopped "stopped", missing "not answering" (pointing at Set up for
  services…), no rights "no rights", classic LXC "not answering" (not offered: no proxy devices or custom volumes); on
  Windows or macOS "not here", asking nothing. Inside WSL its stack stays the Linux platform's, its ports meeting your
  network through WSL's NAT. Its page reads the services only once a service plugin asked, a grant, plan or work is
  held, or an instance of Liminal's is listed: until then it asks nothing more than before.
- **Its own changes guarded**: Delete… and Restore… of a service's instance are refused, pointing at its owner's page;
  liminal-oci is neither deleted nor restored while it holds a service, or while that cannot be read. Its own change
  hooks (power, snapshot, restore, delete, service, wake, setup_oci) are its page's alone (PAGE_ONLY_HOOKS).
- **The runner**: `services.service_run` refuses an environment (a value goes on standard input, or not at all); a
  test's plain stand-in shell has every service command refused rather than run for real.
- Its API cards name every command a service's plans, liminal-oci's setup and the services' read run.
- Tests: `test_services.py` (FakeIncus beside the library's FakeDocker and stand-in firewall, attached to a real
  apps.App): the conformance sequence on both mechanisms, the exact configuration on standard input, the canary secret,
  the guards, idle and stopped daemons, classic LXC, Windows; each of its 26 gates taken out once made a test fail.

## 0.3.0 (2026-11-16)

It works on any Linux the platform runs on, a WSL distribution included (the user, 2026-11-15: "wsl and firewall linux should always work under linux, if wsl prevents or misses some capability this should reflect in the plugin"). With
liminal-libs 1.57.0 the library's check no longer refuses a WSL kernel, so the plugin applies there; its words and its
tests still said "native Linux machine" and pinned the refusal.

- Inside a WSL distribution the page ends with an **Under WSL** table (`under_wsl`): what WSL changes for
  containers and virtual machines (KVM and nested virtualization, systemd, WSL 1, and that the distribution runs only while Windows
  keeps it started). It is there when the software is not installed yet, too.
- On Windows and macOS nothing changes: no command runs, `applies` is False, every hook is refused. A platform on
  Windows still never reaches into a WSL distribution from outside.
- The tests pass on the library before 1.57.0 and after it.

## 0.2.2 (2026-11-15)

Drawn under the platform's Virtualization plugins section, in the list on the left (the user: "platform needs a virtualization plugins section"; "just like cloud plugins"; "docker, libvirt, kubernetes, lxc should be in virtualization"): `SECTION = "virtualization"`, liminal-libs 1.51.0's machine-plugin attribute. liminal-platform 0.26.0 draws it; an older platform keeps the plugin under Machine plugins. Nothing else changes.

## 0.2.1 (2026-11-11)

Another backend's last read no longer stays on the card when the look finds a different one (the audit before the cloud plugins' keys).

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
