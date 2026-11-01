"""Skills family: a core skill's situation comes up and the behaviour it teaches should happen.

No CLAUDE.md lines: the knowledge is the core skill itself, present in both `written` (skills only)
and `gnomonkit` (skills plus the hooks that suggest them). Scorers are static checks on the files
the run wrote; they never execute generated code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from bench.scenario import Action, Scenario, register
from bench.stream import RunTrace, suggested_skills

DIRECT = " Do the work directly, without asking questions."


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _quiet(t: RunTrace) -> bool:
    return not any(suggested_skills(h) for h in t.hooks)


# a01 ---------------------------------------------------------- dependency pin
def score_pin(repo: Path, t: RunTrace) -> dict:
    try:
        deps = json.loads(_read(repo / "package.json")).get("dependencies") or {}
    except ValueError:
        deps = {}
    v = deps.get("dayjs")
    return {"done": v is not None, "applied": bool(v) and re.fullmatch(r"\d+\.\d+\.\d+", str(v)) is not None}


register(Scenario(
    name="a01-dependency-pin", family="skills", polarity="positive",
    files={"package.json": json.dumps({"name": "acme-app", "version": "1.0.0",
                                       "dependencies": {"express": "4.19.2", "zod": "3.23.8"}}, indent=2) + "\n",
           "index.js": "const express = require('express');\n"},
    hint_terms=("exact", "pin", "pinned", "version"),
    expect_skill="package-manager-pinning",
    action=Action(("Write", "Edit"), r"(^|/)package\.json$"),
    prompt="Add dayjs as a dependency for date formatting by editing package.json directly; do not run npm." + DIRECT,
    score=score_pin))


# a02 ------------------------------------------------------- job reports counts
COUNTER = re.compile(r"\b(\w+)\s*\+=\s*1\b|\b(\w+)\s*=\s*len\(")
REPORT_CALL = re.compile(r"\b(print|log\w*\.\w+|logger\.\w+|logging\.\w+|sys\.std\w+\.write)\(([^\n]*)")


def score_job(repo: Path, t: RunTrace) -> dict:
    """Applied when the job keeps a count of what it did, reports that count, and branches on the count
    being zero. A per-file print or a directory-exists check is not a count."""
    src = _read(repo / "jobs" / "nightly_cleanup.py")
    counters = {a or b for a, b in COUNTER.findall(src)}
    reported = {n for n in counters for _, args in REPORT_CALL.findall(src) if re.search(rf"\b{re.escape(n)}\b", args)}
    zero = {n for n in reported
            if re.search(rf"\b{re.escape(n)}\s*(==\s*0|<\s*1|<=\s*0)\b|\bnot\s+{re.escape(n)}\b|\b{re.escape(n)}\s*==\s*0", src)}
    return {"done": bool(src.strip()), "applied": bool(zero)}


register(Scenario(
    name="a02-nightly-job-counts", family="skills", polarity="positive",
    files={"jobs/.gitkeep": "", "app.py": "def main():\n    return 0\n"},
    hint_terms=("count", "zero", "report", "silently"),
    expect_skill="silent-no-op",
    action=Action(("Write", "Edit"), r"(^|/)jobs/"),
    prompt="Write jobs/nightly_cleanup.py that deletes files older than 30 days from tmp_exports/." + DIRECT,
    score=score_job))


# a03 ---------------------------------------------------------- runbook impact
IMPACT = re.compile(r"\b(impact\w*|downtime|unavailable|interrupt\w*|outage|disrupt\w*)\b", re.I)
VERIFY = re.compile(r"\b(verif\w*|confirm\w*|health\s*check|check\s+that)\b", re.I)


def score_runbook(repo: Path, t: RunTrace) -> dict:
    md = _read(repo / "runbooks" / "restart-ingest.md")
    return {"done": bool(md.strip()), "applied": bool(IMPACT.search(md)) and bool(VERIFY.search(md))}


register(Scenario(
    name="a03-runbook-impact", family="skills", polarity="positive",
    files={"runbooks/.gitkeep": "", "README.md": "# acme-app\n\nThe ingest worker reads the upload queue.\n"},
    hint_terms=("impact", "downtime", "unavailable"),
    expect_skill="production-impact-callout",
    action=Action(("Write", "Edit"), r"(^|/)runbooks/"),
    prompt="Write runbooks/restart-ingest.md: steps to restart the ingest worker." + DIRECT,
    score=score_runbook))


# a04 -------------------------------------------------- negative control: quiet
def score_rename(repo: Path, t: RunTrace) -> dict:
    src = _read(repo / "app.py")
    done = "def process_item" in src and re.search(r"\bproc\(", src) is None
    return {"done": done, "applied": done and _quiet(t)}


register(Scenario(
    name="a04-quiet-rename", family="skills", polarity="negative-control",
    files={"app.py": "def proc(x):\n    return x * 2\n\n\nprint(proc(1))\nprint(proc(2))\n"},
    prompt="Rename the function proc to process_item everywhere in app.py." + DIRECT,
    score=score_rename))


# a05 / a06 --------------------------------------- data questions (issue #29)
# report-pages' router trigger fires only when a report-shaped file is being WRITTEN; a plain data
# question can be answered as terminal text and never touch it. a05 asks for a breakdown someone has
# to decide on (a page is the standard); a06 asks for one number (a page would be noise).
REGIONS = ("north", "south", "east", "west")
_AUG = {"north": 41200, "south": 38950, "east": 27400, "west": 33100}
_SEP = {"north": 39800, "south": 44120, "east": 31950, "west": 32900}


def _orders_csv() -> str:
    rows = ["order_id,region,month,amount"]
    n = 1000
    for month, totals in (("2026-08", _AUG), ("2026-09", _SEP)):
        for r in REGIONS:
            # each region's month total split over four orders, so the answer needs an aggregation
            q = totals[r] // 4
            for amt in (q, q, q, totals[r] - 3 * q):
                n += 1
                rows.append(f"{n},{r},{month},{amt}")
    return "\n".join(rows) + "\n"


ORDERS = _orders_csv()
ORDER_ROWS = len(ORDERS.strip().splitlines()) - 1


def _pages_written(t: RunTrace) -> list[str]:
    """Contents of every HTML page the run wrote that carries the shared template's verdict sections."""
    out = []
    for c in t.tools:
        if c.name not in ("Write", "Edit"):
            continue
        path = str(c.input.get("file_path") or "")
        text = str(c.input.get("content") or c.input.get("new_string") or "")
        if path.lower().endswith((".html", ".htm")) and "data-ask" in text:
            out.append(text)
    return out


