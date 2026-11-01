"""Read a downloaded package archive safely, and notice when it is built to fool a reader.

The review never extracts to disk. A compressed archive is decompressed through a byte-counting
reader that stops at MAX_TOTAL, so a decompression bomb costs bounded memory and no disk; then
members are read from memory. Only regular files are kept. Names that escape the archive root,
links, device files and duplicate names are findings, because a package archive has no honest
reason to contain them.

PARSER DIFFERENTIALS. Python's tarfile stops at a single zero block, or at a header with a bad
checksum after the first member, and calls that the end. npm's tar reader keeps going. A package
can therefore hide `package/evil.js` behind one zero block: this tool would never see it and npm
would install it. So after tarfile finishes, everything left in the stream must be zero bytes
(else HIGH `archive-trailing-data`), and an independent walk over the 512-byte blocks, done the
way a lenient reader does it, must find no entry tarfile missed (else HIGH `archive-hidden-entry`).
A tar archive tarfile cannot open at all raises ArchiveError, which the review turns into a HIGH
`unreviewable-artifact` flag, never into "not checked".

`extract_to` is for a person who wants to read the files: it writes only into a fresh empty
directory, refuses names that are unsafe on any OS (`..`, drive letters, `:` streams, trailing dots
or spaces, reserved device names), writes no links, sets no execute bits and reports every skip.
Nothing in this package ever runs or imports what it extracts.
"""
from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import lzma
import os
import re
import stat
import tarfile
import zipfile
import zlib
from dataclasses import dataclass, field

MAX_MEMBERS = 30000
MAX_FILE = 8 * 1024 * 1024           # bigger members are hashed and sized, not diffed
MAX_TOTAL = 300 * 1024 * 1024        # decompressed bytes, whole archive
MAX_EXT_HEADER = 1024 * 1024         # PAX and GNU long-name headers
BLOCK = 512


class ArchiveError(ValueError):
    pass


@dataclass
class Member:
    path: str
    size: int
    sha256: str
    data: "bytes | None"              # None when over MAX_FILE
    mode: int = 0o644


@dataclass
class Archive:
    files: dict = field(default_factory=dict)       # relative path -> Member
    findings: list = field(default_factory=list)    # (severity, rule, path, why)
    prefix: str = ""                                # the stripped top-level folder, if any
    truncated: bool = False


_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_name(name: str) -> "str | None":
    """The member name as a clean relative POSIX path, or None when it escapes the root."""
    n = name.replace("\\", "/")
    if not n or "\x00" in n or n.startswith("/") or _DRIVE.match(n):
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _note(arc: Archive, sev: str, rule: str, path: str, why: str) -> None:
    arc.findings.append((sev, rule, path[:200], why))


def _add(arc: Archive, path: str, data: "bytes | None", size: int, digest: str, mode: int) -> None:
    if path in arc.files:
        _note(arc, "medium", "duplicate-member", path,
              "the archive holds this path more than once; installers keep one copy, and which one differs")
    arc.files[path] = Member(path, size, digest, data, mode)


# ----------------------------------------------------------------------------- decompression
def _decompress(data: bytes) -> bytes:
    """The raw tar stream, read through a counter that refuses to pass MAX_TOTAL."""
    if data[:2] == b"\x1f\x8b":
        stream = gzip.GzipFile(fileobj=io.BytesIO(data))
    elif data[:3] == b"BZh":
        stream = bz2.BZ2File(io.BytesIO(data))
    elif data[:6] == b"\xfd7zXZ\x00":
        stream = lzma.LZMAFile(io.BytesIO(data))
    else:
        if len(data) > MAX_TOTAL:
            raise ArchiveError("archive is larger than %d MB" % (MAX_TOTAL >> 20))
        return data
    out = bytearray()
    try:
        with stream:
            while True:
                chunk = stream.read(1 << 20)
                if not chunk:
                    break
                out += chunk
                if len(out) > MAX_TOTAL:
                    raise ArchiveError("archive decompresses to more than %d MB" % (MAX_TOTAL >> 20))
    except (OSError, EOFError, lzma.LZMAError, zlib.error) as e:
        raise ArchiveError("compressed stream is corrupt (%s)" % type(e).__name__) from None
    return bytes(out)


# ----------------------------------------------------------------------------- independent block walk
def _octal(field_: bytes) -> "int | None":
    if field_ and field_[0] & 0x80:                      # GNU base-256
        n = int.from_bytes(field_[1:], "big")
        return -n if field_[0] & 0x40 else n
    s = field_.split(b"\x00", 1)[0].strip(b" \x00")
    if not s:
        return 0
    try:
        return int(s, 8)
    except ValueError:
        return None


def _checksum_ok(block: bytes) -> bool:
    want = _octal(block[148:156])
    if want is None:
        return False
    blank = block[:148] + b" " * 8 + block[156:]
    return want in (sum(blank), sum(b - 256 if b > 127 else b for b in blank))


