"""Is a NEW dependency name one edit away from a popular one?

Popular-name lists live in lists/ (source and date on each file's first line). A name that is
itself popular is never a squat. Matches: Damerau-Levenshtein distance <= max_distance (one more
for long names), the same letters with different separators (python-dateutil vs
python_dateutil vs pythondateutil), or a popular unscoped npm name re-published under a scope.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

LISTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lists")
_FILES = {"npm": "popular-npm.txt", "pypi": "popular-pypi.txt", "cargo": "popular-cargo.txt",
          "pub": "popular-pub.txt"}


@lru_cache(maxsize=None)
def popular(eco: str) -> frozenset:
    f = _FILES.get(eco)
    if not f:
        return frozenset()
    names = []
    with open(os.path.join(LISTS, f), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                names.append(_norm(eco, line))
    return frozenset(names)


def _norm(eco: str, name: str) -> str:
    n = name.lower()
    if eco == "pypi":
        n = re.sub(r"[-_.]+", "-", n)
    return n


def distance(a: str, b: str, cap: int = 3) -> int:
    """Optimal-string-alignment Damerau-Levenshtein, early exit above cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2 = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
            best = min(best, cur[j])
        if best > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[len(b)]


def check(eco: str, name: str, max_distance: int = 1, long_name: int = 10):
    """(popular name it imitates, how) or None."""
    pop = popular(eco)
    if not pop:
        return None
    n = _norm(eco, name)
    if n in pop:
        return None
    bare = re.sub(r"[-_.]", "", n.split("/")[-1])
    if eco == "npm" and n.startswith("@") and n.split("/", 1)[-1] in pop:
        return n.split("/", 1)[-1], "a popular unscoped name re-published under a scope"
    limit = max_distance + (1 if len(n) >= long_name else 0)
    best = None
    for p in pop:
        pb = re.sub(r"[-_.]", "", p)
        if abs(len(p) - len(n)) > limit and pb != bare:
            continue
        if pb == bare and p != n:
            return p, "same letters, different separators"
        d = distance(n, p, limit)
        if d <= limit and len(p) >= 4 and (best is None or d < best[1] or (d == best[1] and p < best[0])):
            best = (p, d)
    if best:
        return best[0], "edit distance %d" % best[1]
    return None
