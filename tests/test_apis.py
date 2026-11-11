"""What the plugin attaches to (plugin API 2.2; the user, 2026-11-11: "each plugin should reflect the the API its attached
to, and present links that provide documentation"): its cards are sound; they name every command the plugin runs (the
probe's and the read's as their scripts write them, each change's as the stand-in machine was given it, under systemd,
OpenRC and SysV scripts) and none it does not; its facts ask nothing; and, with LIMINAL_VENDOR_PAGES=1 alone, every
address on them answers (the only test here that reaches the network)."""
from __future__ import annotations

import os
import re

import pytest

import plugin_platform_lxd as lx
from liminal_libs.machines import linux as linux_mod
from liminal_libs.sources import apis as A
from liminal_libs.sources.conformance import problems
from plugin_platform_lxd import apis as cards
from plugin_platform_lxd import inventory, system
from test_plugin import FakeLinux, plugin

TWO = {cards.INCUS: {"image", "profile", "storage", "network", "snapshot", "config", "cluster", "project"},
       cards.LXD: {"image", "profile", "storage", "network", "config", "cluster", "project"}}
LXC_FLAGS = {"-L", "-r", "--fancy", "-1", "--version"}  # what tells one lxc-* command from another of the same tool


def shape(api: str, words) -> str:
    """A command by what it does, its instance, snapshot, unit, project and timeout left out: snapshot create, lxc-stop -r,
    systemctl start, rc-service start."""
    words = list(words)
    if words[:2] == ["sudo", "-n"] and words[2:] != ["true"]:
        words = words[2:]  # root through sudo -n: the command itself
    tool = words[0].rsplit("/", 1)[-1]
    rest = words[1:]
    if api in TWO:
        if rest[:1] == ["--version"]:
            return "--version"
        return " ".join(rest[:2]) if rest[0] in TWO[api] else rest[0]
    if api == cards.LXC:
        return " ".join([tool, *sorted(w for w in rest if w in LXC_FLAGS)])
    if tool in ("rc-service", "service"):
        return f"{tool} {rest[-1]}"
    if tool in ("systemctl", "rc-update"):
        return f"{tool} {rest[0]}"
    return " ".join(words) if tool == "sudo" else tool


def card(name: str) -> A.Api:
    return next(a for a in lx.MACHINE.apis if a.name == name)


def claimed(name: str) -> set[str]:
    api = card(name)
    return {shape(name, c.name.split()) for c in api.used if not c.name.startswith("snap ")}


class Stopped(FakeLinux):
    """The stand-in with its instances stopped, so that a delete and classic LXC's restore are asked."""

    def read(self) -> str:
        return super().read().replace('"status":"Running"', '"status":"Stopped"')

    def lxc_read(self) -> str:
        return super().lxc_read().replace("buildbox RUNNING", "buildbox STOPPED")


def changes_of(fake, client: str) -> list[list[str]]:
    return [c for c in fake.changes if c[0] == client or c[0].startswith("lxc-")]


def test_its_cards_are_sound_and_written_for_plugin_api_2_2():
    p = lx.MACHINE
    assert p.api == "2.2" and problems(p) == [] and A.problems(p.apis) == []
    assert [a.name for a in p.apis] == [cards.INCUS, cards.LXD, cards.LXC, cards.SERVICES]
    for a in p.apis:
        assert a.verified == "2026-11-11" and a.docs.startswith("https://") and a.used and a.covered
        assert all(c.area in {x.name for x in a.areas} for c in a.calls), a.name
        assert len(a.areas) > len(a.covered) and len(a.calls) > len(a.used)  # more of the API than the plugin uses


def test_the_incus_card_names_every_command_the_plugin_runs():
    fake = FakeLinux()
    p = plugin(fake)
    p.status()
    p.power("web", "stop")
    p.power("old-test", "start")
    p.power("ci-runner", "restart", "dev")
    p.snapshot("web", "nightly")
    p.restore("web", "snap0", accept_restore=True)
    p.delete("db", accept_delete=True)
    ran = {shape(cards.INCUS, c) for c in changes_of(fake, "/usr/bin/incus")}
    ran |= {shape(cards.INCUS, ["incus", *m.split()]) for m in re.findall(r"'?/usr/bin/incus'? ([^;]*?) 2>&1",
                                                                            inventory.read_script("/usr/bin/incus"))}
    assert '"$p" --version' in system.PROBE and "incus" in system.TOOLS  # each tool's version, the probe's
    ran.add("--version")
    assert ran == claimed(cards.INCUS), ran ^ claimed(cards.INCUS)


