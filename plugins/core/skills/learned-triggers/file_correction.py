#!/usr/bin/env python3
"""Record a user correction as a GitHub issue in the user's PRIVATE corrections repo.

    python file_correction.py list                               open issues, most repeated first
    python file_correction.py new --title T --body-file F        open an issue for a new missed trigger
    python file_correction.py repeat N --body-file F             the same correction again: comment on #N

The repo comes from --repo, the core plugin's corrections_repo option
(CLAUDE_PLUGIN_OPTION_CORRECTIONS_REPO), GNOMONKIT_CORRECTIONS_REPO, or the personal profile's
corrections.repo (`gnomonkit profile set corrections.repo=owner/name`). Every command checks the repo
is private first and refuses otherwise: a correction quotes the user. Bodies are read from a file
(no shell quoting) and common secrets are redacted before anything is sent.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

LABEL = "learned-trigger"
MAX_BODY = 4000
_SECRETS = [
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic|token)\s+)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[redacted]"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:token|secret|password|passwd|pwd|api[_-]?key|access[_-]?key)[A-Z0-9_]*\s*[=:]\s*)"
                r"['\"]?[^\s'\"&]+"), r"\1[redacted]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[redacted]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[redacted]"),
]


class Refused(Exception):
    pass


def redact(text: str) -> str:
    for rx, repl in _SECRETS:
        text = rx.sub(repl, text)
    return text


def _gh_argv() -> list[str]:
    raw = os.environ.get("GNOMONKIT_GH")      # tests point this at a fake
    if raw:
        argv = json.loads(raw)
        if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv):
            return argv
    return ["gh"]


def gh(*args: str) -> str:
    try:
        r = subprocess.run(_gh_argv() + list(args), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except FileNotFoundError:
        raise Refused("the GitHub CLI (gh) is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise Refused("gh timed out") from None
    if r.returncode != 0:
        raise Refused(f"gh {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def _profile(key: str):
    """The personal profile's value for `key` (checked against its schema), or None. Never raises."""
    try:
        core = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if core not in sys.path:
            sys.path.append(core)
        from gnomonkit_cmds import _common
        return _common.checked_profile_value(key)
    except Exception:  # noqa: BLE001 - the profile is optional
        return None


def repo_name(cli: str | None) -> str:
    repo = (cli or os.environ.get("CLAUDE_PLUGIN_OPTION_CORRECTIONS_REPO")
            or os.environ.get("GNOMONKIT_CORRECTIONS_REPO") or _profile("corrections.repo") or "").strip()
    if not repo:
        raise Refused("no corrections repo: set the core plugin's corrections_repo option, "
                      "GNOMONKIT_CORRECTIONS_REPO or the profile's corrections.repo "
                      "(gnomonkit profile set corrections.repo=owner/name) to a PRIVATE owner/name repo")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise Refused(f"corrections_repo must look like owner/name, got {repo!r}")
    return repo


def require_private(repo: str) -> None:
    info = json.loads(gh("repo", "view", repo, "--json", "isPrivate,nameWithOwner") or "{}")
    if info.get("isPrivate") is not True:
        raise Refused(f"{repo} is not private; corrections quote the user, so they only go to a private repo")


def read_body(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        body = redact(fh.read()).strip()
    if not body:
        raise Refused("the body file is empty")
    return body[:MAX_BODY]


def _send_body(body: str) -> str:
    fd, path = tempfile.mkstemp(prefix="gnomonkit-correction-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def cmd_list(repo: str) -> None:
    issues = json.loads(gh("issue", "list", "-R", repo, "--label", LABEL, "--state", "open",
                           "--limit", "100", "--json", "number,title,comments,url") or "[]")
    issues.sort(key=lambda i: len(i.get("comments") or []), reverse=True)
    if not issues:
        print(f"no open {LABEL} issues in {repo}")
    for i in issues:
        n = len(i.get("comments") or [])
        print(f"#{i['number']}  repeated {n}x  {i['title']}")


def cmd_new(repo: str, title: str, body_file: str) -> None:
    body = read_body(body_file)
    title = redact(title).strip()[:120]
    if not title:
        raise Refused("the title is empty")
    try:
        gh("label", "create", LABEL, "-R", repo, "--color", "0f766e",
           "--description", "A correction that should have been a trigger")
    except Refused:
        pass                                   # it already exists
    path = _send_body(body)
    try:
        print(gh("issue", "create", "-R", repo, "--title", title, "--label", LABEL, "--body-file", path).strip())
    finally:
        os.unlink(path)


def cmd_repeat(repo: str, number: str, body_file: str) -> None:
    if not re.fullmatch(r"[1-9][0-9]{0,6}", number):
        raise Refused(f"issue number must be a plain number, got {number!r}")
    body = read_body(body_file)
    path = _send_body(body)
    try:
        print(gh("issue", "comment", number, "-R", repo, "--body-file", path).strip())
    finally:
        os.unlink(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    n = sub.add_parser("new")
    n.add_argument("--title", required=True)
    n.add_argument("--body-file", required=True)
    r = sub.add_parser("repeat")
    r.add_argument("number")
    r.add_argument("--body-file", required=True)
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        repo = repo_name(a.repo)
        require_private(repo)
        if a.cmd == "list":
            cmd_list(repo)
        elif a.cmd == "new":
            cmd_new(repo, a.title, a.body_file)
        else:
            cmd_repeat(repo, a.number, a.body_file)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