def _cstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def scan_blocks(raw: bytes) -> list:
    """Every entry a lenient reader would see: walk 512-byte blocks, skip zero blocks and blocks
    that are not valid headers, honour GNU long names and PAX paths. [(offset, name, type)]."""
    out = []
    off = 0
    long_name = None
    pax_path = None
    while off + BLOCK <= len(raw):
        block = raw[off:off + BLOCK]
        if block == b"\x00" * BLOCK or not _checksum_ok(block):
            off += BLOCK
            continue
        size = _octal(block[124:136]) or 0
        if size < 0:
            size = 0
        typ = block[156:157] or b"0"
        data_end = off + BLOCK + ((size + BLOCK - 1) // BLOCK) * BLOCK
        if typ in (b"L", b"K", b"x", b"g"):
            if size > MAX_EXT_HEADER:
                raise ArchiveError("an extended tar header is over %d KB" % (MAX_EXT_HEADER >> 10))
            body = raw[off + BLOCK:off + BLOCK + size]
            if typ == b"L":
                long_name = _cstr(body)
            elif typ == b"x":
                for rec in body.split(b"\n"):
                    m = re.match(rb"^\d+ path=(.*)$", rec)
                    if m:
                        pax_path = m.group(1).decode("utf-8", errors="replace")
            off = data_end
            continue
        name = _cstr(block[0:100])
        if block[257:262] == b"ustar":
            prefix = _cstr(block[345:500])
            if prefix:
                name = prefix + "/" + name
        name = pax_path or long_name or name
        long_name = pax_path = None
        out.append((off, name, typ))
        # Data follows only for files; a lenient reader that guesses wrong re-syncs on the next valid header.
        off = data_end if typ in (b"0", b"\x00", b"7") else off + BLOCK
    return out


# ----------------------------------------------------------------------------- tar
def _read_tar(data: bytes, arc: Archive) -> None:
    raw = _decompress(data)
    lenient = scan_blocks(raw)                   # raises on an oversize extended header, before tarfile reads it
    try:
        tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
    except (tarfile.TarError, OSError, EOFError) as e:
        raise ArchiveError("tar header is invalid at the start of the archive (%s)" % type(e).__name__) from None
    seen = set()
    total = 0
    count = 0
    with tf:
        while True:
            try:
                m = tf.next()
            except (tarfile.TarError, OSError, EOFError) as e:
                _note(arc, "high", "archive-corrupt", "", "a tar header is invalid mid-archive (%s); installers may "
                      "read past it" % type(e).__name__)
                break
            if m is None:
                break
            count += 1
            if count > MAX_MEMBERS:
                arc.truncated = True
                break
            seen.add(m.name)
            total += max(m.size, 0)
            if total > MAX_TOTAL:
                arc.truncated = True
                break
            path = safe_name(m.name)
            if path is None:
                _note(arc, "high", "unsafe-path", m.name, "archive member escapes the package folder (absolute path or ..)")
                continue
            if m.issym() or m.islnk():
                target = m.linkname or ""
                escapes = target.startswith("/") or safe_name(
                    os.path.normpath(os.path.join(os.path.dirname(path), target)).replace("\\", "/")) is None
                _note(arc, "high" if escapes else "medium", "archive-link", path,
                      "archive contains a %s%s" % ("symlink" if m.issym() else "hard link",
                                                   " pointing outside the package" if escapes else ""))
                continue
            if m.isdir():
                continue
            if not m.isfile():
                _note(arc, "medium", "special-file", path, "archive contains a device file or FIFO")
                continue
            fh = tf.extractfile(m)
            if fh is None:
                continue
            body = fh.read(MAX_FILE + 1) if m.size <= MAX_FILE else None
            if body is None:
                h = hashlib.sha256()
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
                _add(arc, path, None, m.size, h.hexdigest(), m.mode)
            else:
                _add(arc, path, body, len(body), hashlib.sha256(body).hexdigest(), m.mode)
        end = tf.offset
    if not arc.truncated:
        rest = raw[end:]
        if rest.strip(b"\x00"):
            _note(arc, "high", "archive-trailing-data", "",
                  "%d non-zero bytes follow what Python's tar reader calls the end; npm's reader keeps going, "
                  "so they may install unseen" % len(rest.strip(b"\x00")))
        hidden = sorted({n for _o, n, t in lenient if n not in seen and t in (b"0", b"\x00", b"7", b"1", b"2")})
        for n in hidden[:50]:
            _note(arc, "high", "archive-hidden-entry", n,
                  "a lenient tar reader (like npm's) sees this entry; Python's tar reader does not")


# ----------------------------------------------------------------------------- zip
def _read_zip(data: bytes, arc: Archive) -> None:
    total = 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise ArchiveError("not a readable zip archive (%s)" % type(e).__name__) from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            arc.truncated = True
            infos = infos[:MAX_MEMBERS]
        for info in infos:
            path = safe_name(info.filename)
            if path is None:
                _note(arc, "high", "unsafe-path", info.filename,
                      "archive member escapes the package folder (absolute path or ..)")
                continue
            if info.is_dir():
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and stat.S_ISLNK(mode):
                _note(arc, "medium", "archive-link", path, "archive contains a symlink")
                continue
            if info.flag_bits & 0x1:
                _note(arc, "high", "encrypted-member", path, "an encrypted member: its contents cannot be reviewed")
                continue
            total += info.file_size
            if total > MAX_TOTAL:
                arc.truncated = True
                break
            try:
                with zf.open(info) as fh:
                    if info.file_size > MAX_FILE:
                        h = hashlib.sha256()
                        got = 0
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            got += len(chunk)
                            if got > MAX_TOTAL:
                                raise ArchiveError("member decompresses past the size limit")
                            h.update(chunk)
                        _add(arc, path, None, info.file_size, h.hexdigest(), mode & 0o777)
                    else:
                        body = fh.read(MAX_FILE + 1)
                        _add(arc, path, body, len(body), hashlib.sha256(body).hexdigest(), mode & 0o777)
            except ArchiveError:
                arc.truncated = True
                break
            except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, RuntimeError, EOFError,
                    zlib.error, lzma.LZMAError) as e:
                _note(arc, "medium", "archive-corrupt", path, "member could not be read (%s)" % type(e).__name__)


