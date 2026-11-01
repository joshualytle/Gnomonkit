"""Text helpers shared by the history index and the lessons register: words, shingles, repository roots.

Standard library only and cheap to import (the lessons hook loads it on every prompt).

    terms(text)            the distinct search words of a text, lower case, stop words dropped
    tokens(text)           every word, stemmed lightly, stop words dropped (for bm25 and shingles)
    shingles(text)         words plus adjacent word pairs, the unit of near-duplicate checks
    jaccard(a, b)          |a & b| / |a | b| for two shingle sets
    repo_root(path)        the git repository folder holding `path` (a .git folder or file), or None
    repo_name(path)        that folder's name, else the folder's own name
"""
from __future__ import annotations

import functools
import os
import re

_WORD = re.compile(r"[^\W_]+", re.U)   # letters and digits; "_" splits, as SQLite's unicode61 does

STOP = frozenset("""
a about above after again against all also am an and any are aren as at be because been before being
below between both but by can cannot could couldn did didn do does doesn doing don down during each
else even ever every few for from further get gets got had hadn has hasn have haven having he her here
hers herself him himself his how i if in into is isn it its itself just let lets like ll make me might
more most much must mustn my myself need no nor not now of off on once one only or other ought our ours
ourselves out over own please re really s same shall shan she should shouldn so some such t than that
the their theirs them themselves then there these they this those through to too under until up upon us
use used using ve very was wasn way we were weren what when where which while who whom why will with
won would wouldn yes yet you your yours yourself yourselves
""".split())


def words(text: str) -> list:
    """Every word of `text`, lower case, in order."""
    return [w.lower() for w in _WORD.findall(text or "")]


@functools.lru_cache(maxsize=1 << 16)
def stem(w: str) -> str:
    """A light suffix strip, so "deploying", "deployed" and "deploys" meet. Not Porter: close enough
    for matching a lesson against a prompt, and the same on every machine. Cached: a register
    repeats its words, and tokenising one is most of what indexing it costs."""
    if len(w) <= 4 or w.isdigit():
        return w
    for suf in ("ingly", "ations", "ation", "ings", "ing", "edly", "ies", "ied", "ers", "est", "ed", "es", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            if suf in ("ies", "ied"):
                w += "y"
            break
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls":
        w = w[:-1]                           # "committ" -> "commit", "stopp" -> "stop"
    return w


def tokens(text: str) -> list:
    """Stemmed words with stop words dropped, in order (repeats kept: bm25 counts them)."""
    return [stem(w) for w in words(text) if w not in STOP and len(w) > 1]


def terms(text: str, limit: int = 16) -> list:
    """The distinct search words of a query, unstemmed (the search engine stems them itself). When
    every word is a stop word, the words themselves, so a search for "what we did" still runs."""
    ws = words(text)
    keep = [w for w in ws if w not in STOP and len(w) > 1] or [w for w in ws if len(w) > 1]
    out: list = []
    for w in keep:
        if w not in out:
            out.append(w)
    return out[:limit]


def shingles(text: str) -> set:
    t = tokens(text)
    return set(t) | {a + " " + b for a, b in zip(t, t[1:])}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_ROOTS: dict = {}


def repo_root(path: str | None) -> str | None:
    """The nearest folder at or above `path` holding .git (a folder, or a file in a worktree). Cached."""
    if not path:
        return None
    p = os.path.abspath(os.path.expanduser(str(path)))
    if p in _ROOTS:
        return _ROOTS[p]
    found, cur = None, p
    for _ in range(40):
        if os.path.exists(os.path.join(cur, ".git")):
            found = cur
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    _ROOTS[p] = found
    return found


def repo_name(path: str | None) -> str:
    """The repository's folder name for `path`, else the last part of `path` itself ("" for none)."""
    root = repo_root(path)
    base = root or (os.path.abspath(os.path.expanduser(str(path))) if path else "")
    return os.path.basename(base.rstrip("\\/")) if base else ""
