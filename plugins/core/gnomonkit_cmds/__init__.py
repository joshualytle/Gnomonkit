"""Subcommands of the `gnomonkit` command (plugins/core/gnomonkit.py).

Every module in this package whose name does not start with "_" and that defines
`register(subparsers)` is a subcommand: gnomonkit.py finds it with pkgutil and calls `register` with
its argparse subparsers object. A module adds its parser there and sets `func` with
`set_defaults(func=...)`; `func(args)` returns the exit code. So a new area (history, overlay,
grants, trust, ...) is a new file here, and gnomonkit.py never changes.

A module may also define `security_setup(args) -> int` (and `SECURITY_ORDER`, an int, lower runs
first): `gnomonkit setup security` runs each one in a terminal. That is how permissions an agent
must not set (orchestration grants, folder trust) join the setup without editing setup.py.

Shared helpers live in `_common.py`. Keep this file free of imports: hooks import `_common` through
this package, and they must stay fast.
"""
