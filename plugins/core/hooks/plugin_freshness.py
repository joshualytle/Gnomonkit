#!/usr/bin/env python3
"""Say at session start when a plugin this session loaded is behind its marketplace.

WHY. A running session keeps the plugin versions it loaded at start. When a marketplace ships a
new version, nothing in the session says so, and the update only lands when someone happens to
open a new session. This hook closes that gap: one line naming what is behind and how to apply
it without a new session.

WHAT IT CHECKS. Every plugin enabled for this session (enabledPlugins in the user settings, the
project's .claude/settings.json and .claude/settings.local.json; the most specific file wins).
For each, the version installed for this project (installed_plugins.json: a local or project
entry for this directory, else the user entry) is compared with the version in plugin.json on
the marketplace repo's default branch. Only marketplaces with a GitHub source are checked: the
plugin path comes from the local marketplace clone, the version is fetched with `gh` (works for
private repos) and, failing that, from raw.githubusercontent.com (public repos).

WHEN. At SessionStart it checks and says so straight away. On UserPromptSubmit it never touches
the network: it reads the last result (~/.gnomonkit/freshness/, GNOMONKIT_HOME overrides) and,
when that is over an hour old, starts a detached `--refresh` so the next prompt has a fresh
answer. A running session therefore hears about an update shipped after it started, once per
new version, and stops hearing once /reload-plugins has applied it.

INSTALLED SINCE START. SessionStart also records the versions this session loaded. When a newer one is
installed while it runs, its next prompt someone typed carries one notice naming /reload-plugins: once
per set of installed versions, never on a prompt dispatch sent, and again only after a typed prompt has
followed the last notice and a newer update has landed. A /reload-plugins in the transcript resets it.

THE SYNC GATE (docs/SYNC.md). A marketplace adopted with `gnomonkit sync adopt` is never checked
against GitHub here: its updates are offered from the gate's approved checkout only, with the usual
commands. An update the gate held says "update held for review" with the kinds of reason and their
counts and the `sync explain` command, and no install commands; one that failed the signature check (no
overlay) says it was not offered. No text from upstream (a name, an address, a path) is ever printed. With gated sources, this hook also starts `gnomonkit sync check --if-due` in the background,
at most hourly. It reads the gate's files and never writes them.

CONTRACT. Fail-soft: offline, unauthenticated, an unknown layout or any error prints nothing.
Always exits 0. Checks run in parallel with short timeouts so session start is not held up.
Set GNOMONKIT_FRESHNESS=off to silence it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# A hook's children must never open a console window on Windows (a detached parent has none to share).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TIMEOUT = 6
RECHECK = 3600  # seconds between background re-checks while a session is running


def claude_home() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path or ""))


def enabled_plugins(home: str, cwd: str) -> list[str]:
    merged: dict = {}
    for path in (os.path.join(home, "settings.json"),
                 os.path.join(cwd, ".claude", "settings.json"),
                 os.path.join(cwd, ".claude", "settings.local.json")):
        merged.update((read_json(path) or {}).get("enabledPlugins") or {})
    return sorted(k for k, v in merged.items() if v and "@" in k)


def installed_version(installed: dict, key: str, cwd: str) -> str | None:
    entries = (installed.get("plugins") or {}).get(key) or []
    here = [e for e in entries if e.get("scope") in ("local", "project") and norm(e.get("projectPath")) == norm(cwd)]
    user = [e for e in entries if e.get("scope") == "user"]
    for e in here + user:
        if e.get("version"):
            return e["version"]
    return None


def plugin_path(home: str, marketplace: str, install_location: str | None, plugin: str) -> str | None:
    """Where the plugin lives inside its marketplace repo, from the local clone's marketplace.json."""
    loc = install_location or os.path.join(home, "plugins", "marketplaces", marketplace)
    mk = read_json(os.path.join(loc, ".claude-plugin", "marketplace.json")) or {}
    for p in mk.get("plugins") or []:
        if p.get("name") == plugin and isinstance(p.get("source"), str):
            src = p["source"]
            src = src[2:] if src.startswith("./") else src
            return "" if src in (".", "") else src.rstrip("/")
    return None


