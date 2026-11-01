"""The Updates tab: what the sync gate holds, and the Draft approval button (docs/SYNC.md).

READ. public_view() reads the gate's own files under $GNOMONKIT_HOME/sync/ (sources.json and held/*.json)
and keeps a whitelist: source names, branches, commit ids, states, each held update's reasons (kind,
text, the contributor named, the paths touched) and its file-level summary (path, lines added and
deleted). Never a remote URL, an error message (either can carry a host or a credential), or the contents
of a file.

DRAFT. draft(id) runs core's `gnomonkit sync approve-draft <id> --json`, which pushes a gnomonkit-sync/*
branch to the user's overlay and opens the pull request. Merging that pull request is the approval; the
portal never merges and never writes the gate's files. The server offers this only in plain loopback
mode (server.approval_allowed).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import paths

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}-([0-9a-f]{12}|signers)$")
SHA = re.compile(r"[0-9a-f]{7,64}$")
STATES = ("current", "advanced", "held", "unverified", "error", "unchecked")
REASON_KINDS = ("unsigned", "unknown-key", "bad-signature", "expired", "revoked", "unverifiable", "rewritten",
                "new-contributor", "overlay-unavailable")
CLI_REL = Path("gnomonkit.py")


def sync_dir() -> Path:
    return paths.gnomonkit_home() / "sync"


def _s(v, n: int = 300) -> str | None:
    return str(v)[:n] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else None


def _sha(v) -> str | None:
    v = str(v or "")
    return v if SHA.match(v) else None


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _reason(r: dict) -> dict:
    out = {"kind": r.get("kind") if r.get("kind") in REASON_KINDS else "other", "text": _s(r.get("text"), 500),
           "commit": _sha(r.get("commit"))}
    if r.get("kind") == "new-contributor":
        out["who"] = _s(r.get("who"), 200)
        out["paths"] = [_s(p, 300) for p in (r.get("paths") or [])[:40] if _s(p)]
    return out


def held_view(h: dict) -> dict:
    files = [{"path": _s(f.get("path"), 300), "added": _int(f.get("added")), "deleted": _int(f.get("deleted"))}
             for f in (h.get("files") or [])[:200] if isinstance(f, dict) and _s(f.get("path"))]
    return {"id": h.get("id"), "source": _s(h.get("source"), 64), "branch": _s(h.get("branch"), 100),
            "pin": _sha(h.get("pin")), "head": _sha(h.get("head")), "commits": _int(h.get("commits")),
            "created": _s(h.get("created"), 40),
            "reasons": [_reason(r) for r in (h.get("reasons") or [])[:50] if isinstance(r, dict)],
            "files": files, "files_total": _int(h.get("files_total")) or len(files)}


def public_view() -> dict:
    d = paths.read_json(sync_dir() / "sources.json", {}) or {}
    raw = d.get("sources") if isinstance(d, dict) and isinstance(d.get("sources"), dict) else {}
    sources = []
    for name, src in sorted(raw.items()):
        if not isinstance(src, dict) or not ID.match(str(name) + "-signers"):
            continue
        last = src.get("last") if isinstance(src.get("last"), dict) else {}
        state = last.get("state") if last.get("state") in STATES else "unchecked"
        held = last.get("held") if ID.match(str(last.get("held") or "")) else None
        sources.append({"name": name, "branch": _s(src.get("branch"), 100), "state": state,
                        "pin": _sha(last.get("pin")), "head": _sha(last.get("head")), "checked": _s(last.get("checked"), 40),
                        "held": held, "summary": _s(last.get("summary"), 600) if state in ("held", "unverified") else None,
                        "signers_draft": "%s-signers" % name if _int(last.get("suggested_signers")) else None})
    held = []
    try:
        names = sorted(p.name for p in (sync_dir() / "held").iterdir() if p.suffix == ".json")
    except OSError:
        names = []
    for n in names:
        h = paths.read_json(sync_dir() / "held" / n, None)
        if isinstance(h, dict) and ID.match(str(h.get("id") or "")) and h.get("id") + ".json" == n:
            held.append(held_view(h))
    ov = d.get("overlay") if isinstance(d, dict) else None
    return {"sources": sources, "held": held, "overlay": ov if isinstance(ov, bool) else None,
            "checked_at": d.get("checked") if isinstance(d, dict) and isinstance(d.get("checked"), (int, float)) else None}


def offered_ids(view: dict) -> set:
    """The ids the Draft approval button may be pressed for: held updates and pending release-key drafts."""
    return {h["id"] for h in view.get("held") or []} | {s["signers_draft"] for s in view.get("sources") or []
                                                         if s.get("signers_draft")}


# ---- core's command ------------------------------------------------------------------------------------
def find_core_cli() -> Path | None:
    """$GNOMONKIT_CLI, else core's gnomonkit.py beside this plugin in a checkout, else the newest core in the
    Claude Code plugin cache."""
    env = os.environ.get("GNOMONKIT_CLI")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    sibling = paths.PLUGIN_ROOT.parent / "core" / CLI_REL
    if sibling.is_file():
        return sibling
    best = None
    try:
        for mkt in (paths.claude_home() / "plugins" / "cache").iterdir():
            core = mkt / "core"
            for d in core.iterdir() if core.is_dir() else []:
                v = paths._version(d.name)
                if v and (d / CLI_REL).is_file() and (best is None or v > best[0]):
                    best = (v, d / CLI_REL)
    except OSError:
        return None
    return best[1] if best else None


def core(args: list, runner=None, timeout: int = 300):
    """Run core's CLI with `args`; None when no core is found."""
    cli = find_core_cli()
    if not cli:
        return None
    run = runner or subprocess.run
    return run([sys.executable, str(cli)] + args, capture_output=True, text=True, encoding="utf-8",
               errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)


def _run(args: list, runner=None, timeout: int = 300):
    return core(["sync"] + args, runner, timeout)


def draft(hid: str, runner=None) -> tuple:
    """(HTTP status, body) for the Draft approval button."""
    if not ID.match(hid or ""):
        return 400, {"error": "not an update id"}
    try:
        r = _run(["approve-draft", hid, "--json"], runner)
    except (OSError, subprocess.SubprocessError) as e:
        return 502, {"error": "the draft did not run (%s)" % type(e).__name__}
    if r is None:
        return 503, {"error": "core's gnomonkit command was not found"}
    if r.returncode != 0:
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        msg = lines[-1] if lines else "exit %d" % r.returncode
        return 502, {"error": msg[len("gnomonkit: "):] if msg.startswith("gnomonkit: ") else msg[:400]}
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return 502, {"error": "the draft gave no result"}
    return 200, {"ok": True, "id": hid, "branch": _s(out.get("branch"), 200), "already": bool(out.get("already")),
                 "pr": _s(out.get("pr"), 300), "compare": _s(out.get("compare"), 400)}


def check_due(runner=None) -> bool | None:
    """The nightly run: `gnomonkit sync check --if-due --quiet`. True when it ran clean, None without core."""
    try:
        r = _run(["check", "--if-due", "--quiet"], runner, timeout=1800)
    except (OSError, subprocess.SubprocessError):
        return False
    return None if r is None else r.returncode == 0
