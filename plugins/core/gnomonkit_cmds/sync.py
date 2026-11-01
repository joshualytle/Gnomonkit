"""gnomonkit sync: the sync gate. Plugin updates reach Claude Code only when they pass it.

    gnomonkit sync status [--json]
    gnomonkit sync check [--source NAME] [--if-due] [--quiet] [--json]
    gnomonkit sync explain ID [--json]
    gnomonkit sync adopt MARKETPLACE [--branch B] [--url URL] [--pin SHA] [--no-draft] [--force]
    gnomonkit sync approve-draft ID [--signers] [--no-pr] [--json]
    gnomonkit sync overlay [show | set REMOTE [--github OWNER/NAME] [--branch B] | clear]

A gated marketplace is served to Claude Code from a checkout the gate controls, at a pinned commit.
`check` fetches each source into a bare quarantine and moves the pin on its own only when every new
commit is signed by an approved key and no new contributor touched code; anything else is held, with
the reasons. `approve-draft` opens a pull request to your overlay that approves a held update (or
trusts a signer); merging it is the approval, and the gate reads it from the overlay's default branch
as fetched, never from a working tree. `adopt` prints the commands that re-point Claude Code; it runs
none of them. `overlay set` and `overlay clear` run only in a terminal. See docs/SYNC.md.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import _common as C
from . import _syncgate as G

STATE_TEXT = {"current": "up to date", "advanced": "moved on its own (signed by approved keys)",
              "held": "update held for review", "unverified": "update not offered (no verified signature)",
              "error": "the last check failed"}


def state_text(r: dict) -> str:
    """STATE_TEXT, and an advance says what moved it: the overlay's approval, signatures, or both."""
    by = {s.get("by") for s in r.get("steps") or [] if isinstance(s, dict)}
    if r.get("state") == "advanced" and "approval" in by:
        return ("moved by your overlay's approval, then over commits signed by approved keys" if "signatures" in by
                else "moved by your overlay's approval")
    return STATE_TEXT.get(r.get("state"), r.get("state"))


def register(sub) -> None:
    p = sub.add_parser("sync", help="the sync gate: pinned plugin sources, held updates, approvals by overlay PR",
                       description=__doc__.split("\n\n")[0])
    ps = p.add_subparsers(dest="sync_cmd", metavar="<status|check|explain|adopt|approve-draft|overlay>")
    q = ps.add_parser("status", help="each gated source, its pin, and what is held")
    q.add_argument("--json", action="store_true", help="machine-readable output")
    q.set_defaults(func=cmd_status)
    q = ps.add_parser("check", help="fetch each source into quarantine and advance or hold")
    q.add_argument("--source", help="check this source only")
    q.add_argument("--if-due", action="store_true", help="do nothing when the last check is under an hour old")
    q.add_argument("--quiet", action="store_true", help="print nothing (for the session-start hook)")
    q.add_argument("--json", action="store_true", help="machine-readable output")
    q.set_defaults(func=cmd_check)
    q = ps.add_parser("explain", help="why an update is held: each reason and the files it changes")
    q.add_argument("id", help="a held update's id (from `sync status`), or SOURCE-signers")
    q.add_argument("--json", action="store_true", help="machine-readable output")
    q.set_defaults(func=cmd_explain)
    q = ps.add_parser("adopt", help="put a marketplace behind the gate (prints the re-point steps; runs none)")
    q.add_argument("marketplace", help="the marketplace name, as `claude plugin marketplace list` shows it")
    q.add_argument("--branch", help="the branch to follow (default: the remote's default branch)")
    q.add_argument("--url", help="the remote to fetch from, when Claude Code's record has none (terminal only)")
    q.add_argument("--pin", help="the first pinned commit, when no installed commit is recorded (terminal only)")
    q.add_argument("--no-draft", action="store_true", help="don't draft the suggested release-key approval")
    q.add_argument("--force", action="store_true", help="adopt again over an existing pin (terminal only)")
    q.set_defaults(func=cmd_adopt)
    q = ps.add_parser("approve-draft", help="open a pull request to your overlay that approves a held update")
    q.add_argument("id", help="a held update's id, or SOURCE-signers for the suggested release key")
    q.add_argument("--signers", action="store_true",
                   help="trust the keys that signed the held commits instead of approving this one range")
    q.add_argument("--no-pr", action="store_true", help="push the branch only; open the pull request yourself")
    q.add_argument("--json", action="store_true", help="machine-readable output")
    q.set_defaults(func=cmd_approve_draft)
    q = ps.add_parser("overlay", help="the overlay approvals and signers come from (set and clear: terminal only)")
    q.add_argument("action", nargs="?", default="show", choices=("show", "set", "clear"))
    q.add_argument("remote", nargs="?", help="for set: the overlay's remote (https, ssh, or a local path)")
    q.add_argument("--github", help="for set: OWNER/NAME on GitHub, when the remote doesn't say")
    q.add_argument("--branch", help="for set: the branch approvals merge into (default: the remote's default)")
    q.set_defaults(func=cmd_overlay)
    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