def fetch_manifest(repo: str, path: str) -> str | None:
    rel = (path + "/" if path else "") + ".claude-plugin/plugin.json"
    if shutil.which("gh"):
        try:
            r = subprocess.run(["gh", "api", "repos/%s/contents/%s" % (repo, rel), "--jq", ".content"],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT,
                               creationflags=NO_WINDOW)
            if r.returncode == 0 and r.stdout.strip():
                return base64.b64decode(re.sub(r"\s", "", r.stdout)).decode("utf-8")
        except Exception:
            pass
    try:
        with urllib.request.urlopen("https://raw.githubusercontent.com/%s/HEAD/%s" % (repo, rel), timeout=TIMEOUT) as r:
            return r.read().decode("utf-8")
    except Exception:
        return None


def vtuple(v: str):
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def find_stale(home: str, cwd: str, fetch=fetch_manifest) -> list[tuple[str, str, str, str]]:
    installed = read_json(os.path.join(home, "plugins", "installed_plugins.json")) or {}
    markets = read_json(os.path.join(home, "plugins", "known_marketplaces.json")) or {}
    gated = gated_sources()
    jobs = []
    for key in enabled_plugins(home, cwd):
        plugin, marketplace = key.rsplit("@", 1)
        if marketplace in gated:
            continue  # behind the sync gate: offered only from its approved checkout (gate_report)
        m = markets.get(marketplace) or {}
        src = m.get("source") or {}
        have = installed_version(installed, key, cwd)
        if src.get("source") != "github" or not src.get("repo") or not vtuple(have):
            continue
        path = plugin_path(home, marketplace, m.get("installLocation"), plugin)
        if path is None:
            continue
        jobs.append((plugin, marketplace, have, src["repo"], path))

    def check(job):
        plugin, marketplace, have, repo, path = job
        try:
            text = fetch(repo, path)
            latest = json.loads(text).get("version") if text else None
        except Exception:
            return None
        if vtuple(latest) and vtuple(latest) > vtuple(have):
            return (plugin, marketplace, have, latest)
        return None

    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        return [r for r in pool.map(check, jobs) if r]


def notice(stale) -> str:
    items = ", ".join("%s %s -> %s" % (p, have, latest) for p, _, have, latest in stale)
    # The terminal commands come first: /plugin exists only in the terminal CLI, not in the IDE extensions
    # or desktop app, while `claude plugin ...` works from any shell and /reload-plugins works everywhere.
    markets = sorted({m for _, m, _, _ in stale})
    shell = " && ".join(["claude plugin marketplace update %s" % m for m in markets]
                        + ["claude plugin update %s@%s" % (p, m) for p, m, _, _ in stale])
    slash = "; ".join("/plugin marketplace update %s" % m for m in markets)
    return ("Plugin update pending: %s. To apply it, run in a terminal: %s; then /reload-plugins in each "
            "open session (or restart for startup blocks). In the terminal CLI, %s also works."
            % (items, shell, slash))


def gnomonkit_home() -> str:
    return os.environ.get("GNOMONKIT_HOME") or os.path.join(os.path.expanduser("~"), ".gnomonkit")


def state_path(cwd: str) -> str:
    key = hashlib.sha1(norm(cwd).encode("utf-8")).hexdigest()[:16]
    return os.path.join(gnomonkit_home(), "freshness", key + ".json")


# ---- the sync gate (docs/SYNC.md) ------------------------------------------------------------------
# A marketplace adopted by the gate (`gnomonkit sync adopt`) is not checked against GitHub here. What the
# gate approved is in its checkout, ~/.gnomonkit/sync/approved/<name>/; what it held or would not verify is
# in sources.json. This hook only reads those files, and starts `gnomonkit sync check` in the background
# at most hourly; the check itself (the quarantine fetch and the decision) lives in gnomonkit_cmds/.
def sync_dir() -> str:
    return os.path.join(gnomonkit_home(), "sync")


def gated_sources() -> dict:
    """The gated sources by name; a name that isn't a plain marketplace name is dropped."""
    d = read_json(os.path.join(sync_dir(), "sources.json")) or {}
    s = d.get("sources") if isinstance(d, dict) else None
    return {k: v for k, v in s.items() if SOURCE_NAME.match(str(k))} if isinstance(s, dict) else {}


