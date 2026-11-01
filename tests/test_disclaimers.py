"""The disclaimer is published and linked wherever a user decides to install or turn something on.

Keeps a new plugin from shipping without the line, and the manifest from dropping the file.
"""
import fnmatch
import glob
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTIONS = ["What this is", "No warranty, no liability", "The safety hooks are heuristics",
            "Agents act as you", "Your data stays on your machine", "Other tools", "At work",
            "Not advice"]


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_disclaimer_has_every_section():
    text = read("DISCLAIMER.md")
    headings = [ln[3:] for ln in text.splitlines() if ln.startswith("## ")]
    for section in SECTIONS:
        assert any(h.startswith(section) for h in headings), section


def test_top_level_docs_link_the_disclaimer():
    for rel in ("README.md", "SECURITY.md", "CONTRIBUTING.md"):
        assert "(DISCLAIMER.md)" in read(rel), rel
    assert "(../DISCLAIMER.md)" in read(os.path.join("docs", "ONBOARDING.md"))


def test_every_plugin_readme_links_the_disclaimer():
    readmes = glob.glob(os.path.join(ROOT, "plugins", "*", "README.md"))
    assert readmes
    for path in readmes:
        with open(path, encoding="utf-8") as fh:
            head = fh.read(1200)
        assert "(../../DISCLAIMER.md)" in head, os.path.relpath(path, ROOT)


def test_manifest_publishes_the_disclaimer():
    if not os.path.exists(os.path.join(ROOT, "release", "public-manifest.txt")):
        pytest.skip("the release manifest stays in the development repository")
    lines = [ln.strip() for ln in read(os.path.join("release", "public-manifest.txt")).splitlines()]
    allowed = [ln for ln in lines if ln and not ln.startswith(("#", "!"))]
    assert any(fnmatch.fnmatch("DISCLAIMER.md", pat) for pat in allowed)
