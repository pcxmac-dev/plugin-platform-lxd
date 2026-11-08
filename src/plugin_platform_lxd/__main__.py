"""`python -m plugin_platform_lxd ...`: the plugin from the command line (the platform app drives the same hooks from
its page). `status` prints the status as JSON (`--wake` reads an idle daemon too, which starts it); a change is made
at once, but restoring a snapshot and deleting an instance are refused until asked again with `--yes`, as the page asks
a second time."""
from __future__ import annotations

import argparse
import json
import sys

from liminal_libs.machines.plugin import CommandError

from . import MACHINE, Refusal

USAGE = ("python -m plugin_platform_lxd status [--wake] | wake | start|stop|restart NAME [--project P] "
         "| snapshot NAME [SNAPSHOT] [--project P] | restore NAME SNAPSHOT [--project P] [--yes] "
         "| delete NAME [--project P] [--yes] | service UNIT start | run -- ARGS...")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m plugin_platform_lxd", description="LXC / LXD on this native Linux machine")
    sub = p.add_subparsers(dest="what")
    s = sub.add_parser("status", help="the status as JSON")
    s.add_argument("--wake", action="store_true", help="read an idle daemon too (connecting starts it)")
    sub.add_parser("wake", help="read an idle daemon now (connecting starts it)")
    for verb in ("start", "stop", "restart"):
        v = sub.add_parser(verb, help=f"{verb} an instance")
        v.add_argument("name")
        v.add_argument("--project")
    s = sub.add_parser("snapshot", help="take a snapshot of an instance")
    s.add_argument("name")
    s.add_argument("snapshot", nargs="?")
    s.add_argument("--project")
    r = sub.add_parser("restore", help="restore an instance to a snapshot (asked again with --yes)")
    r.add_argument("name")
    r.add_argument("snapshot")
    r.add_argument("--project")
    r.add_argument("--yes", action="store_true", help="what changed since the snapshot is lost: go on anyway")
    d = sub.add_parser("delete", help="delete a stopped instance (asked again with --yes)")
    d.add_argument("name")
    d.add_argument("--project")
    d.add_argument("--yes", action="store_true", help="its disk and snapshots are gone for good: go on anyway")
    v = sub.add_parser("service", help="start one of the daemon's services")
    v.add_argument("unit")
    v.add_argument("action", choices=("start",))
    x = sub.add_parser("run", help="the client with these arguments (incus ..., lxc ...), or an lxc-* tool")
    x.add_argument("args", nargs=argparse.REMAINDER)
    return p


def said(answer: dict) -> int:
    print(answer.get("said", "done"))
    for line in answer.get("lines") or []:
        print(f"  {line}")
    return 0 if answer.get("ok", True) else 1


def main(argv: list[str] | None = None, machine=None) -> int:
    m = machine if machine is not None else MACHINE
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["status"]
    try:
        args = parser().parse_args(argv)
    except SystemExit as e:  # argparse's own usage message is printed
        return 2 if e.code else 0
    try:
        if args.what == "status":
            if args.wake:
                m.wake()
            st = m.status()
            print(json.dumps(st, indent=1))
            return 0 if st.get("valid") else 1
        if args.what == "wake":
            return said(m.wake())
        if args.what in ("start", "stop", "restart"):
            return said(m.power(args.name, args.what, args.project))
        if args.what == "snapshot":
            return said(m.snapshot(args.name, args.snapshot, args.project))
        if args.what == "restore":
            return said(m.restore(args.name, args.snapshot, accept_restore=args.yes, project=args.project))
        if args.what == "delete":
            return said(m.delete(args.name, accept_delete=args.yes, project=args.project))
        if args.what == "service":
            return said(m.service(args.unit, args.action))
        if args.what == "run":
            rest = args.args[1:] if args.args[:1] == ["--"] else args.args
            if rest:
                print(m.run(*rest), end="")
                return 0
    except Refusal as e:
        print(f"refused: {e}" + (" (again with --yes to go on)" if e.accept else ""), file=sys.stderr)
        return 1
    except CommandError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