# What reaches session start about a held or unverified update: this hook's own words for each kind of
# reason, with counts. Never a summary or reason text from sources.json: those can carry upstream names,
# e-mail addresses and paths, and session start is read by the agent.
SOURCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HELD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}-([0-9a-f]{12}|signers)$")
KIND_WORDS = {"unsigned": ("unsigned commit", "unsigned commits"),
              "unknown-key": ("commit signed by a key that is not approved", "commits signed by keys that are not approved"),
              "bad-signature": ("commit with a bad signature", "commits with bad signatures"),
              "expired": ("commit signed by an expired key", "commits signed by expired keys"),
              "revoked": ("commit signed by a revoked key", "commits signed by revoked keys"),
              "unverifiable": ("commit that could not be checked", "commits that could not be checked"),
              "new-contributor": ("new contributor touching code", "new contributors touching code"),
              "rewritten": ("the branch was rewritten", None),
              "overlay-unavailable": ("the overlay could not be fetched", None)}


def kinds_text(kinds) -> str:
    parts = []
    for k, n in (kinds.items() if isinstance(kinds, dict) else []):
        if k not in KIND_WORDS or not isinstance(n, int) or isinstance(n, bool) or n < 1:
            continue
        one, many = KIND_WORDS[k]
        parts.append(one if many is None else "%d %s" % (min(n, 9999), one if n == 1 else many))
    return ", ".join(parts) or "see the reasons"


def core_cli() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gnomonkit.py")


def sync_command(*args: str) -> str:
    py = os.path.splitext(os.path.basename(sys.executable or ""))[0] or "python"
    return " ".join(['%s "%s" sync' % (py, core_cli())] + list(args))


def repointed(home: str, name: str, markets: dict) -> bool:
    m = markets.get(name) or {}
    src = m.get("source") if isinstance(m.get("source"), dict) else {}
    target = norm(os.path.realpath(os.path.join(sync_dir(), "approved", name)))
    paths = [src.get("path")] + ([m.get("installLocation")] if src.get("source") == "directory" else [])
    return any(p and norm(os.path.realpath(str(p))) == target for p in paths)


def gate_report(home: str, cwd: str) -> tuple[list, list]:
    """(updates the gate approved that this session can install, lines about held or unverified updates).
    A held or unverified update never comes with install commands."""
    sources = gated_sources()
    if not sources:
        return [], []
    installed = read_json(os.path.join(home, "plugins", "installed_plugins.json")) or {}
    markets = read_json(os.path.join(home, "plugins", "known_marketplaces.json")) or {}
    enabled = enabled_plugins(home, cwd)
    offered, lines = [], []
    for name, src in sorted(sources.items()):
        mine = [k for k in enabled if k.rsplit("@", 1)[1] == name]
        if not mine or not isinstance(src, dict):
            continue
        last = src.get("last") if isinstance(src.get("last"), dict) else {}
        hid = str(last.get("held") or "")
        if last.get("state") == "held" and HELD_ID.match(hid) and hid.startswith(name + "-"):
            lines.append("%s: update held for review (%s). It is not installable until approved; to see why: %s"
                         % (name, kinds_text(last.get("kinds")), sync_command("explain", hid)))
        elif last.get("state") == "unverified":
            lines.append("%s: update not offered, because no approved key signed it (%s). %s"
                         % (name, kinds_text(last.get("kinds")), sync_command("status")))
        if not repointed(home, name, markets):
            lines.append("%s is behind the sync gate, but Claude Code still reads it from its own clone, so its "
                         "updates bypass the gate. The steps to re-point it: %s" % (name, sync_command("adopt", name)))
            continue
        approved = os.path.join(sync_dir(), "approved", name)
        for key in mine:
            plugin = key.rsplit("@", 1)[0]
            have = installed_version(installed, key, cwd)
            path = plugin_path(home, name, approved, plugin)
            if path is None or not vtuple(have):
                continue
            latest = (read_json(os.path.join(approved, path, ".claude-plugin", "plugin.json")) or {}).get("version")
            if vtuple(latest) and vtuple(latest) > vtuple(have):
                offered.append((plugin, name, have, latest))
    return offered, lines


def sync_due(now: float | None = None) -> bool:
    """True, and the spawn claimed, when there are gated sources and no check ran or started in the last hour."""
    d = read_json(os.path.join(sync_dir(), "sources.json")) or {}
    if not isinstance(d, dict) or not d.get("sources"):
        return False
    now = time.time() if now is None else now
    if now - float(d.get("checked") or 0) < RECHECK:
        return False
    claim = os.path.join(gnomonkit_home(), "freshness", "sync-check.json")
    if now - float((read_json(claim) or {}).get("spawned") or 0) < RECHECK:
        return False
    os.makedirs(os.path.dirname(claim), exist_ok=True)
    tmp = claim + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"spawned": now}, f)
    os.replace(tmp, claim)
    return True