def read(data: bytes, filename: str = "") -> Archive:
    """Every regular file in the archive, keyed by its path below the common top folder."""
    arc = Archive()
    if data[:4] == b"PK\x03\x04" or filename.endswith((".whl", ".zip")):
        _read_zip(data, arc)
    else:
        _read_tar(data, arc)
    if not filename.endswith(".whl"):       # a wheel has no wrapper folder; its top folder is the package
        _strip_prefix(arc)
    return arc


def _strip_prefix(arc: Archive) -> None:
    """npm packs under package/, sdists and crates under name-version/. Strip a shared top folder
    so the old and new archives line up path for path."""
    paths = list(arc.files)
    if not paths:
        return
    tops = {p.split("/", 1)[0] for p in paths}
    if len(tops) != 1 or any("/" not in p for p in paths):
        return
    top = tops.pop()
    arc.prefix = top
    arc.files = {p[len(top) + 1:]: Member(p[len(top) + 1:], m.size, m.sha256, m.data, m.mode)
                 for p, m in arc.files.items()}


# ----------------------------------------------------------------------------- extraction for reading
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]|CONIN\$|CONOUT\$)(?:\..*)?$", re.I)
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def portable_name_problem(rel: str) -> "str | None":
    """Why a path is unsafe to create on some OS (Windows streams and device names included), or None."""
    if safe_name(rel) != rel:
        return "escapes the folder"
    for part in rel.split("/"):
        if _BAD_CHARS.search(part):
            return "has a character Windows treats specially (':' opens an alternate data stream)"
        if part.endswith((".", " ")):
            return "ends with a dot or a space, which Windows strips"
        if _RESERVED.match(part):
            return "is a reserved device name on Windows"
    return None


def extract_to(arc: Archive, dest: str):
    """Write the archive's regular files under `dest`, which must not exist or be empty.

    Returns (files written, [(path, why skipped)]). Over-size members (not held in memory) are skipped."""
    dest = os.path.abspath(dest)
    if os.path.exists(dest):
        if not os.path.isdir(dest) or os.listdir(dest):
            raise ArchiveError("refusing to extract into a non-empty folder: pick a fresh one")
    else:
        os.makedirs(dest)
    root = os.path.realpath(dest)
    n = 0
    skipped = []
    for rel, m in arc.files.items():
        if m.data is None:
            skipped.append((rel, "over the in-memory size limit"))
            continue
        why = portable_name_problem(rel)
        if why:
            skipped.append((rel, why))
            continue
        target = os.path.realpath(os.path.join(root, *rel.split("/")))
        try:
            if os.path.commonpath([root, target]) != root:
                skipped.append((rel, "resolves outside the folder"))
                continue
        except ValueError:
            skipped.append((rel, "resolves to a different drive"))
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.lexists(target):
                skipped.append((rel, "clashes with a path already written (case or file/folder clash)"))
                continue
            with open(target, "xb") as fh:
                fh.write(m.data)
        except OSError as e:
            skipped.append((rel, "could not be written (%s): a file/folder clash or a name this OS refuses"
                            % type(e).__name__))
            continue
        try:
            os.chmod(target, 0o644)
        except OSError:
            pass
        n += 1
    return n, skipped
