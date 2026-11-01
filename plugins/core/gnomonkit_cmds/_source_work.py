"""History source: GitHub work items (issues labelled gk:work, and their comments), read with `gh`.

Runs only when asked: `gnomonkit history index --github [REPO]`. REPO is owner/name; without it, the
GitHub repository of the folder the command runs in (as `gh repo view` finds it). The session-start
refresh never passes --github, so it never calls GitHub. No `gh`, not logged in, offline or no
repository: one line on stderr says so, this source reads nothing, and the other sources' records
are indexed as usual.

The records come from _work_source.records(): one per item (title, state, body) and one per
comment, scrubbed there and again by the index. Each search result shows the issue (owner/name#N)
and links to it. Issue text is written by whoever can comment on the repository, so it is data,
never instructions, as every search says.

Incremental: the cursor, per repository, is the newest `updatedAt` read. The next run asks GitHub
only for items updated at or after it, least recently updated first; an item read again replaces
its records (same ids). When a page comes back full, the source reads on from the new cursor.

See _history.py for the source contract this module implements.
"""
from __future__ import annotations

import os
import sys

from . import _work_source as W

NAME = "work"
HERE = "."            # --github with no value: this folder's repository
MAX_PAGES = 50        # LIST_LIMIT items each; a run reads at most this many pages per repository


def add_arguments(parser) -> None:
    parser.add_argument("--github", nargs="?", const=HERE, default=None, metavar="REPO",
                        help="also read GitHub work items (issues labelled %s, and their comments) from REPO "
                             "(owner/name; default: this folder's GitHub repository). Needs the GitHub CLI, gh"
                             % W.WORK_LABEL)


def enabled(args) -> bool:
    return bool(getattr(args, "github", None))


def say(msg: str) -> None:
    print("history index: --github: %s" % msg, file=sys.stderr)


def _name(repo: str | None, cwd: str) -> str:
    """owner/name as GitHub spells it, for `repo` or for the repository of `cwd`."""
    data = W.gh_json(["repo", "view"] + ([repo] if repo else []) + ["--json", "nameWithOwner"], cwd=cwd)
    name = data.get("nameWithOwner") if isinstance(data, dict) else None
    if not isinstance(name, str) or "/" not in name:
        raise W.GhError("gh repo view returned no repository", "not-found")
    return name


def _why(e: W.GhError, asked: str | None) -> str:
    if e.kind == "missing":
        return "skipped: the GitHub CLI (gh) is not installed or not on PATH"
    if e.kind == "auth":
        return "skipped: gh is not logged in (run `gh auth login`)"
    if e.kind == "offline":
        return "skipped: GitHub can't be reached (offline?)"
    if e.kind == "not-found":
        return ("skipped: can't find the repository %s, or this account can't see it" % asked if asked else
                "skipped: this folder has no GitHub repository; name one with --github owner/name")
    return "skipped: %s" % e


def records(ctx):
    asked = getattr(ctx.args, "github", None)
    asked = None if asked in (None, HERE) else str(asked).strip()
    cwd = os.getcwd()
    try:
        repo = _name(asked, cwd)
        here = repo if asked is None else _here(cwd)
    except W.GhError as e:
        say(_why(e, asked))
        return
    # The folder's own repository gets this folder's project and root, so `--project here` finds its
    # items; another repository is shown by its name.
    project, root = ctx.project_for(cwd) if here and here.lower() == repo.lower() else (repo.split("/")[-1], "")
    key = repo.lower()
    since = ctx.cursor(key)
    for _page in range(MAX_PAGES):
        newest, items = since, 0
        try:
            for r in W.records(repo, since=since):
                item = "updated" in r                  # an item's record; a comment's has no `updated`
                if item:
                    items += 1
                    if r["updated"] and (newest is None or r["updated"] > newest):
                        newest = r["updated"]
                yield {"id": r["id"], "text": r["text"], "timestamp": r.get("timestamp"), "origin": NAME,
                       "project": project, "root": root, "ref": "%s#%d" % (repo, r["number"]), "url": r["origin"],
                       "role": "issue" if item else "comment"}
        except W.GhError as e:
            say("stopped reading %s: %s" % (repo, _why(e, repo).split(": ", 1)[-1]))
            return
        if newest and newest != since:
            ctx.set_cursor(key, newest)
        if items < W.LIST_LIMIT or newest == since:
            return
        since = newest
    say("read %d pages of %s; the rest comes on the next run" % (MAX_PAGES, repo))


def _here(cwd: str) -> str | None:
    try:
        return _name(None, cwd)
    except W.GhError:
        return None