def spawn_sync_check() -> None:
    if not os.path.isfile(core_cli()):
        return
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen([sys.executable, core_cli(), "sync", "check", "--if-due", "--quiet"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=flags, start_new_session=(os.name != "nt"))


def message(stale: list, lines: list) -> str | None:
    parts = ([notice(stale)] if stale else []) + list(lines)
    return "\n".join(parts) if parts else None


def save_state(cwd: str, state: dict) -> None:
    path = state_path(cwd)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def refresh(home: str, cwd: str, fetch=fetch_manifest) -> list:
    stale = find_stale(home, cwd, fetch)
    state = read_json(state_path(cwd)) or {}
    state.update(checked=time.time(), stale=[list(s) for s in stale])
    save_state(cwd, state)
    return stale


def still_stale(home: str, cwd: str, stale) -> list:
    """Drop entries the user has since applied (a /reload-plugins moves the installed version)."""
    installed = read_json(os.path.join(home, "plugins", "installed_plugins.json")) or {}
    out = []
    for plugin, marketplace, _, latest in stale:
        have = installed_version(installed, "%s@%s" % (plugin, marketplace), cwd)
        if vtuple(have) and vtuple(latest) and vtuple(latest) > vtuple(have):
            out.append((plugin, marketplace, have, latest))
    return out


def spawn_refresh(cwd: str) -> None:
    flags = 0
    if os.name == "nt":
        # A hidden console, not DETACHED_PROCESS: with no console at all, every gh the refresh starts gets
        # its own, and the default terminal (Windows Terminal) shows each one as a window.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--refresh", cwd],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=flags, start_new_session=(os.name != "nt"))


def on_prompt(home: str, cwd: str, session: str, spawn=spawn_refresh, sync_spawn=spawn_sync_check) -> str | None:
    """Per prompt: read the cached result only; refresh it in the background when it is old."""
    state = read_json(state_path(cwd)) or {}
    if time.time() - float(state.get("checked") or 0) > RECHECK:
        state["checked"] = time.time()  # claim the refresh so parallel prompts don't all spawn one
        save_state(cwd, state)
        spawn(cwd)
    if sync_due():
        sync_spawn()
    offered, lines = gate_report(home, cwd)
    stale = still_stale(home, cwd, [tuple(s) for s in state.get("stale") or []]) + offered
    if not stale and not lines:
        return None
    seen = "%s|%s|%s" % (session, ",".join("%s@%s=%s" % (p, m, latest) for p, m, _, latest in stale),
                         hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:12] if lines else "")
    if seen in (state.get("told") or []):
        return None
    state["told"] = ((state.get("told") or []) + [seen])[-50:]
    save_state(cwd, state)
    return message(stale, lines)


# ---- installed since this session started ------------------------------------------------------------
# A session keeps the versions it loaded. When an update is installed while it runs (by another session, or
# in a terminal), it gets one notice at its next prompt someone typed, naming /reload-plugins. One notice per
# set of installed versions: not again while the last one has had no typed prompt after it, and again only
# once a newer update lands. A /reload-plugins in the transcript, or this hook running from a new plugin
# folder (core's own version moved), takes a fresh snapshot. Kept in freshness/sessions/<session>.json.
SESSION_KEEP_DAYS = 14
RELOAD_MARK = "<command-name>/reload-plugins</command-name>"
# dispatch's first prompts (interjector session-dispatch): the session's user didn't type them.
BRIEF_POINTERS = ("Your task brief is in ", "A new brief for this session is in ")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def session_state_path(session: str) -> str | None:
    if not _SAFE_ID.match(session or ""):
        return None
    return os.path.join(gnomonkit_home(), "freshness", "sessions", session + ".json")


def loaded_versions(home: str, cwd: str) -> dict:
    installed = read_json(os.path.join(home, "plugins", "installed_plugins.json")) or {}
    out = {}
    for key in enabled_plugins(home, cwd):
        v = installed_version(installed, key, cwd)
        if vtuple(v):
            out[key] = v
    return out


