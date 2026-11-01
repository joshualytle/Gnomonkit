"""The access token: random, generated, kept in a file only its owner can read.

On POSIX the file is created 0600 with O_EXCL and O_NOFOLLOW, so it is never readable by others even
for a moment and never written through a planted symlink. A token path that is a symlink, or a file
that belongs to another account, is refused rather than read or replaced. The folder it sits in is
made 0700 and refused if another account owns it or can write to it (paths.private_dir).

On Windows os.open's mode only toggles read-only; the file is private because it sits under the
user's profile, whose ACL admits only that user, SYSTEM and Administrators. That is the real
mechanism, so the docs say so rather than claiming 0600 there.
"""
from __future__ import annotations

import os
import re
import secrets
import stat
from pathlib import Path

from . import paths

MIN_LEN = 32
MAX_BYTES = 4096
_OK = re.compile(r"[A-Za-z0-9_-]{%d,256}" % MIN_LEN)


class UnsafeToken(paths.UnsafePath):
    """The token path is a symlink or a file another account owns."""


def generate() -> str:
    return secrets.token_urlsafe(32)          # 43 characters, 256 bits


def valid(tok: str) -> bool:
    return bool(tok) and bool(_OK.fullmatch(tok))


def _refuse_link(path: Path) -> None:
    if os.path.islink(path):
        raise UnsafeToken("refusing the token path %s: it is a symlink. Remove it and run again." % path)


def read(path: Path) -> str | None:
    """The token, or None when there is no valid one. Raises UnsafeToken for a symlink or a file of
    another account, so a planted file is never trusted as the token."""
    _refuse_link(path)
    try:
        fd = os.open(str(path), os.O_RDONLY | paths.NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as e:
        if paths.NOFOLLOW and os.path.islink(path):
            raise UnsafeToken("refusing the token path %s: it is a symlink" % path) from e
        return None
    try:
        if paths.POSIX:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise UnsafeToken("refusing the token path %s: not a regular file" % path)
            if st.st_uid != os.geteuid():
                raise UnsafeToken("refusing the token file %s: it belongs to another account" % path)
            if st.st_mode & 0o077:
                os.fchmod(fd, 0o600)          # a token from before 0.4.0 or a loose umask
        raw = os.read(fd, MAX_BYTES)
    finally:
        os.close(fd)
    tok = raw.decode("utf-8-sig", "replace").strip()
    return tok if valid(tok) else None


def ensure(path: Path) -> tuple[str, bool]:
    """(token, created). Keeps an existing valid token, so re-running an install changes nothing."""
    paths.private_dir(path.parent)
    cur = read(path)
    if cur:
        return cur, False
    try:
        path.unlink()                         # an invalid leftover of this account (read() checked it)
    except OSError:
        pass
    tok = generate()
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | paths.NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(tok + "\n")
    return tok, True
