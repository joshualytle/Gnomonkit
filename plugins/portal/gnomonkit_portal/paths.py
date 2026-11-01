"""Where the portal keeps its files and where it finds core's collector.

DATA DIR. $GNOMONKIT_PORTAL_DIR, else $GNOMONKIT_HOME/portal, else ~/.gnomonkit/portal. It holds:
  metrics.json   the collector's last output (never served as is; see view.py)
  state.json     the last refresh: when, how it was started, ok or the error class
  config.json    optional settings: {"hide_titles": bool, "refresh_min_interval": seconds,
                 "memory": "auto" | "on" | "off", "since_days": number}
  token          the access token, when one was generated
  portal.log     server and refresh log (rotated)

On POSIX the folder is 0700 and every file in it 0600, whatever the umask. A data folder that belongs
to another account, or that group or others can write to, is refused: anyone who can write there
could plant a file or a symlink where the portal writes. Files are replaced through a fresh mkstemp
name and os.replace, never written through a fixed temp name, and logs are opened with O_NOFOLLOW.

WORK ITEMS. $GNOMONKIT_HOME/work/items.json (else ~/.gnomonkit/work/items.json): the summary of open
work items that core's `gnomonkit work` writes whenever it reads a repository. It holds no issue bodies
and no comment text, and is never served as is either (see view.work_view).

COLLECTOR. $GNOMONKIT_EFFECTIVENESS_COLLECTOR, else core beside this plugin in a checkout
(plugins/core/skills/effectiveness/effectiveness.py), else the newest core in the Claude Code plugin
cache (<config>/plugins/cache/<marketplace>/core/<version>/skills/effectiveness/effectiveness.py).
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_REL = Path("skills") / "effectiveness" / "effectiveness.py"


def gnomonkit_home() -> Path:
    return Path(os.environ.get("GNOMONKIT_HOME") or Path.home() / ".gnomonkit")


def data_dir() -> Path:
    env = os.environ.get("GNOMONKIT_PORTAL_DIR")
    return Path(env) if env else gnomonkit_home() / "portal"


def work_file() -> Path:
    return gnomonkit_home() / "work" / "items.json"


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _version(name: str):
    if not re.fullmatch(r"\d+(\.\d+){1,3}", name or ""):
        return None
    return tuple(int(x) for x in name.split("."))


def find_collector() -> Path | None:
    env = os.environ.get("GNOMONKIT_EFFECTIVENESS_COLLECTOR")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    sibling = PLUGIN_ROOT.parent / "core" / COLLECTOR_REL          # a Gnomonkit checkout
    if sibling.is_file():
        return sibling
    best = None
    cache = claude_home() / "plugins" / "cache"
    try:
        for mkt in cache.iterdir():
            core = mkt / "core"
            if not core.is_dir():
                continue
            for d in core.iterdir():
                v = _version(d.name)
                f = d / COLLECTOR_REL
                if v and f.is_file() and (best is None or v > best[0]):
                    best = (v, f)
    except OSError:
        return None
    return best[1] if best else None


DEFAULTS = {"hide_titles": False, "refresh_min_interval": 600, "memory": "auto", "since_days": 30}


def load_config(d: Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    p = (d or data_dir()) / "config.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
                    cfg[k] = v
                elif k in DEFAULTS and isinstance(DEFAULTS[k], (int, float)) and isinstance(v, (int, float)) \
                        and not isinstance(v, bool):
                    cfg[k] = v
    except (OSError, ValueError):
        pass
    env = os.environ.get("GNOMONKIT_PORTAL_HIDE_TITLES")
    if env is not None:
        cfg["hide_titles"] = env.strip().lower() in ("1", "true", "yes", "on")
    if cfg["memory"] not in ("auto", "on", "off"):
        cfg["memory"] = "auto"
    return cfg


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return default


# ---------------------------------------------------------------- private files
POSIX = os.name == "posix"
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class UnsafePath(Exception):
    """A data folder or file another account could tamper with. The message says how to fix it."""


def check_private_dir(d: Path) -> None:
    """Refuse a folder that belongs to another account or that group or others can write to; tighten
    one that is merely readable by them (a portal before 0.4.0 made it with the default mode)."""
    if not POSIX:
        return
    st = os.stat(d)
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafePath("%s is not a folder" % d)
    if st.st_uid != os.geteuid():
        raise UnsafePath("refusing the data folder %s: it belongs to another account. Use a folder of your "
                         "own (--data-dir, GNOMONKIT_PORTAL_DIR)." % d)
    if st.st_mode & 0o022:
        raise UnsafePath("refusing the data folder %s: other accounts can write to it, so they could plant "
                         "files the portal writes through. Check what is in it, then: chmod 700 %s . If its mode "
                         "stays the same after that, it is on a file system without POSIX permissions (a Windows "
                         "drive under WSL, exFAT, a network share): put the data folder on a native file system "
                         "instead (--data-dir, GNOMONKIT_PORTAL_DIR)." % (d, d))
    if st.st_mode & 0o077:
        os.chmod(d, 0o700)


def private_dir(d: Path) -> Path:
    """The folder, made 0700 where it (or a parent) is missing, then checked."""
    missing, p = [], Path(d)
    while not os.path.lexists(p) and p.parent != p:
        missing.append(p)
        p = p.parent
    for q in reversed(missing):
        try:
            os.mkdir(q, 0o700)
        except FileExistsError:
            pass
    check_private_dir(d)
    return d


def write_private(p: Path, text: str, mode: int = 0o600) -> None:
    """Replace p with text, readable by its owner only (or with mode, which is set on the temp file before
    it takes p's place, never on p by name afterwards). The temp file is a fresh mkstemp name in the same
    folder (created 0600 with O_EXCL), so a file or symlink planted at a guessable name is never written
    through, and os.replace swaps a symlink at p itself instead of following it."""
    fd, tmp = tempfile.mkstemp(prefix="." + p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            if POSIX and mode != 0o600:
                os.fchmod(fh.fileno(), mode)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(p: Path, obj) -> None:
    private_dir(p.parent)
    write_private(p, json.dumps(obj, indent=1, ensure_ascii=False))


def open_private_append(p: Path, encoding: str = "utf-8"):
    """p opened for appending: created 0600, never through a symlink (O_NOFOLLOW), and refused unless it
    is a regular file of this account."""
    fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT | NOFOLLOW, 0o600)
    try:
        if POSIX:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
                raise UnsafePath("refusing %s: not a regular file of this account" % p)
            if st.st_mode & 0o077:
                os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a", encoding=encoding)
    except BaseException:
        os.close(fd)
        raise
