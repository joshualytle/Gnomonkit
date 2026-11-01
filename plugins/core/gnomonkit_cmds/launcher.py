"""gnomonkit install-launcher: put a `gnomonkit` command on your PATH.

    gnomonkit install-launcher [--dir FOLDER]

Writes three files to ~/.gnomonkit/bin (or FOLDER):
    gnomonkit             for sh, bash and zsh (macOS, Linux, Git Bash)
    gnomonkit.cmd         for Command Prompt and PowerShell
    gnomonkit_launch.py   what both run

The launcher doesn't hold a copy of Gnomonkit. Each time it runs it looks up the core plugin's
current install folder in Claude Code's record of installed plugins (installed_plugins.json, the
user-scope entry for core@<marketplace>, core@gnomonkit first) and runs that folder's gnomonkit.py,
so it keeps working after the plugin updates. When the record names no usable folder it falls back
to the folder that installed it.

It prints the line that puts the folder on PATH and never edits a shell profile itself. Running it
again rewrites the three files; a file of the same name that it didn't write is left alone.
"""
from __future__ import annotations

import os
import sys

from . import _common as C

MARK = "Written by `gnomonkit install-launcher`"
NAMES = ("gnomonkit", "gnomonkit.cmd", "gnomonkit_launch.py")

LAUNCH_PY = '''#!/usr/bin/env python3
"""%(mark)s. Finds the Gnomonkit core plugin's current install folder
and runs its gnomonkit.py, so this keeps working after the plugin updates. Safe to delete: run
`gnomonkit.py install-launcher` again to put it back."""
import json
import os
import runpy
import sys

FALLBACK = %(fallback)r  # the core folder that wrote this launcher


def claude_home():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def from_record():
    """The user-scope install folder of core@<marketplace> (core@gnomonkit first), or None."""
    try:
        with open(os.path.join(claude_home(), "plugins", "installed_plugins.json"), encoding="utf-8") as fh:
            plugins = json.load(fh).get("plugins") or {}
        keys = sorted((k for k in plugins if k.split("@", 1)[0] == "core"),
                      key=lambda k: (k != "core@gnomonkit", k))
    except Exception:
        return None
    for key in keys:
        entries = plugins.get(key)
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("scope") == "user":
                path = entry.get("installPath")
                if isinstance(path, str) and os.path.isfile(os.path.join(path, "gnomonkit.py")):
                    return path
    return None


def main():
    core = from_record()
    if core is None and os.path.isfile(os.path.join(FALLBACK, "gnomonkit.py")):
        core = FALLBACK
    if core is None:
        sys.stderr.write("gnomonkit: can't find the Gnomonkit core plugin. Install it for your user "
                         "(claude plugin install core@gnomonkit --scope user), or run gnomonkit.py from "
                         "the plugin folder.\\n")
        return 1
    script = os.path.join(core, "gnomonkit.py")
    sys.argv = [script] + sys.argv[1:]
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

LAUNCH_SH = '''#!/bin/sh
# %(mark)s: runs the Gnomonkit core plugin's gnomonkit.py.
py=%(exe)s
if [ ! -x "$py" ]; then
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) py=python ;;
    *) if command -v python3 >/dev/null 2>&1; then py=python3; else py=python; fi ;;
  esac
fi
exec "$py" %(launch)s "$@"
'''

LAUNCH_CMD = '''@echo off
rem %(mark)s: runs the Gnomonkit core plugin's gnomonkit.py.
setlocal
set "GNOMONKIT_PY=%(exe)s"
if not exist "%%GNOMONKIT_PY%%" set "GNOMONKIT_PY=python"
"%%GNOMONKIT_PY%%" "%(launch)s" %%*
exit /b %%ERRORLEVEL%%
'''


def register(sub) -> None:
    p = sub.add_parser("install-launcher", help="put a `gnomonkit` command on your PATH (prints the line to add)",
                       description=__doc__.split("\n\n")[0])
    p.add_argument("--dir", help="the folder to write the launcher to (default: ~/.gnomonkit/bin)")
    p.set_defaults(func=run)


def launcher_dir() -> str:
    return os.path.join(C.gnomonkit_home(), "bin")


def installed(folder: str) -> bool:
    return all(os.path.isfile(os.path.join(folder, n)) for n in NAMES)


def on_path(folder: str) -> bool:
    want = C.normalise_path(folder)
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        if entry.strip():
            try:
                if C.normalise_path(entry.strip().strip('"')) == want:
                    return True
            except (OSError, ValueError):
                continue
    return False


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _posix_form(folder: str) -> str:
    """The folder as a POSIX shell sees it: $HOME-relative when under home; /c/... in Git Bash."""
    home = C.resolve_path("~")
    if C.path_within(folder, home):
        rest = folder[len(home):].replace("\\", "/")
        return "$HOME" + rest
    if os.name == "nt" and len(folder) > 1 and folder[1] == ":":
        return "/" + folder[0].lower() + folder[2:].replace("\\", "/")
    return folder


def path_lines(folder: str) -> list:
    sh = 'export PATH="%s:$PATH"' % _posix_form(folder)
    if os.name == "nt":
        ps = ('[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") '
              '+ ";%s", "User")' % folder)
        return ["PowerShell (once; new terminals then find it):", "  " + ps,
                "Git Bash (add to ~/.bashrc):", "  " + sh]
    return ["Add to your shell's start-up file (~/.zshrc, ~/.bashrc or ~/.profile):", "  " + sh]


def _ours(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return MARK in fh.read(4096)
    except OSError:
        return True  # nothing there to protect


def install(folder: str) -> list:
    """Write the three files to `folder`. Returns their paths. CommandError if one would replace a file
    this command didn't write."""
    folder = C.resolve_path(folder)
    foreign = [n for n in NAMES if os.path.exists(os.path.join(folder, n)) and not _ours(os.path.join(folder, n))]
    if foreign:
        raise C.CommandError("%s already has %s, which install-launcher didn't write. Move it, or pass --dir "
                             "with another folder; nothing was written." % (folder, ", ".join(foreign)))
    C.ensure_dir(folder)
    launch = os.path.join(folder, "gnomonkit_launch.py")
    exe = os.path.abspath(sys.executable) if sys.executable else ""
    files = {
        launch: (LAUNCH_PY % {"mark": MARK, "fallback": C.core_dir()}, 0o600),
        os.path.join(folder, "gnomonkit"): (LAUNCH_SH % {"mark": MARK, "exe": _sh_quote(exe.replace("\\", "/")),
                                                         "launch": _sh_quote(launch.replace("\\", "/"))}, 0o700),
        os.path.join(folder, "gnomonkit.cmd"): (
            (LAUNCH_CMD % {"mark": MARK, "exe": exe, "launch": launch}).replace("\n", "\r\n"), 0o600),
    }
    for path, (text, mode) in files.items():
        C.atomic_write(path, text.encode("utf-8"), mode=mode)
    return list(files)


def run(args) -> int:
    folder = C.resolve_path(args.dir or launcher_dir())
    for path in install(folder):
        print("wrote %s" % path)
    if on_path(folder):
        print("%s is on PATH: `gnomonkit` works in a new terminal." % folder)
        return 0
    print("\n%s is not on PATH yet. Gnomonkit doesn't edit your shell profile; to add it yourself:" % folder)
    for line in path_lines(folder):
        print("  " + line)
    print("Until then: %s" % C.command_line())
    return 0
