#!/usr/bin/env python3
"""gnomonkit: the one command for Gnomonkit's settings and tools.

    python gnomonkit.py setup [status | done | skipped | security]
    python gnomonkit.py profile show | set KEY=VALUE ... | unset KEY ...
    python gnomonkit.py install-launcher      puts `gnomonkit` on your PATH (prints the line to add)

Each subcommand is a module in gnomonkit_cmds/ that defines register(subparsers); this file finds
them with pkgutil, so a new area is a new module and this file does not change. Hooks print this
file's absolute path when they need the agent or the user to run it.

Standard library only. Exit 0 on success, 2 on a refusal or a bad value (the message says what is
valid), 1 on an unexpected error.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from gnomonkit_cmds import _common  # noqa: E402


def build_parser() -> tuple:
    ap = argparse.ArgumentParser(
        prog="gnomonkit",
        description="Gnomonkit's settings and tools. `gnomonkit <command> --help` shows each command.")
    ap.add_argument("--version", action="version", version="gnomonkit (core %s)" % _common.core_version())
    sub = ap.add_subparsers(dest="command", metavar="<command>")
    broken = []
    for name, mod, err in _common.command_modules():
        register = getattr(mod, "register", None) if mod else None
        if err:
            broken.append((name, err))
        elif callable(register):
            try:
                register(sub)
            except Exception as e:  # noqa: BLE001 - one broken area must not take the others down
                broken.append((name, "%s: %s" % (type(e).__name__, e)))
    return ap, broken


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    ap, broken = build_parser()
    for name, err in broken:
        print("gnomonkit: the %s commands could not load (%s)" % (name, err), file=sys.stderr)
    args = ap.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        ap.print_help()
        return 0
    try:
        return int(func(args) or 0)
    except _common.CommandError as e:
        print("gnomonkit: %s" % e, file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