# ---- Claude Code's records (read only) -------------------------------------------------------------
def claude_home() -> str:
    raw = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return os.path.expanduser(raw) if raw else os.path.join(os.path.expanduser("~"), ".claude")


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def known_marketplaces() -> dict:
    d = _read_json(os.path.join(claude_home(), "plugins", "known_marketplaces.json"))
    return d if isinstance(d, dict) else {}


def installed_entries(marketplace: str) -> list:
    """[(plugin, entry)] for every install of a plugin from this marketplace, at any scope."""
    d = _read_json(os.path.join(claude_home(), "plugins", "installed_plugins.json")) or {}
    out = []
    for key, entries in sorted((d.get("plugins") or {}).items() if isinstance(d, dict) else []):
        if isinstance(key, str) and key.endswith("@" + marketplace) and isinstance(entries, list):
            out += [(key.rsplit("@", 1)[0], e) for e in entries if isinstance(e, dict)]
    return out


def market_url(m: dict) -> str | None:
    src = m.get("source") if isinstance(m, dict) else None
    if not isinstance(src, dict):
        return None
    if src.get("source") == "github" and src.get("repo"):
        return "https://github.com/%s.git" % src["repo"]
    if src.get("url"):
        return str(src["url"])
    return None


def repointed(name: str, markets: dict | None = None) -> bool:
    """True when Claude Code reads this marketplace from the gate's approved checkout."""
    m = (markets if markets is not None else known_marketplaces()).get(name) or {}
    src = m.get("source") if isinstance(m.get("source"), dict) else {}
    target = C.normalise_path(G.approved_dir(name))
    for p in (src.get("path"), m.get("installLocation") if src.get("source") == "directory" else None):
        if p and C.normalise_path(str(p)) == target:
            return True
    return False


def installed_pin(name: str, q: str) -> str | None:
    """The commit Claude Code installed this marketplace's plugins from. With several, the oldest one
    (the one the others descend from), so adopting never moves anything forward unchecked."""
    shas = []
    for _, e in installed_entries(name):
        s = str(e.get("gitCommitSha") or "").lower()
        if G.SHA.match(s) and G.rev(q, s) and s not in shas:
            shas.append(s)
    if not shas:
        loc = (known_marketplaces().get(name) or {}).get("installLocation")
        gd = os.path.join(str(loc), ".git") if loc else None
        s = G.rev(gd, "HEAD") if gd and os.path.isdir(gd) else None
        return s if s and G.rev(q, s) else None
    for s in shas:
        if all(s == o or G.is_ancestor(q, s, o) for o in shas):
            return s
    raise C.CommandError("the plugins of %s were installed from commits on different lines of history (%s); "
                         "pick the first pin with --pin, in a terminal" % (name, ", ".join(x[:7] for x in shas)))


# ---- output helpers ----------------------------------------------------------------------------------
def _short(sha) -> str:
    return str(sha)[:7] if sha else "-"


def _cmd(*args: str) -> str:
    return C.command_line("sync", *args)


def _when(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "never"


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True))