def test_the_lxd_card_names_every_command_the_plugin_runs():
    fake = Stopped("lxd", os="ubuntu", units="lxd", groups=("ciara", "lxd"), daemons=("lxd",),
                   sockets=("/var/snap/lxd/common/lxd/unix.socket",))
    p = plugin(fake)
    p.status()
    p.power("noble", "start")
    p.power("noble", "stop")
    p.power("noble", "restart")
    p.snapshot("noble", "pre-release")
    p.restore("noble", "snap0", accept_restore=True)
    p.delete("noble", accept_delete=True)
    ran = {shape(cards.LXD, c) for c in changes_of(fake, "/snap/bin/lxc")}
    ran |= {shape(cards.LXD, ["lxc", *m.split()]) for m in re.findall(r"'?/snap/bin/lxc'? ([^;]*?) 2>&1",
                                                                       inventory.read_script("/snap/bin/lxc"))}
    ran.add("--version")
    assert ran == claimed(cards.LXD), ran ^ claimed(cards.LXD)
    assert "snap list lxd" in system.PROBE and any(c.name == "snap list lxd" for c in card(cards.LXD).used)


def test_the_lxc_card_names_every_tool_the_plugin_runs():
    fake = Stopped("lxc", os="debian", units="lxc", groups=("ciara", "sudo"), daemons=(), sockets=())
    p = plugin(fake)
    p.status()
    p.power("legacy", "start")
    p.power("buildbox", "stop")
    p.power("buildbox", "restart")
    p.snapshot("buildbox")
    p.restore("buildbox", "snap0", accept_restore=True)
    p.delete("legacy", accept_delete=True)
    ran = {shape(cards.LXC, c) for c in fake.changes}
    ran |= {shape(cards.LXC, m.replace('"$n"', "NAME").split()) for m in re.findall(r"(?<!liminal-)(lxc-[a-z]+ [^;)\n]*?)(?: 2>|\)|;|$)",
                                                                                    inventory.LXC_SCRIPT, re.M)}
    ran.add("lxc-ls --version")  # the probe's: "$p" --version for lxc-ls
    assert ran == claimed(cards.LXC), ran ^ claimed(cards.LXC)


def test_the_services_card_names_what_is_asked_of_the_machine(monkeypatch):
    stopped = FakeLinux(units="incus-stopped", daemons=(), sockets=())
    plugin(stopped).service("incus.service", "start")
    monkeypatch.setattr(linux_mod.os, "geteuid", lambda: 0, raising=False)  # as root: rc-service and service as they are
    gentoo = FakeLinux(os="gentoo", init="openrc", rc=["incus stopped -"], daemons=(), sockets=())
    plugin(gentoo).service("incus", "start")
    devuan = FakeLinux(init="sysvinit", rc=["incus 3"], daemons=(), sockets=())
    plugin(devuan).service("incus", "start")
    monkeypatch.setattr(linux_mod.os, "geteuid", lambda: 1000, raising=False)
    outsider = FakeLinux(groups=("ciara", "wheel"))  # not in incus-admin: root's commands through sudo -n, asked first
    plugin(outsider).status()
    ran = {shape(cards.SERVICES, c) for f in (stopped, gentoo, devuan, outsider) for c in f.calls
           if c[:2] != ["sh", "-c"] and c[2:4] != ["sh", "-c"]}
    for words, written in (("systemctl show", "systemctl show -p Id,LoadState,ActiveState,SubState,UnitFileState,Description"),
                           ("service status", 'service "$n" status'), ("pgrep", 'pgrep -x "$d"'), ("id", "id -nG")):
        assert written in system.PROBE, written  # the probe's, as its script writes them
        ran.add(words)
    assert ran == claimed(cards.SERVICES), ran ^ claimed(cards.SERVICES)
    writes = {shape(cards.SERVICES, c.name.split()) for c in card(cards.SERVICES).used if c.writes}
    assert writes == {"systemctl start", "rc-service start", "service start"}  # started when asked, never stopped
    assert not any(c.costs for a in lx.MACHINE.apis for c in a.calls)


def test_its_facts_say_what_it_learned_and_ask_nothing():
    assert lx.LxdPlugin(native="").api_facts() == {}  # nothing looked at yet: nothing asked
    fake = FakeLinux()
    p = plugin(fake)
    p.status()
    before = len(fake.calls)
    facts = A.facts_of(p)
    assert len(fake.calls) == before  # asked nothing to answer
    assert facts[cards.INCUS]["Version"] == "6.0.4" and facts[cards.INCUS]["Daemon"] == "running (incus.service)"
    assert facts[cards.INCUS]["Read"].endswith("networks") and "running)" in facts[cards.INCUS]["Read"]
    assert facts[cards.SERVICES]["Init system"].startswith("systemd") and "incus.service running" in facts[cards.SERVICES]["Services"]
    p.detach()
    assert p.api_facts() == {}  # let go: what it learned goes


@pytest.mark.skipif(os.environ.get("LIMINAL_VENDOR_PAGES") != "1", reason="reaches the makers' pages: LIMINAL_VENDOR_PAGES=1")
def test_every_address_on_its_cards_answers(monkeypatch):
    monkeypatch.undo()  # this test alone reaches the network (conftest refuses it to every other)
    assert A.dead_links(lx.MACHINE.apis) == []
