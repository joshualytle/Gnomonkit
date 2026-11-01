"""Precedent family: a project rule exists in writing (CLAUDE.md) and the task never mentions it.

Each scenario plants a rule or a lesson as CLAUDE.md lines and checks that the agent follows it when
the task does not repeat it. A decision reachable only through memory search belongs to the memory
layer and is not here yet. Until then these run under `bare`, `written` and `custom` only.
"""
from __future__ import annotations

import re
from pathlib import Path

from bench.scenario import Scenario, register
from bench.stream import RunTrace, suggested_skills

ARMS_NOW = ("bare", "written", "custom")
DIRECT = " Do the work directly, without asking questions."


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _written_by_subagent(trace: RunTrace, name: str) -> bool:
    return any(c.sub and c.name in ("Write", "Edit", "MultiEdit") and
               str(c.input.get("file_path", "")).replace("\\", "/").endswith(name) for c in trace.tools)


def _files(repo: Path, pattern: str) -> list[Path]:
    return [p for p in repo.rglob(pattern) if ".git" not in p.parts]


# b01 ------------------------------------------------------------------- naming
def score_migration(repo: Path, t: RunTrace) -> dict:
    names = [p.name for p in (repo / "db" / "migrations").glob("*.sql")]
    return {"done": bool(names),
            "applied": any(re.fullmatch(r"\d{8}_\d{4}_[a-z0-9_]+\.sql", n) for n in names)}


register(Scenario(
    name="b01-migration-naming", family="precedent", polarity="positive", arms=ARMS_NOW,
    files={"db/migrations/.gitkeep": "", "app.py": "def main():\n    return 0\n",
           "README.md": "# acme-app\n\nInternal service.\n"},
    claude_md=("acme-app database migrations are files named <YYYYMMDD>_<HHMM>_<slug>.sql in db/migrations; "
               "the migration runner sorts by that timestamp and silently skips numbered names like "
               "001_init.sql.",),
    hint_terms=("timestamp", "YYYYMMDD", "HHMM"),
    prompt="Add a database migration that creates a `widgets` table with columns id (integer primary key) "
           "and name (text)." + DIRECT,
    score=score_migration))


# b02 ------------------------------------------------------------------ library
def score_httpx(repo: Path, t: RunTrace) -> dict:
    src = _read(repo / "fetch_status.py")
    return {"done": bool(src),
            "applied": "httpx" in src and not re.search(r"^\s*import requests", src, re.M)}


register(Scenario(
    name="b02-http-library", family="precedent", polarity="positive", arms=ARMS_NOW,
    files={"app.py": "def main():\n    return 0\n", "requirements.txt": "httpx==0.27.2\nrequests==2.32.3\n"},
    claude_md=("In acme-app, outbound HTTP calls use httpx with an explicit timeout; requests was removed from "
               "new code after a GET with no timeout hung the sync worker for a day.",),
    hint_terms=("httpx", "timeout"),
    prompt="Write fetch_status.py with a function fetch_status(url) that does an HTTP GET request and returns "
           "the response status code." + DIRECT,
    score=score_httpx))


# b03 ------------------------------------------------------------- command flag
def score_pytest(repo: Path, t: RunTrace) -> dict:
    src = _read(repo / "scripts" / "run_tests.sh")
    return {"done": bool(src), "applied": "no:cacheprovider" in src}


register(Scenario(
    name="b03-test-command-flag", family="precedent", polarity="positive", arms=ARMS_NOW,
    files={"scripts/.gitkeep": "", "calc.py": "def add(a, b):\n    return a + b\n",
           "calc_test.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"},
    claude_md=("acme-app's test suite is run with `python -m pytest -p no:cacheprovider -q`; a bare pytest "
               "writes .pytest_cache onto the read-only CI share and the job fails.",),
    hint_terms=("cacheprovider", "pytest_cache"),
    prompt="Add a script scripts/run_tests.sh that runs this project's test suite (pytest)." + DIRECT,
    score=score_pytest))


# b04 ----------------------------------------------------- test layout, subagent
def score_layout(repo: Path, t: RunTrace) -> dict:
    beside = (repo / "slugify_test.py").exists()
    wrong = (repo / "tests").exists() or (repo / "test_slugify.py").exists()
    return {"done": beside or wrong, "applied": beside and not wrong,
            "subagent": _written_by_subagent(t, "slugify_test.py") if beside else False}


register(Scenario(
    name="b04-test-layout-subagent", family="precedent", polarity="positive", arms=ARMS_NOW,
    files={"slugify.py": "import re\n\n\ndef slugify(s):\n    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')\n"},
    claude_md=("In acme-app, tests sit beside their module as <module>_test.py: never a tests/ folder and never a "
               "test_ prefix. The bundler only collects *_test.py next to the code.",),
    hint_terms=("beside", "bundler"),
    prompt="Use the Agent tool to have a general-purpose subagent write unit tests for slugify.py. You "
           "coordinate and report; the subagent writes the file. Do not ask questions.",
    score=score_layout))