# ---- status ------------------------------------------------------------------------------------------
def source_rows() -> list:
    d = G.load_sources()
    markets = known_marketplaces()
    rows = []
    for name, src in sorted(d["sources"].items()):
        last = src.get("last") or {}
        rows.append({"name": name, "url": src.get("url"), "branch": src.get("branch"),
                     "pin": G.approved_pin(name) or last.get("pin"), "head": last.get("head"),
                     "state": last.get("state") or "unchecked", "checked": last.get("checked_ts"),
                     "held": last.get("held"), "summary": last.get("summary"), "error": last.get("error"),
                     "notes": last.get("notes") or [], "repointed": repointed(name, markets),
                     "suggested_signers": int(last.get("suggested_signers") or 0)})
    return rows


def cmd_status(a) -> int:
    rows = source_rows()
    ov = G.overlay_config()
    held = G.list_held()
    if a.json:
        _print_json({"overlay": ov, "sources": rows, "held": [{k: h.get(k) for k in ("id", "source", "pin", "head",
                     "commits", "reasons", "files_total")} for h in held], "checked": G.load_sources().get("checked")})
        return 0
    print("Sync gate (docs/SYNC.md)")
    if ov:
        print("Overlay: %s (approvals and signers come from its %s branch as fetched)"
              % (ov["remote"], ov["branch"] or "default"))
    else:
        print("Overlay: none. Nothing is held or approved; updates to the %s marketplace are offered only when "
              "signed by the suggested release key. Set one in a terminal: %s"
              % (G.SUGGESTED_FOR, _cmd("overlay", "set", "<remote>")))
    if not rows:
        print("No gated sources. Put a marketplace behind the gate with: %s" % _cmd("adopt", "<marketplace>"))
        return 0
    print("Last check: %s (at most hourly from session start; `%s` runs one now)"
          % (_when(G.load_sources().get("checked")), _cmd("check")))
    for r in rows:
        print("")
        print("%s  (%s, branch %s)" % (r["name"], r["url"], r["branch"]))
        print("  pin %s, upstream %s: %s" % (_short(r["pin"]), _short(r["head"]),
                                              STATE_TEXT.get(r["state"], r["state"])))
        if r["state"] == "held" and r["held"]:
            print("  held %s: %s" % (r["held"], r["summary"]))
            print("  details: %s" % _cmd("explain", r["held"]))
        elif r["state"] == "unverified":
            print("  %s" % r["summary"])
        elif r["state"] == "error":
            print("  %s" % r["error"])
        for n in r["notes"]:
            print("  note: %s" % n)
        if r["suggested_signers"]:
            print("  the suggested release key is not in your overlay's signers yet: %s"
                  % _cmd("approve-draft", "%s-signers" % r["name"]))
        if not r["repointed"]:
            print("  Claude Code does not read %s from the approved checkout yet, so its updates bypass the gate. "
                  "The steps: %s" % (r["name"], _cmd("adopt", r["name"])))
    return 0


# ---- check -------------------------------------------------------------------------------------------
def cmd_check(a) -> int:
    d = G.load_sources()
    if not d["sources"]:
        if not a.quiet:
            print("No gated sources. Put a marketplace behind the gate with: %s" % _cmd("adopt", "<marketplace>"))
        return 0
    if a.source and a.source not in d["sources"]:
        raise C.CommandError("%r is not a gated source (see `%s`)" % (a.source, _cmd("status")))
    if a.if_due and time.time() - float(d.get("checked") or 0) < G.RECHECK:
        return 0
    with G.Lock() as lock:
        if not lock.held:
            if not a.quiet:
                print("Another sync check is running; nothing done.")
            return 0
        out = G.check_all(a.source)
    if a.json:
        _print_json(out)
    elif not a.quiet:
        for name, r in sorted(out["results"].items()):
            line = "%s: %s" % (name, state_text(r))
            if r.get("state") in ("current", "advanced"):
                line += " at %s" % _short(r.get("pin"))
                if r.get("state") == "advanced":
                    line += " (was %s)" % _short(r.get("previous_pin"))
            elif r.get("state") == "held":
                line += " at %s, upstream %s: %s. Details: %s" % (_short(r.get("pin")), _short(r.get("head")),
                                                                   r.get("summary"), _cmd("explain", r["held"]))
            elif r.get("state") == "unverified":
                line += ": %s" % r.get("summary")
            else:
                line += ": %s" % r.get("error")
            print(line)
            for n in r.get("notes") or []:
                print("  note: %s" % n)
            if r.get("restored"):
                print("  the approved checkout had been changed on disk; put back: %s" % ", ".join(r["restored"][:10]))
    return 1 if any(r.get("state") == "error" for r in out["results"].values()) else 0