def plugin_root() -> str:
    return norm(os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def count_reloads(transcript: str | None, start: int = 0) -> tuple[int, int]:
    """(how many /reload-plugins the transcript holds past byte `start`, its size). (0, start) when unreadable."""
    try:
        with open(transcript, "rb") as f:
            f.seek(start)
            data = f.read()
    except (OSError, TypeError, ValueError):
        return 0, start
    return data.count(RELOAD_MARK.encode("utf-8")), start + len(data)


def write_session_state(session: str, state: dict) -> None:
    path = session_state_path(session)
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def take_snapshot(home: str, cwd: str, session: str, transcript: str | None = None) -> dict:
    try:
        size = os.path.getsize(transcript) if transcript else 0
    except (OSError, TypeError, ValueError):
        size = 0
    state = {"taken": time.time(), "versions": loaded_versions(home, cwd), "root": plugin_root(),
             "scanned": size, "told": None, "answered": True}
    write_session_state(session, state)
    return state


def prune_sessions(now: float | None = None) -> None:
    d = os.path.join(gnomonkit_home(), "freshness", "sessions")
    cutoff = (time.time() if now is None else now) - SESSION_KEEP_DAYS * 86400
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        p = os.path.join(d, n)
        try:
            if n.endswith(".json") and os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


def typed(prompt) -> bool:
    """A prompt the session's user typed: not dispatch's brief pointer, not a notice wrapped in a tag."""
    p = prompt.strip() if isinstance(prompt, str) else ""
    return bool(p) and not p.startswith(BRIEF_POINTERS) and not p.startswith("<")


def reload_notice(home: str, cwd: str, session: str, prompt=None, transcript: str | None = None) -> str | None:
    """The one notice for updates installed since this session took its snapshot, else None."""
    if not session_state_path(session):
        return None
    state = read_json(session_state_path(session))
    if not isinstance(state, dict) or not isinstance(state.get("versions"), dict):
        take_snapshot(home, cwd, session, transcript)   # started before this hook knew it: from now on
        return None
    reloads, size = count_reloads(transcript, int(state.get("scanned") or 0))
    if reloads or state.get("root") != plugin_root():
        take_snapshot(home, cwd, session, transcript)   # it has loaded what is installed now
        return None
    state["scanned"] = size
    now = loaded_versions(home, cwd)
    was = state["versions"]
    moved = sorted((k, was[k], v) for k, v in now.items() if k in was and v != was[k])
    if typed(prompt):
        state["answered"] = True
    sig = ",".join("%s=%s" % (k, v) for k, _, v in moved)
    say = bool(moved) and typed(prompt) and sig != state.get("told") and state.get("answered", True)
    if say:
        state.update(told=sig, answered=False)
    write_session_state(session, state)
    if not say:
        return None
    items = ", ".join("%s %s -> %s" % (k.split("@", 1)[0], a, b) for k, a, b in moved)
    return ("Plugins updated since this session started: %s. Run /reload-plugins to load them (a restart also "
            "applies their session-start hooks)." % items)


def emit(event: str, line: str) -> None:
    print(json.dumps({"systemMessage": line,
                      "hookSpecificOutput": {"hookEventName": event, "additionalContext": line}}))


def main() -> int:
    try:
        if len(sys.argv) > 2 and sys.argv[1] == "--refresh":
            refresh(claude_home(), sys.argv[2])
            return 0
        try:
            payload = json.load(sys.stdin) or {}
        except Exception:
            payload = {}
        if os.environ.get("GNOMONKIT_FRESHNESS", "").lower() in ("0", "off", "false", "no"):
            return 0
        cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        home = claude_home()
        session = str(payload.get("session_id") or "")
        if payload.get("hook_event_name") == "UserPromptSubmit":
            parts = []
            try:
                parts.append(reload_notice(home, cwd, session, payload.get("prompt"), payload.get("transcript_path")))
            except Exception:
                pass
            parts.append(on_prompt(home, cwd, session))
            line = "\n".join(p for p in parts if p)
            if line:
                emit("UserPromptSubmit", line)
            return 0
        try:   # startup or resume: this process loaded what is installed now
            take_snapshot(home, cwd, session, payload.get("transcript_path"))
            prune_sessions()
        except Exception:
            pass
        stale = refresh(home, cwd)
        try:
            if sync_due():
                spawn_sync_check()
        except Exception:
            pass
        offered, lines = gate_report(home, cwd)
        line = message(stale + offered, lines)
        if line:
            emit("SessionStart", line)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
