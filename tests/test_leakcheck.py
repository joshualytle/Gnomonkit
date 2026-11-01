"""Tests for scripts/leakcheck.py. Synthetic data only: every private-looking string is assembled at
runtime from fragments, so this file itself passes leakcheck."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "leakcheck.py")

FAKE_TERM = "Zebra" + "corp"                     # stands in for a maintainer's private product name
FAKE_DOMAIN = "zebra" + "corp-internal.test"
PRIVATE_IP = "10." + "20.30.40"
DOC_IP = "192.0.2." + "15"                        # RFC 5737 documentation address: allowed
WIN_HOME = "C:" + "\\Users\\" + "alice" + "\\notes"
WIN_PLACEHOLDER = "C:" + "\\Users\\" + "<you>" + "\\notes"
REAL_EMAIL = "alice" + "@" + "zebra" + "mail.test"
OK_EMAIL = "someone" + "@" + "example.com"


def run(args, cwd, denylist=True, stdin=None, tmp_path=None):
    env = dict(os.environ)
    env.pop("GNOMONKIT_DENYLIST", None)
    if denylist:
        dl = os.path.join(cwd, "..", "denylist.txt")
        with open(dl, "w", encoding="utf-8") as f:
            f.write("# synthetic denylist\nproducts\t\\b%s\\b\ndomains\t%s\n" % (FAKE_TERM, FAKE_DOMAIN.replace(".", "\\.")))
        env["GNOMONKIT_DENYLIST"] = os.path.abspath(dl)
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=cwd, env=env, input=stdin,
                          capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


def write(repo, name, text, binary=False):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        p.write_bytes(text)
    else:
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", name], check=True)


def test_missing_denylist_fails_closed(repo):
    write(repo, "a.md", "hello")
    r = run(["--all"], str(repo), denylist=False)
    assert r.returncode == 2
    assert "denylist" in r.stderr


def test_builtin_only_skips_the_denylist_but_keeps_builtin_patterns(repo):
    # For runs that cannot have the denylist (a fork's pull request); never the default.
    r = run(["--builtin-only", "--stdin", "t"], str(repo), denylist=False, stdin="Made at " + FAKE_TERM)
    assert r.returncode == 0
    r = run(["--builtin-only", "--stdin", "t"], str(repo), denylist=False, stdin=WIN_HOME)
    assert r.returncode == 1 and "home-path" in r.stdout


def test_clean_tree_passes(repo):
    write(repo, "README.md", "A generic tool. Docs use %s and %s.\n" % (DOC_IP, OK_EMAIL))
    r = run(["--all"], str(repo))
    assert r.returncode == 0, r.stdout + r.stderr


def test_denylist_hit_reports_category_not_text(repo):
    write(repo, "doc.md", "intro\nbuilt for %s at %s\n" % (FAKE_TERM, FAKE_DOMAIN))
    r = run(["--all"], str(repo))
    assert r.returncode == 1
    assert "doc.md:2: products" in r.stdout
    assert "doc.md:2: domains" in r.stdout
    assert FAKE_TERM not in r.stdout + r.stderr
    assert FAKE_DOMAIN not in r.stdout + r.stderr


@pytest.mark.parametrize("text,cat", [
    ("ssh " + PRIVATE_IP, "private-ip"),
    ("see " + WIN_HOME, "home-path"),
    ("mail " + REAL_EMAIL, "email"),
])
def test_builtin_patterns(repo, text, cat):
    write(repo, "x.md", text + "\n")
    r = run(["--all"], str(repo))
    assert r.returncode == 1 and ("x.md:1: " + cat) in r.stdout
    assert text.split(" ", 1)[1] not in r.stdout


def test_placeholders_allowed(repo):
    write(repo, "x.md", "path %s, docs ip %s, mail %s\n" % (WIN_PLACEHOLDER, DOC_IP, OK_EMAIL))
    assert run(["--all"], str(repo)).returncode == 0


@pytest.mark.parametrize("name", ["sessions/s1.md", "out.jsonl", "palace.sqlite3", "bench/cases2.json",
                                  ".env", "keys/id.pem", "my-transcript.txt"])
def test_blocked_files(repo, name):
    write(repo, name, "x")
    r = run(["--all"], str(repo))
    assert r.returncode == 1 and "blocked-file-type" in r.stdout


def test_binary_blocked(repo):
    write(repo, "img.dat", b"\x89PNG\x00\x00data", binary=True)
    assert "binary-file" in run(["--all"], str(repo)).stdout


def _png(*chunks, trailer=b""):
    """A minimal PNG built from (type, payload) chunks; CRCs are not checked by leakcheck."""
    import struct
    import zlib
    body = b"\x89PNG\r\n\x1a\n"
    for ctype, payload in chunks:
        body += struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", zlib.crc32(ctype + payload))
    return body + trailer


PIXELS = [(b"IHDR", b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"),
          (b"IDAT", b"\x78\x9c\x63\x60\x60\x60\x00\x00\x00\x04\x00\x01"), (b"IEND", b"")]


def test_pixels_only_png_in_assets_passes(repo):
    write(repo, "assets/brand/logo.png", _png(*PIXELS), binary=True)
    r = run(["--all"], str(repo))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("name,data", [
    ("assets/brand/text.png", _png(PIXELS[0], (b"tEXt", b"Comment\x00made on a private host"), *PIXELS[1:])),
    ("assets/brand/exif.png", _png(PIXELS[0], (b"eXIf", b"MM\x00*"), *PIXELS[1:])),
    ("assets/brand/trailing.png", _png(*PIXELS, trailer=b"hidden payload after IEND")),
    ("docs/logo.png", _png(*PIXELS)),                       # clean, but outside assets/
    ("assets/brand/fake.png", b"GIF89a\x00\x00not a png"),  # wrong format behind a .png name
])
def test_png_with_hidden_data_or_wrong_place_blocked(repo, name, data):
    write(repo, name, data, binary=True)
    r = run(["--all"], str(repo))
    assert r.returncode == 1 and "binary-file" in r.stdout


def test_private_term_in_path(repo):
    write(repo, "plugins/%s/README.md" % FAKE_TERM.lower(), "generic")
    r = run(["--all"], str(repo))
    assert r.returncode == 1 and "products" in r.stdout


def test_allowlist(repo):
    write(repo, ".leakcheck-allow", "# the project's own public handle\n%s/public-repo\n" % FAKE_TERM)
    write(repo, "x.md", "install from %s/public-repo\n" % FAKE_TERM)
    assert run(["--all"], str(repo)).returncode == 0
    write(repo, "y.md", "but %s alone is private\n" % FAKE_TERM)
    assert run(["--all"], str(repo)).returncode == 1


def test_staged_mode_reads_index(repo):
    write(repo, "x.md", "built for %s\n" % FAKE_TERM)            # staged version is dirty
    (repo / "x.md").write_text("clean now\n", encoding="utf-8")   # working tree clean, not staged
    assert run(["--staged"], str(repo)).returncode == 1


def test_stdin_commit_message(repo):
    r = run(["--stdin", "commit-msg"], str(repo), stdin="fix the %s thing\n" % FAKE_TERM)
    assert r.returncode == 1 and "commit-msg:1: products" in r.stdout