# ---- explain -----------------------------------------------------------------------------------------
def _parse_id(hid: str) -> tuple:
    m = G.HELD_ID.match(hid or "")
    if not m:
        raise C.CommandError("%r is not an id: use SOURCE-<12 hex> from `%s`, or SOURCE-signers"
                             % (hid, _cmd("status")))
    return m.group(1), m.group(2)


def pending_signers(source: str) -> list:
    ov = G.overlay_config()
    if not ov:
        raise C.CommandError("no overlay is set, so there is nowhere to approve a signer. In a terminal: %s"
                             % _cmd("overlay", "set", "<remote>"))
    return G.suggested_pending(source, ov)


def cmd_explain(a) -> int:
    source, tail = _parse_id(a.id)
    if tail == "signers":
        lines = pending_signers(source)
        if a.json:
            _print_json({"id": a.id, "source": source, "keys": [G.fingerprint(G.key_of(x)) for x in lines]})
            return 0
        if not lines:
            print("%s: the overlay already lists every suggested release key (as last fetched)." % a.id)
            return 0
        print("%s: the suggested release key for %s is not in your overlay's approved signers:" % (a.id, source))
        for ln in lines:
            print("  %s" % G.fingerprint(G.key_of(ln)))
        print("Check the fingerprint against the one the maintainer publishes, then: %s" % _cmd("approve-draft", a.id))
        return 0
    h = G.read_held(a.id)
    if not h:
        raise C.CommandError("no held update %s (see `%s`)" % (a.id, _cmd("status")))
    if a.json:
        _print_json(h)
        return 0
    n = h.get("commits")
    print("%s: %s is held at %s; upstream %s is at %s%s." % (h["id"], h["source"], _short(h["pin"]), h.get("branch"),
                                                           _short(h["head"]),
                                                           " (%d new commits)" % n if isinstance(n, int) else ""))
    print("Why it is held:")
    for r in h.get("reasons") or []:
        print("  - %s" % (r.get("text") or r.get("kind")))
    files = h.get("files") or []
    print("Files changed (%d):" % int(h.get("files_total") or len(files)))
    for f in files[:60]:
        print("  %6s %6s  %s" % ("+%s" % f["added"] if f.get("added") is not None else "bin",
                                 "-%s" % f["deleted"] if f.get("deleted") is not None else "", f["path"]))
    if len(files) > 60:
        print("  ... and %d more (--json lists them)" % (len(files) - 60))
    print("To read the change itself (the quarantine is never checked out, and nothing in it runs):")
    print('  git --git-dir "%s" log -p %s..%s' % (G.quarantine_path(h["source"]), h["pin"][:12], h["head"][:12]))
    print("To approve this range, draft a pull request to your overlay (merging it is the approval):")
    print("  %s" % _cmd("approve-draft", h["id"]))
    if any(r.get("kind") == "unknown-key" for r in h.get("reasons") or []):
        print("Or trust the key that signed it, for this and later updates: %s" % _cmd("approve-draft", h["id"],
                                                                                       "--signers"))
    return 0


