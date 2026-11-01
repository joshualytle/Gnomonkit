#!/usr/bin/env python3
"""Make a scratch folder in the one place scratch belongs, stamped so cleanup knows what it is.

    python make_scratch.py "qa data for the import test"            prints the new folder's path
    python make_scratch.py "fresh-install check" --days 3 --by app
    python make_scratch.py --mark C:\\some\\existing\\folder "why it exists" --days 30

The root is the core plugin's scratch_root option (CLAUDE_PLUGIN_OPTION_SCRATCH_ROOT), else
GNOMONKIT_SCRATCH_ROOT, else the personal profile's paths.scratch_root, else ~/.gnomonkit/scratch.
Never a drive root, never the projects folder.
The folder gets .gnomonkit-made.json {by, purpose, created, expires}; once it expires,
local_inventory.py calls it safe to remove. --mark stamps a folder that already exists.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

MARKER = ".gnomonkit-made.json"


def _profile(key: str):
    """The personal profile's value for `key` (checked against its schema), or None. Never raises."""
    try:
        core = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if core not in sys.path:
            sys.path.append(core)
        from gnomonkit_cmds import _common
        return _common.checked_profile_value(key)
    except Exception:  # noqa: BLE001 - the profile is optional
        return None


def scratch_root() -> str:
    return (os.environ.get("CLAUDE_PLUGIN_OPTION_SCRATCH_ROOT") or os.environ.get("GNOMONKIT_SCRATCH_ROOT")
            or _profile("paths.scratch_root")
            or os.path.join(os.environ.get("GNOMONKIT_HOME") or os.path.join(os.path.expanduser("~"), ".gnomonkit"),
                            "scratch"))


def stamp(folder: str, purpose: str, days: float, by: str) -> None:
    now = time.time()
    with open(os.path.join(folder, MARKER), "w", encoding="utf-8") as f:
        json.dump({"by": by, "purpose": purpose, "created": now, "expires": now + days * 86400}, f, indent=1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("purpose")
    ap.add_argument("--days", type=float, default=14, help="when cleanup may treat it as expired (default 14)")
    ap.add_argument("--by", default=os.path.basename(os.getcwd()) or "agent", help="who it is for (default: this folder's name)")
    ap.add_argument("--mark", metavar="FOLDER", help="stamp an existing folder instead of making one")
    a = ap.parse_args(argv)
    if a.mark:
        if not os.path.isdir(a.mark):
            print("not a folder: %s" % a.mark, file=sys.stderr)
            return 2
        stamp(a.mark, a.purpose, a.days, a.by)
        print(os.path.abspath(a.mark))
        return 0
    slug = re.sub(r"[^a-z0-9]+", "-", a.purpose.lower()).strip("-")[:40] or "scratch"
    folder = os.path.join(scratch_root(), "%s-%s-%s" % (re.sub(r"[^A-Za-z0-9]+", "-", a.by)[:30], slug,
                                                       time.strftime("%Y%m%d-%H%M%S")))
    os.makedirs(folder)
    stamp(folder, a.purpose, a.days, a.by)
    print(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
