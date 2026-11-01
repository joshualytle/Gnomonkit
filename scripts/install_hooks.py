#!/usr/bin/env python3
"""Install the local git hooks that run the leak gates before anything is committed.

  python scripts/install_hooks.py

pre-commit  : leakcheck and provenance_check on the staged change, then `gitleaks git --staged` (if installed)
commit-msg  : leakcheck and provenance_check on the commit message
Both need the private denylist: set GNOMONKIT_DENYLIST to its path first (see docs/LEAK-GATES.md).
Hooks are plain sh, which git runs on Windows (Git for Windows) as well as macOS/Linux.
"""
import os
import stat
import subprocess
import sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
HOOKS = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], capture_output=True, text=True, check=True,
                       cwd=ROOT).stdout.strip()
HOOKS = HOOKS if os.path.isabs(HOOKS) else os.path.join(ROOT, HOOKS)

PY = "python3" if sys.platform != "win32" else "python"

PRE_COMMIT = """#!/bin/sh
# Installed by scripts/install_hooks.py -- Gnomonkit leak gates.
if [ -z "$GNOMONKIT_DENYLIST" ]; then
  echo "leak gates: GNOMONKIT_DENYLIST is not set; refusing to commit (see docs/LEAK-GATES.md)." >&2
  exit 1
fi
%(py)s scripts/leakcheck.py --staged || exit 1
# Hooks are shared by every worktree; a branch from before provenance_check existed skips it (CI still runs it).
if [ -f scripts/provenance_check.py ]; then %(py)s scripts/provenance_check.py --staged || exit 1; fi
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks git --staged --no-banner --redact -l warn || exit 1
else
  echo "leak gates: gitleaks not installed; secret scan skipped locally (CI still runs it)." >&2
fi
""" % {"py": PY}

COMMIT_MSG = """#!/bin/sh
# Installed by scripts/install_hooks.py -- Gnomonkit leak gates (commit message).
%(py)s scripts/leakcheck.py --stdin commit-message < "$1" || exit 1
if [ -f scripts/provenance_check.py ]; then %(py)s scripts/provenance_check.py --stdin commit-message < "$1" || exit 1; fi
""" % {"py": PY}

os.makedirs(HOOKS, exist_ok=True)
for name, body in (("pre-commit", PRE_COMMIT), ("commit-msg", COMMIT_MSG)):
    path = os.path.join(HOOKS, name)
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("installed", path)
