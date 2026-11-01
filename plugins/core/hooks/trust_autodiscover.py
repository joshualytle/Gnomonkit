#!/usr/bin/env python3
"""SessionStart (startup): trust new repositories under a folder-trust root with `auto = true`.

The levels are the [[trust]] tables in ~/.gnomonkit/grants.toml (gk_trust.py). At the start of a new session
(never resume, clear or compact) this hook:

  1. looks one level down each `trusted` root that has `auto = true` for folders holding `.git`, and reads
     each one's owner from its .git/config (no git process). A repository already trusted, under a more
     specific `per-project` or `never` root, or whose origin's owner is not in `owners`, is left alone;
  2. puts back the trust key on repositories Gnomonkit trusted before (here or with `gnomonkit trust sync`)
     when Claude Code's file has lost it, and logs that ("reapplied"). Only where step 1 would look: a
     repository directly inside a `trusted` root with `auto` on, at the path it was trusted at (not through
     a link made since); one trusted with `trust sync --depth` deeper down is left to `trust sync`;
  3. sets projects["<repository root>"].hasTrustDialogAccepted = true for the rest, through trust_write.py:
     each repository checked again against the levels right before the edit, the file's format checked, a
     backup kept, the file re-read just before an atomic swap and nothing written if it changed, the key
     read back afterwards and tried once more if it didn't stick, every change in
     ~/.gnomonkit/trust/audit.jsonl;
  4. shows one line per repository it trusted, at most MAX_LINES and then how many more.

A root whose folder's modification time and levels are as they were at the last complete scan is not
scanned again (a new clone directly under it changes the time). Everything, the write included, is done
by BUDGET_S after the hook starts, well inside the hook's timeout: looking stops WRITE_RESERVE_S before
that, and the checks right before the edit stop TAIL_S before it, leaving time for the edit, the swap, the
read-back and the audit lines. What it didn't reach waits for the next start (step 2 carries on where it
stopped; a root not scanned completely is scanned again).
GNOMONKIT_TRUST_AUTO=off turns the hook off without touching the levels.

Fails open: any error, a lock held by another Gnomonkit process, or a file it doesn't recognise prints
nothing and exits 0. It never blocks a session start.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

BUDGET_S = 5.0          # the whole run, from the hook's start (hooks.json gives it 10 s)
WRITE_RESERVE_S = 2.0   # of that, kept for the write: looking stops this long before the end
TAIL_S = 0.75           # and the write's checks stop this long before it (the edit and audit lines follow)
MAX_LINES = 10
OFF = ("0", "off", "false", "no")


def _event() -> dict:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        ev = json.loads(raw) if raw.strip() else {}
        return ev if isinstance(ev, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _mtime(folder: str):
    try:
        return os.stat(folder).st_mtime_ns
    except OSError:
        return None


def plan(levels, cfg, st: dict, deadline: float) -> tuple:
    """(items to trust, {root key: scan record} for the roots scanned completely, state keys to forget).
    Stops at `deadline`: the reapply pass gets at most half the time left and records where it stopped
    (st["cursor"]), so the next start carries on from there; a root not scanned completely is not recorded,
    so it is scanned again."""
    import gk_trust as T
    import trust_write as TW

    G = T.G
    projects = cfg.projects
    index = T.key_index(projects)
    fp = hashlib.sha256(json.dumps([r.fingerprint() for r in levels.roots]).encode("utf-8")).hexdigest()[:16]
    items, scanned, forget = [], {}, []
    queued = set()
    auto_roots = {r.key for r in levels.roots if r.level == "trusted" and r.auto}

    # Repositories Gnomonkit trusted before whose key Claude Code has lost, where the scan below would look.
    now = time.monotonic()
    reapply_until = now + max(0.0, deadline - now) / 2
    keys = sorted(k for k in st["trusted"] if isinstance(k, str))
    cursor = st.get("cursor") if isinstance(st.get("cursor"), str) else ""
    last = cursor
    st.pop("cursor", None)
    for key in [k for k in keys if k > cursor] + [k for k in keys if k <= cursor]:
        if time.monotonic() > reapply_until:
            st["cursor"] = last
            break
        last = key
        rec = st["trusted"].get(key)
        repo = rec.get("repo") if isinstance(rec, dict) else None
        if not isinstance(repo, str) or not repo:
            forget.append(key)
            continue
        if T.is_trusted(projects, repo, index, resolved=True):
            continue               # nothing lost: no need to look at the disk
        if not T.is_repo(repo):
            forget.append(key)
            continue
        n = G.norm(repo)
        if n != T.key_norm(repo) or G._parent(n) not in auto_roots:
            forget.append(key)     # a link leads elsewhere now, or not where the scan looks (trust sync's job)
            continue
        gov = T.governing(repo, levels, resolved=True)      # it leads to itself: checked just above
        if gov is None or gov.level != "trusted":
            forget.append(key)
            continue
        if not gov.auto:
            continue
        ok, why = T.qualifies(repo, gov)
        if not ok:
            forget.append(key)
            continue
        items.append(TW.Item(repo, gov.root, why, by="reapply"))
        queued.add(T.key_norm(repo))

    # New repositories under the auto roots.
    for r in levels.roots:
        if time.monotonic() > deadline:
            break
        if r.level != "trusted" or not r.auto or not os.path.isdir(r.root):
            continue
        mtime = _mtime(r.root)
        last_scan = st["roots"].get(r.key)
        if isinstance(last_scan, dict) and last_scan.get("mtime") == mtime and last_scan.get("levels") == fp:
            continue
        found, complete = T.candidates(levels, projects, depth=1, auto_only=True, deadline=deadline, roots=[r],
                                       pending_only=True)
        for c in found:
            if c.pending and T.key_norm(c.repo) not in queued:
                items.append(TW.Item(c.repo, c.root.root, c.reason, by="auto"))
                queued.add(T.key_norm(c.repo))
        if complete:
            scanned[r.key] = {"mtime": mtime, "levels": fp}
    return items, scanned, forget


def lines_for(changed: list, limit: int | None = None) -> list:
    """One line per repository; past `limit` (MAX_LINES by default), one more line saying how many more."""
    out, seen = [], set()
    for rec in changed:
        if rec["repo"] in seen:
            continue
        seen.add(rec["repo"])
        if rec.get("by") == "reapply":
            out.append("Gnomonkit put back Claude Code's trust for %s (its file had lost it)." % rec["repo"])
        else:
            out.append("Gnomonkit trusted %s in Claude Code: it is under %s and %s."
                       % (rec["repo"], rec.get("root") or "a trusted root", rec.get("reason") or "it qualifies"))
    limit = MAX_LINES if limit is None else limit
    if len(out) > limit:
        more = len(out) - limit
        out = out[:limit] + ["...and %d more; `gnomonkit trust status` lists them and %s has each one."
                             % (more, _audit_path())]
    return out


def _audit_path() -> str:
    import gk_grants as G
    return G.audit_path("trust")


def run(event: dict, deadline: float | None = None) -> list:
    """The lines to show; [] when there is nothing to do or anything goes wrong. Everything stops at
    `deadline` (time.monotonic(); BUDGET_S from now by default)."""
    if deadline is None:
        deadline = time.monotonic() + BUDGET_S
    if event.get("source") not in (None, "", "startup"):
        return []
    if (os.environ.get("GNOMONKIT_TRUST_AUTO") or "").strip().lower() in OFF:
        return []
    import gk_trust as T
    import trust_write as TW

    levels = T.load_levels()
    if not levels.ok or not any(r.level == "trusted" and r.auto for r in levels.roots):
        return []
    cfg = T.read_claude_config()
    if not cfg.ok:
        return []
    st = TW.read_state()
    items, scanned, forget = plan(levels, cfg, st, deadline - WRITE_RESERVE_S)
    lines = []
    if items:
        try:
            res = TW.set_trusted(items, by="auto", path=cfg.path, deadline=deadline - TAIL_S)
        except TW.WriteError:
            return []      # logged by trust_write where it could be; the next start tries again
        TW.remember(st, res.changed)
        lines = lines_for(res.changed)
        # A root with a key that didn't stick, or a repository the write didn't reach in time, is scanned
        # again next time.
        redo = {T.key_norm(repo) for repo, _why in res.failed} | {T.key_norm(repo) for repo in res.deferred}
        for it in items:
            if T.key_norm(it.repo) in redo:
                gov = T.governing(it.repo, levels, resolved=True)
                if gov is not None:
                    scanned.pop(gov.key, None)
    for key in forget:
        st["trusted"].pop(key, None)
    st["roots"].update(scanned)
    try:
        TW.save_state(st)
    except OSError:
        pass
    return lines


def main() -> int:
    deadline = time.monotonic() + BUDGET_S
    try:
        lines = run(_event(), deadline)
        if lines:
            text = "\n".join(lines)
            print(json.dumps({"systemMessage": text,
                              "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    except Exception:  # noqa: BLE001 - a session start is never blocked by this hook
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