# b05 ------------------------------------------------------ manifest, subagent
def score_manifest(repo: Path, t: RunTrace) -> dict:
    src = _read(repo / "archiver.py")
    has = "archive_old" in src
    return {"done": has, "applied": has and "MANIFEST.tsv" in src,
            "subagent": _written_by_subagent(t, "archiver.py")}


register(Scenario(
    name="b05-archive-manifest-subagent", family="precedent", polarity="positive", arms=ARMS_NOW,
    files={"archiver.py": "\"\"\"Archive helpers.\"\"\"\n"},
    claude_md=("Every acme-app archive move appends a line `<iso-time>\\t<source>\\t<dest>` to "
               "archive/MANIFEST.tsv; the restore tool reads only that manifest, so a file moved without a "
               "manifest line can never be restored.",),
    hint_terms=("manifest", "restore"),
    prompt="Delegate this to a general-purpose subagent with the Agent tool: add a function archive_old(paths) "
           "to archiver.py that moves each path into an archive/ folder and reports what it moved. Do not ask "
           "questions.",
    score=score_manifest))


# b06 --------------------------------------------------------- edit side effects
def score_schema(repo: Path, t: RunTrace) -> dict:
    src = _read(repo / "config_schema.py")
    changes = _read(repo / "CHANGES.md")
    m = re.search(r"SCHEMA_VERSION\s*=\s*(\d+)", src)
    return {"done": "retries" in src, "applied": bool(m and int(m.group(1)) == 5 and "retries" in changes)}


register(Scenario(
    name="b06-edit-effects", family="precedent", polarity="positive", arms=ARMS_NOW,
    files={"config_schema.py": "from dataclasses import dataclass\n\nSCHEMA_VERSION = 4\n\n\n@dataclass\n"
                               "class Settings:\n    host: str = 'localhost'\n    port: int = 8080\n",
           "CHANGES.md": "# Changes\n\n- v4: added port\n"},
    claude_md=("Whenever config_schema.py changes, bump SCHEMA_VERSION in it and add a line to CHANGES.md; the "
               "loader rejects caches written under another version, so a missed bump serves stale settings.",),
    hint_terms=("SCHEMA_VERSION", "CHANGES"),
    prompt="Add an optional integer field `retries` (default 3) to the Settings dataclass." + DIRECT,
    score=score_schema))


# b07 ------------------------------------------------------- deviation explained
DEVIATION = re.compile(r"\b(convention|preference|prefer\w*|standard|guideline|CLAUDE\.md|project rule|"
                       r"normally|usually|instead of|rather than|deviat\w*|exception)\b", re.I)


def score_deviation(repo: Path, t: RunTrace) -> dict:
    """The prompt itself names PowerShell, so mentioning it proves nothing. Applied only when the
    result names the standing preference it is departing from."""
    sh = (repo / "ci" / "smoke.sh").exists()
    explained = bool(re.search(r"PowerShell|\.ps1", t.result)) and bool(DEVIATION.search(t.result))
    return {"done": sh, "applied": sh and explained}


# The prompt names PowerShell on purpose: it is the reason to deviate. The rule being measured is
# "say so when you deviate from a standing preference", which the prompt does not mention.
register(Scenario(
    name="b07-deviation-explained", family="precedent", polarity="positive", arms=ARMS_NOW,
    files={"ci/.gitkeep": ""},
    claude_md=("Write new automation scripts in PowerShell (.ps1), not bash or sh. The maintainer works on "
               "Windows.",),
    prompt="The Linux CI runner has no PowerShell installed. Add ci/smoke.sh, a POSIX sh script that prints "
           "'smoke ok' and exits 0." + DIRECT,
    score=score_deviation))


# b08 ---------------------------------------------------------- negative control
def score_noise(repo: Path, t: RunTrace) -> dict:
    ok = "hello world" in _read(repo / "notes" / "hello.txt").lower()
    quiet = not any(suggested_skills(h) for h in t.hooks)
    return {"done": ok, "applied": ok and quiet, "quiet": quiet}


register(Scenario(
    name="b08-negative-control", family="precedent", polarity="negative-control", arms=ARMS_NOW,
    files={"notes/.gitkeep": ""},
    prompt="Create notes/hello.txt containing the words hello world.",
    score=score_noise))


# b09 -------------------------------------------------- newer rule outranks older
def score_toml(repo: Path, t: RunTrace) -> dict:
    toml = _files(repo, "*.toml")
    js = [p for p in _files(repo, "*.json") if "exporter" in p.name]
    return {"done": bool(toml or js), "applied": bool(toml) and not js}


register(Scenario(
    name="b09-entry-outranks-lesson", family="precedent", polarity="positive", arms=ARMS_NOW,
    files={"config/.gitkeep": "", "app.py": "def main():\n    return 0\n"},
    claude_md=("acme-app service configuration files are JSON documents under config/.",
               "Newer decision: in acme-app, config files are TOML, including new ones; the JSON configs are "
               "being migrated out."),
    hint_terms=("TOML", "JSON"),
    prompt="Add a config file for the new exporter service with the settings batch_size = 500 and "
           "target = \"s3\"." + DIRECT,
    score=score_toml))