# ---- adopt -------------------------------------------------------------------------------------------
def repoint_steps(name: str) -> list:
    path = G.approved_dir(name)
    if repointed(name):
        return ["Claude Code already reads %s from the approved checkout (%s). Nothing to re-point." % (name, path)]
    installs = installed_entries(name)
    out = ["Claude Code still reads %s from its own clone, so updates bypass the gate until you re-point it."
           % name,
           "Nothing in Claude Code was changed. Run these yourself, in a terminal, in this order "
           "(docs/SYNC.md explains each):",
           "  1. claude plugin marketplace remove %s" % name,
           "     Removing a marketplace uninstalls its plugins; step 3 puts them back.",
           '  2. claude plugin marketplace add "%s"' % path]
    lines = []
    for plugin, e in installs:
        scope = e.get("scope") if e.get("scope") in ("user", "project", "local") else "user"
        cmd = "claude plugin install %s@%s -s %s" % (plugin, name, scope)
        if scope != "user" and e.get("projectPath"):
            cmd += '   (in "%s")' % e["projectPath"]
        if cmd not in lines:
            lines.append(cmd)
    if not lines:
        lines = ["claude plugin install <plugin>@%s -s user   (for each plugin you use from it)" % name]
    out.append("  3. " + lines[0])
    out += ["     " + x for x in lines[1:]]
    if any(e.get("scope") == "project" for _, e in installs):
        out.append("     A project-scope install writes that project's .claude/settings.json: check `git diff` "
                   "there afterwards.")
    out.append("  4. /reload-plugins in each open session (or restart it).")
    out.append("After that, `claude plugin marketplace update %s` reads the approved checkout, which moves only when "
               "the gate lets it." % name)
    return out


def _inside(path) -> bool:
    """A relative path that stays inside the repository: no drive, no leading slash, no scheme, no '..'."""
    p = str(path).replace("\\", "/") if isinstance(path, str) else ""
    return bool(p) and not p.startswith("/") and ":" not in p and ".." not in p.split("/")


def outside_sources(manifest) -> list:
    """The plugins a marketplace manifest takes from anywhere but a path in its own repository (a GitHub repo,
    a URL, a package): the gate pins the repository, so it could not pin or check those."""
    out = []
    meta = manifest.get("metadata") if isinstance(manifest, dict) else None
    if isinstance(meta, dict) and meta.get("pluginRoot") is not None and not _inside(meta.get("pluginRoot")):
        out.append("metadata.pluginRoot")
    plugins = manifest.get("plugins") if isinstance(manifest, dict) else None
    for p in plugins if isinstance(plugins, list) else []:
        if isinstance(p, dict) and not _inside(p.get("source")):
            n = str(p.get("name") or "")
            out.append(n if G.NAME.match(n) else "an unnamed plugin")
    return out


