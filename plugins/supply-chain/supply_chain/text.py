"""Make untrusted text inert before it lands in a report.

Package names, file paths, diff lines and model output all come from people we do not control.
Rendered into Markdown they could fake a section of the review ("No risks found"), @mention
someone, or carry a live link. Everything external passes through here first.
"""
from __future__ import annotations

import re
import unicodedata

_URL = re.compile(r"(?i)\b(?:https?|ftp|file|javascript|data)\s*:")
_MD = re.compile(r"([\\`*_{}\[\]()#+!|~=])")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
NAME_OK = re.compile(r"^[A-Za-z0-9@._/+:~-]{1,214}$")


def _invisible(ch: str) -> bool:
    """Characters a reader cannot see but a model or terminal acts on: controls (Cc), format
    characters such as bidi overrides, zero-width joiners and the U+E0000 tag block (Cf), private-use
    (Co), surrogates (Cs) and variation selectors."""
    o = ord(ch)
    if 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or 0xE0000 <= o <= 0xE007F:
        return True
    return unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs")


def plain(text, limit: int = 400) -> str:
    """One line of inert text: no control or bidi characters, no live links, no mentions, no
    Markdown or HTML, bounded length."""
    t = ANSI.sub("", str(text))
    t = re.sub(r"\s+", " ", t)                   # tabs and newlines become spaces before controls go
    t = "".join(ch for ch in t if not _invisible(ch))
    t = re.sub(r"\s+", " ", t).strip()
    t = _URL.sub(lambda m: m.group(0).replace(":", "[:]"), t)
    t = t.replace("@", "@⁠")                # word joiner: no mention, still readable
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = _MD.sub(r"\\\1", t)
    if len(t) > limit:
        t = t[:limit - 1].rstrip("\\") + "…"
    return t


def code(name) -> str:
    """An identifier inside backticks, or a plain-escaped fallback when it is not one."""
    s = str(name)
    if NAME_OK.match(s):
        return "`%s`" % s
    return plain(s, 160)
