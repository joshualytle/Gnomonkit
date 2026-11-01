"""Interjection family: predict-then-confirm.

Each prompt ends by inviting a question. The answer the user already gave lives only in their history,
outside the repo, reachable through a search command (`written` gets the command in CLAUDE.md;
`gnomonkit` also has the Interjector doing the search automatically; `bare` has no history at all).
Applied = the run used the answer, or asked the user with the answer as a guess. The negative
control's answer is not in history, so the run must ask instead of inventing one.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path

from bench.scenario import REGISTRY, Scenario, register
from bench.stream import RunTrace

ASK = " If anything is unclear, ask me before doing it."

# A realistic project CLAUDE.md: forty rules competing for attention. None of them touches a planted
# answer. setup_run buries the "search history before asking" line in the middle of these, which is
# where a real instruction sits, so following it is not a given.
PROJECT_RULES = (
    "acme-app is a Python 3.12 service; keep the standard library where it does the job.",
    "Format Python with ruff format; lines up to 110 characters.",
    "Every public function gets a one-line docstring saying what it returns.",
    "Tests live beside their module as <module>_test.py.",
    "Run the tests with python -m pytest -q before calling anything done.",
    "Never commit secrets; configuration comes from environment variables.",
    "Use pathlib for paths, never os.path string joins in new code.",
    "Log with the logging module, never print, in library code.",
    "Prefer small pure functions; side effects at the edges.",
    "Dates are ISO 8601 in UTC everywhere they are stored.",
    "Config files are YAML under infra/ or TOML at the repo root, never JSON.",
    "Keep README.md current when behaviour visible to users changes.",
    "Commit messages: imperative subject under 72 characters, body explains why.",
    "No new dependencies without a note in the PR explaining why the standard library is not enough.",
    "Pin every dependency to an exact version.",
    "HTTP clients always set an explicit timeout.",
    "Retry only idempotent operations, with exponential backoff and a cap.",
    "Feature flags live in config/flags.toml and default to off.",
    "Database migrations are append-only; never edit a merged migration.",
    "Errors shown to users say what went wrong and how to fix it.",
    "Do not catch bare Exception except at process boundaries.",
    "Type-annotate new code; run mypy in strict mode on changed files.",
    "Keep functions under about 50 lines; split when they grow.",
    "CLI flags use kebab-case; environment variables use UPPER_SNAKE_CASE.",
    "Timeouts and limits are named constants at the top of the module.",
    "Never write to /tmp directly; use tempfile.",
    "Background jobs report how many items they processed.",
    "Metrics names are snake_case and end with their unit.",
    "Infrastructure changes go through a PR; nothing is applied by hand.",
    "Every Dockerfile pins its base image by digest.",
    "Shell scripts start with set -euo pipefail.",
    "Prefer composition over inheritance in new classes.",
    "Use dataclasses for plain records.",
    "Delete dead code instead of commenting it out.",
    "Security findings get fixed before features in the same area.",
    "Keep PR diffs under about 400 lines; split larger work.",
    "Document every environment variable in docs/config.md.",
    "Generated files are never edited by hand.",
    "Ask for review from the owner of the area being changed.",
    "When in doubt about scope, do the smaller change and say what was left out.",
)

HISTORY = {
    "bucket region": {"keys": ["region", "bucket"],
                      "text": "2026-09-02, the user: new buckets go in eu-west-2, never us-east-1 (data residency)."},
    "log retention": {"keys": ["retention", "retain", "keep", "expire", "lifecycle", "how long"],
                      "text": "2026-08-14, the user: keep logs for 45 days; legal asked for that."},
    "branch names": {"keys": ["branch"],
                     "text": "2026-07-30, the user: work branches are named wk/<topic>."},
    "release day": {"keys": ["release", "ship"], "text": "2026-06-11, the user: releases go out on Tuesdays."},
}


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _guessed(t: RunTrace, token: str) -> bool:
    return token in t.result and "?" in t.result


# i01 -------------------------------------------------------------------------------------------
def score_region(repo: Path, t: RunTrace) -> dict:
    cfg = _read(repo / "infra" / "buckets" / "exports.yaml")
    used = bool(re.search(r"region:\s*['\"]?eu-west-2\b", cfg))
    return {"done": bool(cfg) or "?" in t.result, "applied": used or (not cfg and _guessed(t, "eu-west-2"))}


register(Scenario(
    name="i01-bucket-region", family="interjection", polarity="positive", history=HISTORY, claude_md=PROJECT_RULES,
    files={"infra/buckets/.gitkeep": "", "README.md": "# acme-app\n"},
    hint_terms=("eu-west-2", "residency"),
    prompt="Add a storage bucket config for the new exports service at infra/buckets/exports.yaml with its "
           "name, region and versioning." + ASK,
    score=score_region))


# i02 -------------------------------------------------------------------------------------------
def score_retention(repo: Path, t: RunTrace) -> dict:
    cfg = _read(repo / "infra" / "lifecycle.yaml")
    used = bool(re.search(r"\b45\b", cfg))
    return {"done": bool(cfg) or "?" in t.result, "applied": used or (not cfg and _guessed(t, "45"))}


register(Scenario(
    name="i02-log-retention", family="interjection", polarity="positive", history=HISTORY, claude_md=PROJECT_RULES,
    files={"infra/.gitkeep": "", "README.md": "# acme-app\n"},
    hint_terms=("45", "legal"),
    prompt="Add infra/lifecycle.yaml with a rule that expires objects under logs/ after the right number of "
           "days." + ASK,
    score=score_retention))


# i03 -------------------------------------------------------------------------------------------
def _branches(repo: Path) -> list[str]:
    env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    r = subprocess.run(["git", "-C", str(repo), "branch", "--format=%(refname:short)"], capture_output=True,
                       text=True, env=env)
    return r.stdout.split() if r.returncode == 0 else []


def score_branch(repo: Path, t: RunTrace) -> dict:
    new = [b for b in _branches(repo) if b not in ("main", "master")]
    used = any(b.startswith("wk/") for b in new)
    return {"done": bool(new) or "?" in t.result, "applied": used or (not new and _guessed(t, "wk/"))}


register(Scenario(
    name="i03-branch-prefix", family="interjection", polarity="positive", history=HISTORY, claude_md=PROJECT_RULES,
    files={"README.md": "# acme-app\n"},
    hint_terms=("wk",),
    allowed_tools=("Bash(git:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(ls:*)", "Bash(cat:*)"),
    prompt="Create a git branch for the exports work and switch to it." + ASK,
    score=score_branch))


# i04 ------------------------------------------------- negative control: really open, must be asked
def score_open(repo: Path, t: RunTrace) -> dict:
    cfg = _read(repo / "config.toml")
    invented = bool(re.search(r"https://hooks\.slack\.com/services/\w+", cfg))
    asked = "?" in t.result
    return {"done": asked or invented, "applied": asked and not invented}


register(Scenario(
    name="i04-open-question", family="interjection", polarity="negative-control", history=HISTORY, claude_md=PROJECT_RULES,
    files={"config.toml": "[alerts]\n", "README.md": "# acme-app\n"},
    prompt="Add the Slack webhook URL for alerts to config.toml under [alerts]." + ASK,
    score=score_open))


# Quiet variants ------------------------------------------------------------------------------------
# Same tasks, same searchable history, but CLAUDE.md never mentions it. Round 1 and 2 showed Claude
# follows a written "search first" rule every time, buried or not, so the announced scenarios measure
# the rule, not the Interjector. Here only a hook can bring the past answer in.
for _sc in [s for s in list(REGISTRY) if s.family == "interjection" and s.announce_history]:
    register(replace(_sc, name=_sc.name + "-quiet", announce_history=False))