def cmd_adopt(a) -> int:
    name = G.check_name(a.marketplace)
    d = G.load_sources()
    existing = d["sources"].get(name)
    if existing and not a.force:
        print("%s is already behind the gate, pinned at %s." % (name, _short(G.approved_pin(name))))
        for line in repoint_steps(name):
            print(line)
        return 0
    if a.url or a.pin or existing:
        C.require_tty("adopt", prompt="This sets where %s comes from or which commit it is pinned at. "
                                      "Type adopt to confirm: " % name)
    m = known_marketplaces().get(name)
    if not m and not a.url:
        raise C.CommandError("Claude Code has no marketplace named %s (see `claude plugin marketplace list`)" % name)
    url = a.url or market_url(m or {})
    if not url:
        raise C.CommandError("Claude Code's record of %s names no GitHub or git remote to fetch from; pass --url, "
                             "in a terminal" % name)
    url = G.check_remote(url)
    src = (m or {}).get("source") if isinstance((m or {}).get("source"), dict) else {}
    branch = G.check_branch(a.branch or src.get("ref") or G.remote_head_branch(url) or "main")
    with G.Lock() as lock:
        if not lock.held:
            raise C.CommandError("a sync check is running; try again in a minute", code=1)
        head = G.quarantine_fetch(name, url, branch)
        q = G.quarantine_path(name)
        pin = G.rev(q, a.pin) if a.pin else installed_pin(name, q)
        if not pin:
            raise C.CommandError("no installed commit of %s is in the fetched %s branch; pick the first pin with "
                                 "--pin, in a terminal" % (name, branch) if not a.pin else
                                 "commit %s is not in the fetched %s branch" % (a.pin, branch))
        if not G.is_ancestor(q, pin, head):
            raise C.CommandError("commit %s is not on the %s branch's history; pick a pin that is" % (pin[:7], branch))
        r = G.git(["cat-file", "blob", "%s:.claude-plugin/marketplace.json" % pin], git_dir=q, isolated=True,
                  check=False)
        try:
            manifest = json.loads(r.stdout) if r.returncode == 0 else {}
        except ValueError:
            manifest = {}
        declared = manifest.get("name") if isinstance(manifest, dict) else None
        if declared != name:
            raise C.CommandError("the repository at %s declares the marketplace %r, not %r, so plugin keys would "
                                 "change; nothing was adopted" % (url, declared, name))
        outside = outside_sources(manifest)
        if outside:
            raise C.CommandError("%s lists plugins that come from outside its repository (%s), which the gate can't "
                                 "pin or check; nothing was adopted" % (name, ", ".join(outside[:5])))
        G.approved_checkout(name, branch, pin)
        d = G.load_sources()
        d["sources"][name] = {"url": url, "branch": branch, "marketplace": name, "adopted": G.now_iso(),
                              "last": {"state": "current" if pin == head else "unchecked", "pin": pin, "head": head,
                                       "checked": G.now_iso(), "checked_ts": time.time()}}
        d["checked"] = 0                   # the next session start runs a full check
        G.save_sources(d)
    print("%s is behind the gate: the approved checkout is at %s, pinned at %s (upstream %s is at %s)."
          % (name, G.approved_dir(name), _short(pin), branch, _short(head)))
    if pin != head:
        print("The next `%s` decides whether the newer commits pass." % _cmd("check"))
    for line in repoint_steps(name):
        print(line)
    ov = G.overlay_config()
    if not ov:
        print("No overlay is set: nothing will be held or approved, and %s"
              % ("updates are offered only when signed by the suggested release key." if name == G.SUGGESTED_FOR
                 else "updates are not offered (there is no key to check them against)."))
        print("Set one in a terminal: %s" % _cmd("overlay", "set", "<remote>"))
    elif name == G.SUGGESTED_FOR and not a.no_draft:
        _draft_suggested(name, ov)
    return 0


def _draft_suggested(name: str, ov: dict) -> None:
    try:
        G.fetch_overlay(ov)
        pending = G.suggested_pending(name, ov)
    except C.CommandError as e:
        print("The suggested release key could not be compared with your overlay (%s). Later: %s"
              % (e, _cmd("approve-draft", "%s-signers" % name)))
        return
    if not pending:
        try:
            with open(G.suggested_signers_path(), encoding="utf-8") as fh:
                has_keys = bool(G.signer_lines(fh.read()))
        except OSError:
            has_keys = False
        print("Your overlay already trusts the suggested release key." if has_keys else
              "Core ships no release key yet (the suggested key file holds none), so no signer approval was drafted.")
        return
    try:
        out = draft_signers(name, pending, "the release key Gnomonkit suggests for %s" % name)
    except C.CommandError as e:
        print("Drafting the release-key approval failed (%s). Later: %s" % (e, _cmd("approve-draft", "%s-signers"
                                                                                     % name)))
        return
    _report_draft(out)


# ---- approve-draft -----------------------------------------------------------------------------------
def open_pr(slug: str, base: str, head: str, title: str, body: str) -> str | None:
    """Open the pull request with gh. Returns its URL, or None when gh is not installed."""
    if not shutil.which("gh"):
        return None
    fd, path = tempfile.mkstemp(prefix="gnomonkit-pr-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        r = subprocess.run(["gh", "pr", "create", "--repo", slug, "--base", base, "--head", head, "--title", title,
                            "--body-file", path], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, creationflags=G.NO_WINDOW)
    finally:
        G._unlink(path)
    if r.returncode != 0:
        raise G.GateError("gh pr create failed: %s" % ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1][:300])
    urls = [w for w in (r.stdout or "").split() if w.startswith("https://")]
    return urls[-1] if urls else (r.stdout or "").strip() or None


