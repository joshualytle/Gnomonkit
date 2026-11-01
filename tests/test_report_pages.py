"""The report-pages template keeps the promises its SKILL.md makes."""
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "plugins" / "core" / "skills" / "report-pages"
PAGE = (SKILL / "template.html").read_text(encoding="utf-8")
DOC = (SKILL / "SKILL.md").read_text(encoding="utf-8")


def test_answers_land_where_the_skill_says_claude_reads_them():
    assert 'collection("answers")' in PAGE and "`answers` collection" in DOC
    for field in ("section", "title", "verdict", "note", "at"):
        assert re.search(rf"\b{field}:", PAGE), field
        assert field in DOC


def test_store_is_optional_and_copy_out_always_works():
    # claude.use only exists on the published page; a saved file must still work.
    assert 'window.claude && typeof window.claude.use === "function"' in PAGE
    assert "Copy feedback" in PAGE and "FEEDBACK:" in PAGE and "FEEDBACK:" in DOC
    assert "not reaching Claude here" in PAGE


def test_no_external_code_and_dark_mode_tokens():
    assert not re.search(r"<script[^>]+src=", PAGE) and not re.search(r"<link[^>]+stylesheet", PAGE)
    assert "@media (prefers-color-scheme: dark)" in PAGE
    assert ':root:not([data-theme="light"])' in PAGE and ':root[data-theme="dark"]' in PAGE
    assert re.search(r"body\s*\{[^}]*background:", PAGE)


def test_every_asking_section_has_an_id():
    asking = re.findall(r"<section([^>]*)data-ask", PAGE)
    assert asking and all(re.search(r'\bid="[^"]+"', attrs) for attrs in asking)


def test_storage_access_is_guarded():
    # Browser storage can throw (private windows, previews); the page must still render.
    for call in ("localStorage.getItem", "localStorage.setItem"):
        line = next(l for l in PAGE.splitlines() if call in l)
        assert "try" in line, line