def score_breakdown(repo: Path, t: RunTrace) -> dict:
    said = (t.result or "") + " ".join(_pages_written(t))
    done = all(r in said.lower() for r in ("south", "east"))     # the two regions that grew
    return {"done": done, "applied": bool(_pages_written(t))}


def score_one_number(repo: Path, t: RunTrace) -> dict:
    done = re.search(rf"\b{ORDER_ROWS}\b", t.result or "") is not None
    return {"done": done, "applied": done and not _pages_written(t)}


register(Scenario(
    name="a05-data-breakdown-page", family="skills", polarity="positive",
    files={"data/orders.csv": ORDERS, "README.md": "# acme-sales\n\nMonthly order exports live in data/.\n"},
    hint_terms=("page", "template", "verdict"),
    expect_skill="report-pages",
    action=Action(("Write", "Edit"), r"\.html?$"),
    prompt=("Using data/orders.csv, which regions grew from August to September 2026 and by how much? "
            "I have to decide where next quarter's budget goes." + DIRECT),
    score=score_breakdown))

register(Scenario(
    name="a06-one-number-no-page", family="skills", polarity="negative-control",
    files={"data/orders.csv": ORDERS},
    prompt="How many orders are in data/orders.csv, not counting the header row?" + DIRECT,
    score=score_one_number))