def _publish(ov: dict, branch: str, changes: dict, title: str, body: str, no_pr: bool) -> dict:
    out = G.draft_commit(ov, branch, changes, "%s\n\n%s" % (title, body))
    out.update(pr=None, compare=None, base=G.overlay_base(ov))
    if ov.get("github"):
        out["compare"] = "https://github.com/%s/compare/%s...%s?expand=1" % (ov["github"], out["base"], branch)
        if not no_pr and not out["already"]:
            out["pr"] = open_pr(ov["github"], out["base"], branch, title, body)
    return out


def _report_draft(out: dict) -> None:
    if out.get("already"):
        print("Branch %s already exists in your overlay: this approval was drafted before. Merge or close that "
              "pull request." % out["branch"])
    else:
        print("Pushed branch %s to your overlay." % out["branch"])
    if out.get("pr"):
        print("Pull request: %s" % out["pr"])
    elif out.get("compare") and not out.get("already"):
        print("Open the pull request: %s" % out["compare"])
    elif not out.get("already"):
        print("Open a pull request from %s into %s in your overlay." % (out["branch"], out.get("base")))
    print("Merging it is the approval; the next `%s` picks it up from the overlay's default branch." % _cmd("check"))


def draft_signers(source: str, lines: list, why: str, no_pr: bool = False, hid: str | None = None) -> dict:
    ov = G.overlay_config()
    current = G.overlay_file(G.signers_path(source))
    fps = [G.fingerprint(G.key_of(x)) for x in lines]
    text = G.signers_text(current, source, lines, "added %s by gnomonkit sync approve-draft: %s" % (G.now_iso()[:10], why))
    title = "sync: trust %d signer key%s for %s" % (len(lines), "" if len(lines) == 1 else "s", source)
    body = ("The sync gate will move %s on its own over commits signed by %s.\n\nWhy: %s.\n\nKey fingerprints:\n\n%s\n\n"
            "Check each fingerprint against one the publisher gives you before merging. Merging this pull request is "
            "the approval; closing it changes nothing. Drafted by `gnomonkit sync approve-draft`."
            % (source, "this key" if len(lines) == 1 else "these keys", why, "\n".join("- `%s`" % f for f in fps)))
    branch = G.SYNC_BRANCH_PREFIX + (("%s-signers" % hid) if hid else "%s-signers" % source)
    out = _publish(ov, branch, {G.signers_path(source): text}, title, body, no_pr)
    out.update(id=hid or "%s-signers" % source, keys=fps)
    return out


def draft_approval(h: dict, no_pr: bool = False) -> dict:
    ov = G.overlay_config()
    text = G.approval_text(G.overlay_file(G.approvals_path()), h)
    n = h.get("commits")
    title = "sync: approve %s %s..%s" % (h["source"], h["pin"][:7], h["head"][:7])
    rows = ["| %s | %s | `%s` |" % ("+%s" % f["added"] if f.get("added") is not None else "bin",
                                    "-%s" % f["deleted"] if f.get("deleted") is not None else "",
                                    f["path"].replace("|", "\\|")) for f in (h.get("files") or [])[:100]]
    body = ("Approves the update the sync gate held for **%s**: %s..%s%s.\n\nReasons it was held, which merging this "
            "accepts:\n\n%s\n\nFiles changed (%d):\n\n| added | deleted | path |\n|---|---|---|\n%s\n\n"
            "Read the change before merging: `gnomonkit sync explain %s` prints the command. Merging this pull request "
            "is the approval; closing it leaves the update held. Drafted by `gnomonkit sync approve-draft`."
            % (h["source"], h["pin"][:12], h["head"][:12], " (%d commits)" % n if isinstance(n, int) else "",
               "\n".join("- %s" % (r.get("text") or r.get("kind")) for r in h.get("reasons") or []) or "- (none recorded)",
               int(h.get("files_total") or 0), "\n".join(rows) or "| | | (none) |", h["id"]))
    out = _publish(ov, G.SYNC_BRANCH_PREFIX + h["id"], {G.approvals_path(): text}, title, body, no_pr)
    out.update(id=h["id"])
    return out


