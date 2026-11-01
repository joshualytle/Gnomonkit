"""Turn run records into counts, a significance test and a ship-or-hold verdict.

Counts are always k/n with errors excluded from n and reported beside it. Percentages only appear
once n >= 10, and never without the k/n behind them.
"""
from __future__ import annotations

from collections import defaultdict
from math import comb

P_THRESHOLD = 0.10
CONTROL_SLACK = 1          # a negative control may lose at most this many more runs than the base arm


def _ok(r: dict) -> bool:
    """Counted in n? Infrastructure errors are excluded; a run where the agent itself timed out or ran
    out of turns is counted, as a failure, so an arm that makes the model loop cannot hide it."""
    return not r.get("error") or r.get("error_kind") == "behaviour"


def tally(records: list[dict]) -> dict:
    t: dict = defaultdict(lambda: {"k": 0, "n": 0, "errors": 0})
    for r in records:
        cell = t[(r["family"], r["scenario"], r["arm"])]
        if not _ok(r):
            cell["errors"] += 1
            continue
        cell["n"] += 1
        cell["k"] += 1 if r.get("applied") is True else 0
    return dict(t)


def family_tally(records: list[dict]) -> dict:
    t: dict = defaultdict(lambda: {"k": 0, "n": 0, "errors": 0})
    for r in records:
        if r.get("polarity") != "positive":
            continue
        cell = t[(r["family"], r["arm"])]
        if not _ok(r):
            cell["errors"] += 1
            continue
        cell["n"] += 1
        cell["k"] += 1 if r.get("applied") is True else 0
    return dict(t)


def fisher_one_sided(k_new: int, n_new: int, k_base: int, n_base: int) -> float:
    """P(X >= k_new) under the hypergeometric: the chance the new arm does this well if both arms
    were really the same."""
    big_k, big_n = k_new + k_base, n_new + n_base
    if n_new == 0 or big_n == 0:
        return 1.0
    total = comb(big_n, n_new)
    hits = sum(comb(big_k, x) * comb(big_n - big_k, n_new - x) for x in range(k_new, min(big_k, n_new) + 1))
    return hits / total


def gate(records: list[dict], family: str, new_arm: str, base_arm: str = "written") -> dict:
    ft = family_tally(records)
    new = ft.get((family, new_arm), {"k": 0, "n": 0})
    base = ft.get((family, base_arm), {"k": 0, "n": 0})
    p = fisher_one_sided(new["k"], new["n"], base["k"], base["n"])

    fails: dict = defaultdict(int)
    present: set = set()
    for r in records:
        if r.get("polarity") != "negative-control" or not _ok(r) or r["arm"] not in (new_arm, base_arm):
            continue
        present.add((r["scenario"], r["arm"]))
        if r.get("applied") is not True:
            fails[(r["scenario"], r["arm"])] += 1
    controls_ok = True
    for sc in {s for s, _ in present}:
        if (sc, new_arm) in present and (sc, base_arm) in present:
            if fails[(sc, new_arm)] - fails[(sc, base_arm)] > CONTROL_SLACK:
                controls_ok = False
    return {"k_new": new["k"], "n_new": new["n"], "k_base": base["k"], "n_base": base["n"], "p": p,
            "controls_ok": controls_ok,
            "verdict": "ship" if p < P_THRESHOLD and controls_ok else "hold"}


def floor_flags(records: list[dict]) -> list[str]:
    """Positive scenarios that plain Claude (`bare`) already gets right in most runs: too easy."""
    k: dict = defaultdict(int)
    n: dict = defaultdict(int)
    for r in records:
        if r["arm"] == "bare" and r.get("polarity") == "positive" and _ok(r):
            n[r["scenario"]] += 1
            k[r["scenario"]] += 1 if r.get("applied") is True else 0
    return sorted(s for s in n if k[s] * 2 > n[s])


def _kn(records: list[dict]) -> dict:
    t: dict = defaultdict(lambda: [0, 0])
    for r in records:
        if _ok(r):
            t[(r["scenario"], r["arm"])][1] += 1
            t[(r["scenario"], r["arm"])][0] += 1 if r.get("applied") is True else 0
    return t


def compare(old: list[dict], new: list[dict]) -> list[dict]:
    a, b = _kn(old), _kn(new)
    rows = []
    for key in sorted(set(a) | set(b)):
        ok, on = a.get(key, [0, 0])
        nk, nn = b.get(key, [0, 0])
        delta = (nk / nn if nn else 0.0) - (ok / on if on else 0.0)
        rows.append({"scenario": key[0], "arm": key[1], "old": f"{ok}/{on}", "new": f"{nk}/{nn}", "delta": delta})
    return rows


def fmt_kn(k: int, n: int) -> str:
    return f"{k}/{n} ({round(100 * k / n)}%)" if n >= 10 else f"{k}/{n}"
