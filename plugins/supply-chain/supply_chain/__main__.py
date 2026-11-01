"""Command line: `python -I supply_review.py {patch,release} ...` (see --help).

Exit codes: 0 nothing flagged, 1 something flagged, 2 something could not be reviewed (bad input,
registry or network down, a download that failed). An archive the reader cannot parse is a HIGH
flag (exit 1), not a gap. A review that ran but hit a cap exits 0 and lists what it could not
check, so read the "Not checked" section before calling a bump clean.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import llm, patch, render, review
from .netfetch import HTTP, cache_dir


def _http(args) -> HTTP:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    return HTTP(timeout=args.timeout, cache=None if args.no_cache else cache_dir(), github_token=token)


def _opts(args) -> review.Options:
    return review.Options(committers=args.committers, old_ref=getattr(args, "old_ref", None),
                          new_ref=getattr(args, "new_ref", None), prefer=args.pypi_artifact,
                          extract_dir=getattr(args, "extract", None), diff_out=getattr(args, "diff_out", None),
                          model=not args.no_model)


def _write_json(path: "str | None", obj) -> None:
    if not path:
        return
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, default=str)
        fh.write("\n")


def _emit(md: str) -> None:
    try:
        sys.stdout.write(md)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(md.encode("utf-8", errors="replace"))


def _common(p) -> None:
    p.add_argument("--committers", default="auto",
                   help="new-committer check source: auto (the GitHub repo the registry names), off, "
                        "a path to a local clone of the upstream source, or owner/name on GitHub")
    p.add_argument("--pypi-artifact", default="auto", choices=("auto", "sdist", "wheel"),
                   help="PyPI: which artifact to diff (auto: sdist when both versions have one, else a wheel)")
    p.add_argument("--json", metavar="FILE", help="also write the full result as JSON")
    p.add_argument("--timeout", type=float, default=30, help="seconds per network request (default 30)")
    p.add_argument("--no-cache", action="store_true", help="do not cache downloaded artifacts on disk")
    p.add_argument("--no-model", action="store_true", help="skip the optional local-model summary")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="supply_review", description="Review what a dependency update actually "
                                 "changes: the released artifacts, old vs new, plus who wrote it.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("patch", help="review the dependency bumps in this repository's change")
    p.add_argument("--repo", default=".", help="repository root (default: current folder)")
    p.add_argument("--base", default="HEAD", help="the before side (default HEAD)")
    p.add_argument("--head", default=None, help="the after side (default: the working tree)")
    p.add_argument("--max", type=int, default=patch.MAX_PACKAGES, help="review at most this many packages")
    p.add_argument("--committer-checks", type=int, default=5,
                   help="run the new-committer check for the first N bumps (direct dependencies first)")
    _common(p)

    r = sub.add_parser("release", help="review one package release (a version range)")
    r.add_argument("eco", help="npm, pypi, cargo or pub")
    r.add_argument("name")
    r.add_argument("range", help="OLD..NEW, or NEW alone (compared with the release before it), or 'latest'")
    r.add_argument("--old-ref", help="source tag or commit for the old version (when tags are unusual)")
    r.add_argument("--new-ref", help="source tag or commit for the new version")
    r.add_argument("--extract", metavar="EMPTY_DIR", help="also unpack both artifacts here for reading (never run them)")
    r.add_argument("--diff-out", metavar="FILE", help="write the full unified diff of the text changes here")
    _common(r)

    args = ap.parse_args(argv)
    engine = review.Engine(_http(args), model=llm.Local())

    if args.cmd == "patch":
        res = patch.run(engine, os.path.abspath(args.repo), args.base, args.head, _opts(args), args.max,
                        args.committer_checks)
        _emit(render.patch(res))
        _write_json(args.json, {"schema": "gnomonkit/supply-chain-patch/v1", "base": res.base, "head": res.head,
                                "files": res.files, "skipped": res.skipped, "errors": res.errors,
                                "reports": [x.as_dict() for x in res.reports]})
        if any(x.flags for x in res.reports):
            return 1
        # Any package that could not be reviewed (a download failed, a lockfile would not parse) means
        # the change is not cleared: exit 2, never 0.
        if res.errors or any(x.error for x in res.reports):
            return 2
        return 0

    old, _, new = args.range.partition("..")
    if not _:
        old, new = None, args.range
    rep = engine.review(args.eco, args.name, new or "latest", old or None, _opts(args))
    _emit(render.report(rep))
    _write_json(args.json, rep.as_dict())
    if rep.flags:
        return 1
    return 2 if rep.error else 0


if __name__ == "__main__":
    sys.exit(main())
