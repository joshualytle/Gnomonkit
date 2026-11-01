#!/usr/bin/env python3
"""provenance_check -- keep evidence drawn from ONE real setup out of this repository.

leakcheck stops names, hosts, paths and secrets. It cannot see the other way a personal setup leaks
into a shared toolkit: numbers and stories measured on someone's own sessions, prompts and disk.
"Replayed 4,210 typed prompts", "on one workstation", "the owner's transcripts", "Q:\\Example" name
nobody, but they describe one person's machine and habits. Rules belong here; that evidence belongs
in the maintainer's private overlay.

What it flags (case-insensitive), as `path:line: owner-evidence/<rule>` -- never the text itself:
  count     a count of real-use units: "18,400 commands", "812 subagent spawns", "4,210 typed prompts"
            (every number in this file is made up)
  whose     someone's own data: "one user's transcripts", "the owner's prompts", "our sessions"
  replay    measured or replayed on real/local/own data: "replayed ... local transcripts"
  machine   "on this machine", "on one workstation", "from the owner's desktop"
  drive     an absolute path on a drive other than C: (a personal data drive, e.g. a scratch disk)

Only ADDED text is checked (a diff against the base, the staged change, or stdin), so text that
predates this check does not block every PR. A line that is generic on purpose carries
`provenance-ok: <reason>`, and the reason must say why the content is not one setup's evidence.

Modes
  --diff BASE..HEAD   lines added between two commits (CI)
  --staged            lines added in the staged change (pre-commit hook)
  --stdin NAME        text on stdin, reported as NAME (commit messages, PR title and body)
Exit codes: 0 clean, 1 findings, 2 usage error.
"""
import argparse
import re
import subprocess
import sys

UNITS = r"(?:prompts|transcripts|commands|sessions|subagent\s+spawns|spawns|corrections|tool\s+calls|messages)"
# Calibrated against PR bodies written from real data (known positives), the repo's earlier PR bodies
# (known negatives) and every line already in the repo. Generic phrasings that must stay quiet:
# "on this machine" in CLI help, "~20 messages" as a threshold, "the owner's data proves the numbers".
RULES = [
    # a hundred-plus real-use units, or any count that says it was real/typed/recorded
    ("count", re.compile(r"(?:\b\d{3,}|\b\d{1,3}(?:,\d{3})+)\s+(?:real\s+|typed\s+|local\s+|recorded\s+|logged\s+)?"
                         r"(?:user\s+)?" + UNITS + r"\b"
                         r"|\b\d+\s+(?:real|typed|recorded|logged)\s+(?:user\s+)?" + UNITS + r"\b", re.I)),
    ("whose", re.compile(r"\b(?:one\s+user'?s|the\s+owner'?s|this\s+owner'?s|our|my)\s+(?:own\s+)?(?:real\s+)?"
                         r"(?:transcripts|prompts|sessions|history|disk|machine|workstation)\b", re.I)),
    ("replay", re.compile(r"\b(?:replay(?:ed|ing)?|measured|calibrated|harvested|mined)\b[^.\n]{0,60}"
                          r"\b(?:real|actual|local|own)\b[^.\n]{0,30}\b(?:transcripts?|prompts|sessions|commands|history|data|requests)\b",
                          re.I)),
    # "a real run over the disk found 19 items (9.4 GB)": a real-data result with a measured quantity
    ("realrun", re.compile(r"\breal\s+(?:run|runs|round|data|disk|machine|history|corpus)\b[^.\n]{0,100}"
                           r"\b\d[\d,.]*\s*(?:items|GB|MB|TB|files|folders|worktrees|commits|%|" + UNITS[3:-1] + r")\b",
                           re.I)),
    # "on one workstation", or a result taken here: "real run on this machine", "measured on this machine".
    # Plain "run on this machine" (CLI help) stays quiet.
    ("machine", re.compile(r"\b(?:on|from)\s+(?:one|the\s+owner'?s|a\s+single|my|our)\s+"
                           r"(?:machine|workstation|desktop|laptop|disk|drive)\b"
                           r"|\b(?:real\s+runs?|measured|ran|verified|observed)\s+on\s+this\s+(?:machine|workstation|desktop)\b",
                           re.I)),
    ("drive", re.compile(r"(?<![A-Za-z])[D-Z]:[\\/][A-Za-z]")),
]
OK = re.compile(r"provenance-ok:\s*\S")
# The detector's own source and tests are made of the phrasings it looks for.
SELF = {"scripts/provenance_check.py", "tests/test_provenance_check.py"}


def scan(lines):
    """lines: iterable of (where, lineno, text). Yields (where, lineno, rule)."""
    for where, n, text in lines:
        if where.replace("\\", "/") in SELF or OK.search(text):
            continue
        for name, rx in RULES:
            if rx.search(text):
                yield where, n, name
                break


def added_lines(diff_text):
    where, n = None, 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            where = raw[6:] if raw.startswith("+++ b/") else raw[4:]
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            n = int(m.group(1)) if m else 0
            continue
        if where is None or where == "/dev/null":
            continue
        if raw.startswith("+"):
            yield where, n, raw[1:]
            n += 1
        elif not raw.startswith("-"):
            n += 1


def git_diff(*args):
    r = subprocess.run(["git", "diff", "--unified=0", "--no-color", "--no-ext-diff", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("provenance: git diff failed: " + r.stderr.strip()[:200], file=sys.stderr)
        sys.exit(2)
    return r.stdout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--diff", metavar="BASE..HEAD")
    g.add_argument("--staged", action="store_true")
    g.add_argument("--stdin", metavar="NAME")
    a = ap.parse_args(argv)
    if a.stdin:
        text = sys.stdin.read()
        lines = ((a.stdin, i, t) for i, t in enumerate(text.splitlines(), 1))
    elif a.staged:
        lines = added_lines(git_diff("--cached"))
    else:
        if ".." not in a.diff:
            print("provenance: --diff needs BASE..HEAD", file=sys.stderr)
            return 2
        base, head = a.diff.split("..", 1)
        lines = added_lines(git_diff(base, head))
    found = list(scan(lines))
    for where, n, rule in found:
        print(f"{where}:{n}: owner-evidence/{rule}")
    if found:
        print(f"provenance: {len(found)} line(s) read as evidence from one real setup. Move the numbers and stories "
              "to the private overlay, keep the rule here; or mark a line that is generic on purpose with "
              "`provenance-ok: <why>`.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