def cmd_approve_draft(a) -> int:
    source, tail = _parse_id(a.id)
    ov = G.overlay_config()
    if not ov:
        raise C.CommandError("no overlay is set, so there is nowhere to draft an approval. In a terminal: %s"
                             % _cmd("overlay", "set", "<remote>"))
    with G.Lock() as lock:
        if not lock.held:
            raise C.CommandError("a sync check is running; try again in a minute", code=1)
        G.fetch_overlay(ov)
        if tail == "signers":
            lines = G.suggested_pending(source, ov)
            if not lines:
                raise C.CommandError("nothing to draft: the overlay already lists every suggested release key for %s, "
                                     "or core suggests none" % source)
            out = draft_signers(source, lines, "the release key Gnomonkit suggests for %s" % source, a.no_pr)
        else:
            h = G.read_held(a.id)
            if not h:
                raise C.CommandError("no held update %s (see `%s`)" % (a.id, _cmd("status")))
            if a.signers:
                q = G.quarantine_path(source)
                commits = [r["commit"] for r in h.get("reasons") or [] if r.get("kind") == "unknown-key"
                           and r.get("commit")]
                have = {G.key_of(x) for x in G.signer_lines(G.overlay_file(G.signers_path(source)))}
                lines, seen = [], set()
                for sha in commits:
                    key = G.signature_key(q, sha)
                    if key and key not in have and key not in seen:
                        seen.add(key)
                        email = G.git(["log", "-1", "--format=%ce", sha], git_dir=q, isolated=True).stdout.strip()
                        principal = email if email and " " not in email and '"' not in email else "*"
                        lines.append('%s namespaces="git" %s' % (principal, key))
                if not lines:
                    raise C.CommandError("no signer to add: no commit in %s is signed by a key the overlay lacks "
                                         "(an unsigned commit can only be approved as a range)" % a.id)
                out = draft_signers(source, lines, "the key%s that signed %s" % ("" if len(lines) == 1 else "s", a.id),
                                    a.no_pr, hid=a.id)
            else:
                out = draft_approval(h, a.no_pr)
    if a.json:
        _print_json(out)
    else:
        _report_draft(out)
    return 0


# ---- overlay -----------------------------------------------------------------------------------------
def cmd_overlay(a) -> int:
    cfg = G.read_config()
    if a.action == "show":
        ov = G.overlay_config()
        if not ov:
            print("No overlay is set. In a terminal: %s" % _cmd("overlay", "set", "<remote>"))
        else:
            print("Overlay: %s\n  GitHub: %s\n  branch: %s" % (ov["remote"], ov["github"] or "(not on GitHub: open "
                                                              "pull requests yourself)", ov["branch"] or "its default"))
        return 0
    if a.action == "clear":
        if not cfg.get("overlay"):
            print("No overlay is set; nothing to clear.")
            return 0
        C.require_tty("clear", prompt="Without an overlay nothing is approved and held updates stay held. "
                                      "Type clear to confirm: ")
        cfg.pop("overlay", None)
    else:
        if not a.remote:
            raise C.CommandError("give the overlay's remote: %s" % _cmd("overlay", "set", "<remote>"))
        remote = G.check_overlay_remote(a.remote)
        slug = (a.github or "").strip() or G.github_slug(remote)
        if a.github and not G.SLUG.match(a.github.strip()):
            raise C.CommandError("--github takes OWNER/NAME, not %r" % a.github)
        branch = G.check_branch(a.branch) if a.branch else ""
        C.require_tty("set", prompt="Approvals and trusted signers for the sync gate will come from %s. "
                                    "Type set to confirm: " % remote)
        cfg["overlay"] = {"remote": remote, "github": slug or "", "branch": branch}
    C.atomic_write(G.config_path(), C.dumps(cfg, layout=[("overlay", ["remote", "github", "branch"]),
                                                         ("suggested", ["signers"])],
                                            header="Sync gate settings. Change them with `gnomonkit sync overlay`; "
                                                   "see docs/SYNC.md."))
    print("Saved %s." % G.config_path())
    return 0
