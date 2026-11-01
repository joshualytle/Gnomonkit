#!/usr/bin/env python3
"""The owner's check, by hand: does folder trust hold up in a real Claude Code session?

    python tests/manual/trust_real_session.py --dry-run     # print each step; nothing is made or changed
    python tests/manual/trust_real_session.py --real        # do it (you type a word first)

The test suite proves the writer against made-up files. What it can't prove is how Claude Code itself
treats the key: whether a background session started in a repository Gnomonkit trusted gets past the trust
dialog, and whether Claude Code keeps the key while it runs. This script checks that, with a throwaway
repository, in this order:

  1. a scratch folder (the scratch root: --scratch, else the core plugin's scratch_root setting, else
     ~/.gnomonkit/scratch) holding a throwaway Gnomonkit home whose grants.toml has one temporary
     `trusted` root (auto on, local_only on), and a throwaway repository under it (git init);
  2. a copy of Claude Code's file (.claude.json, in CLAUDE_CONFIG_DIR or your home) in the scratch folder,
     taken before anything changes;
  3. the session-start hook (trust_autodiscover.py) run against it: the repository must come out trusted;
  4. `claude --bg` started in the repository, as session-dispatch starts one: it must not stop at the trust
     dialog (the command succeeds, and the session shows up in `claude agents`);
  5. the session left to settle, then stopped: Claude Code must have kept the key;
  6. the key taken out of the repository's entry, as a copy Claude Code wrote back without it would read,
     and the hook run again: it must put the key back and log "reapplied";
  7. the repository's entry removed again, the file compared with the copy from step 2 (you are offered
     the copy back if they differ), and the scratch folder deleted; it is kept, with the copy in it, when
     anything failed.

It edits the Claude Code file your sessions use, so it never runs on its own: --real, a terminal and a
typed word, every time. To keep your own file out of it, point CLAUDE_CONFIG_DIR at a config folder of its
own that you have logged in to; the check is the same. Each edit follows Gnomonkit's writer rules (one
writer at a time, the file read again right before an atomic swap, nothing written if it changed). It
starts one short session, so it uses a little of your usage. Not part of the test suite; never run in CI.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "plugins" / "core"
HOOK = CORE / "hooks" / "trust_autodiscover.py"
for p in (str(CORE), str(CORE / "hooks"), str(CORE / "skills" / "folder-placement")):
    if p not in sys.path:
        sys.path.insert(0, p)

import gk_trust as T  # noqa: E402
import make_scratch  # noqa: E402
import trust_write as TW  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROMPT = "This is a throwaway session that checks folder trust. Reply with the single word ready, then stop."
APPEAR_S = 60       # how long the session gets to show up in `claude agents`
SETTLE_S = 120      # and to finish its one reply
SETTLED = ("idle", "ended", "finished", "completed", "done", "stopped", "exited")


class Failed(Exception):
    pass


def steps(config: str, scratch_root: str, claude) -> list:
    return [
        "Make a scratch folder under %s: a throwaway Gnomonkit home with one temporary trusted root (auto on, "
        "local_only on) in its grants.toml, and a throwaway repository under that root (git init)." % scratch_root,
        "Copy Claude Code's file, %s, into the scratch folder before anything changes." % config,
        "Run the session-start hook (%s) with the throwaway Gnomonkit home: the repository must come out "
        "trusted." % HOOK,
        "Start `%s --bg` in the repository (as session-dispatch does): it must not stop at the trust dialog, "
        "and the session must show up in `claude agents` within %d s." % (claude or "claude", APPEAR_S),
        "Let the session settle (up to %d s), stop it, and check Claude Code kept the key." % SETTLE_S,
        "Take the key out of the repository's entry, as a copy written back without it would read, run the "
        "hook again, and check it puts the key back and logs it as reapplied.",
        "Remove the repository's entry, compare the file with the copy from step 2 (offering the copy back "
        "if they differ), and delete the scratch folder (kept if anything failed).",
    ]


# ---- edits of Claude Code's file, by Gnomonkit's writer rules -----------------------------------------
def _without(text: str, obj, key: str) -> str:
    """`text` with `key`'s member taken out of `obj`, every other byte as it was."""
    ms = obj.members
    i = next(n for n, m in enumerate(ms) if m.key == key)
    if len(ms) == 1:
        return text[:obj.open] + "{}" + text[obj.close + 1:]
    if i == 0:
        return text[:ms[0].key_start] + text[ms[1].key_start:]
    return text[:ms[i - 1].value_end] + text[ms[i].value_end:]


def _edit(path: str, change, tries: int = 3) -> None:
    """change(text, data) -> new text, with `data` (the parsed file) changed to match; written only when the
    new text parses to exactly that, under the writer's lock, with the file read again right before the
    swap. Tried again when Claude Code wrote the file in between."""
    for attempt in range(tries):
        with TW._Lock():
            raw = TW._read(path)
            data, problem = T.parse_claude_config(raw)
            if problem:
                raise Failed("%s is not a format Gnomonkit recognises (%s); nothing was changed" % (path, problem))
            text = raw.decode("utf-8")
            new = change(text, data)
            if json.loads(new) != data:
                raise Failed("the edit of %s did not check out; nothing was changed" % path)
            try:
                TW._swap(path, raw, new.encode("utf-8"))
                return
            except TW.WriteError as e:
                if e.kind != "changed" or attempt == tries - 1:
                    raise Failed(str(e)) from None
        time.sleep(1)


def _projects_obj(text: str):
    top = TW._scan_object(text, TW._ws(text, 0))
    return TW._scan_object(text, top.get("projects").value_start)


def drop_key(path: str, keys: list) -> None:
    def change(text, data):
        for key in keys:
            entry = _projects_obj(text).get(key)       # scanned again after each cut: the spans move
            if entry is None or not isinstance(data["projects"].get(key), dict):
                continue
            eobj = TW._scan_object(text, entry.value_start)
            if eobj.get(T.TRUST_KEY) is not None:
                text = _without(text, eobj, T.TRUST_KEY)
                data["projects"][key].pop(T.TRUST_KEY, None)
        return text
    _edit(path, change)


def drop_entries(path: str, keys: list) -> None:
    def change(text, data):
        for key in keys:
            pobj = _projects_obj(text)
            if pobj.get(key) is not None:
                text = _without(text, pobj, key)
                data["projects"].pop(key, None)
        return text
    _edit(path, change)


def put_back(path: str, copy: bytes) -> None:
    with TW._Lock():
        raw = TW._read(path)
        try:
            TW._swap(path, raw, copy)
        except TW.WriteError as e:
            raise Failed(str(e)) from None


# ---- Claude Code ------------------------------------------------------------------------------------
def agents(claude: list) -> list:
    try:
        r = subprocess.run([*claude, "agents", "--json", "--all"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=NO_WINDOW)
        data = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def find_session(claude: list, name: str, repo: str):
    for e in agents(claude):
        cwd = str(e.get("cwd") or "")
        if e.get("name") == name or (cwd and T.key_norm(T.G.resolve(cwd)) == T.key_norm(repo)):
            return e
    return None


def stop_session(claude: list, e: dict) -> None:
    sid = str(e.get("id") or e.get("sessionId") or "")
    if sid:
        subprocess.run([*claude, "stop", sid], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30, creationflags=NO_WINDOW)


# ---- the run -------------------------------------------------------------------------------------------
def run_hook(env: dict, config: str, repo: str, tries: int = 3) -> str:
    """The hook's message, run until the repository's key is there: a running Claude Code may write its
    file between the hook's read and its swap, and then the hook writes nothing and waits for next time."""
    msg = ""
    for attempt in range(tries):
        r = subprocess.run([sys.executable, str(HOOK)], input='{"source": "startup"}', capture_output=True,
                           text=True, encoding="utf-8", env=env, timeout=30, creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise Failed("the hook exited %d" % r.returncode)
        msg = json.loads(r.stdout)["systemMessage"] if r.stdout.strip() else ""
        if msg or trusted_keys(config, repo):
            return msg
        time.sleep(2)
    return msg


def trusted_keys(path: str, repo: str) -> list:
    cfg = T.read_claude_config(path)
    if not cfg.ok:
        raise Failed("%s can't be read now (%s)" % (path, cfg.problem))
    return [k for k in T.entry_keys(cfg.projects, repo, resolved=True)
            if isinstance(cfg.projects.get(k), dict) and cfg.projects[k].get(T.TRUST_KEY) is True]


def differences(before: bytes, after: bytes) -> list:
    a, b = json.loads(before.decode("utf-8")), json.loads(after.decode("utf-8"))
    out = ["top-level %s" % k for k in sorted(set(a) | set(b)) if k != "projects" and a.get(k) != b.get(k)]
    pa, pb = a.get("projects") or {}, b.get("projects") or {}
    out += ["projects entry %s" % k for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)]
    return out


def real(args, claude: list, config: str, scratch_root: str) -> int:
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok))
        print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, (": " + detail) if detail else ""))
        return ok

    original_env = dict(os.environ)
    scratch = Path(scratch_root) / ("trust-real-session-%s" % time.strftime("%Y%m%d-%H%M%S"))
    gk, root = scratch / "gnomonkit-home", scratch / "root"
    repo_dir = root / ("gk-trust-check-%s" % secrets.token_hex(3))
    name = repo_dir.name
    session = None
    keys: list = []
    copy = None
    keep = False
    try:
        print("\n1. Scratch folder: %s" % scratch)
        gk.mkdir(parents=True)
        make_scratch.stamp(str(scratch), "folder-trust real-session check", 3, "trust_real_session")
        repo_dir.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo_dir)], check=True, capture_output=True, creationflags=NO_WINDOW)
        repo = T.G.resolve(repo_dir)
        (gk / "grants.toml").write_text(
            'version = 1\n\n[[trust]]\nroot = %s\nlevel = "trusted"\nauto = true\nlocal_only = true\n'
            'owners = ["example-user"]\n' % json.dumps(T.G.resolve(root)), encoding="utf-8")
        os.environ["GNOMONKIT_HOME"] = str(gk)          # this process's own edits lock and log there
        hook_env = dict(original_env, GNOMONKIT_HOME=str(gk))

        print("2. Copy of %s" % config)
        copy = TW._read(config)
        (scratch / "claude.json.before").write_bytes(copy)
        if not check("the throwaway repository has no entry yet", not trusted_keys(config, repo)):
            raise Failed("stopping before anything changes")

        print("3. The session-start hook")
        msg = run_hook(hook_env, config, repo)
        keys = trusted_keys(config, repo)
        check("the hook trusted the repository", bool(keys) and repo in msg, msg.splitlines()[0] if msg else "no output")

        print("4. claude --bg in %s" % repo)
        child_env = dict(original_env, GNOMONKIT_NO_ONBOARDING="1")
        r = subprocess.run([*claude, "--bg", PROMPT, "--name", name], cwd=str(repo_dir), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                           env=child_env, creationflags=NO_WINDOW)
        said = ((r.stderr or "") + (r.stdout or "")).strip()
        check("claude --bg started without stopping at the trust dialog",
              r.returncode == 0 and "trust" not in (r.stderr or "").lower(), "exit %d %s" % (r.returncode, said[-200:]))
        until = time.monotonic() + APPEAR_S
        while session is None and time.monotonic() < until:
            session = find_session(claude, name, repo)
            if session is None:
                time.sleep(3)
        check("the session shows up in claude agents", session is not None)

        print("5. Letting it settle, then stopping it")
        state = ""
        until = time.monotonic() + SETTLE_S
        while session is not None and time.monotonic() < until:
            now = find_session(claude, name, repo) or session
            state = str(now.get("state") or "").lower()
            if state in SETTLED:
                break
            time.sleep(5)
        print("     its state: %s" % (state or "unknown"))
        if session is not None:
            stop_session(claude, session)
            time.sleep(3)
        check("Claude Code kept the key while the session ran", bool(trusted_keys(config, repo)))

        print("6. A lost key")
        cfg = T.read_claude_config(config)
        keys = T.entry_keys(cfg.projects, repo, resolved=True) if cfg.ok else []
        drop_key(config, keys)
        check("the key is gone", not trusted_keys(config, repo))
        msg = run_hook(hook_env, config, repo)
        audit = gk / "trust" / "audit.jsonl"
        events = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines()] if audit.exists() else []
        check("the next start put it back", bool(trusted_keys(config, repo)) and "put back" in msg,
              msg.splitlines()[0] if msg else "no output")
        check("and logged it as reapplied", any(e.get("event") == "reapplied" and e.get("repo") == repo for e in events))
    except Failed as e:
        check(str(e), False)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        check("%s: %s" % (type(e).__name__, e), False)
    finally:
        print("7. Cleaning up")
        if session is not None:
            stop_session(claude, session)
        try:
            where = T.G.resolve(repo_dir)
            cfg = T.read_claude_config(config)
            mine = T.entry_keys(cfg.projects, where, resolved=True) if cfg.ok else []
            if mine:
                drop_entries(config, mine)
                after = T.read_claude_config(config)
                check("the throwaway repository's entry is removed",
                      after.ok and not T.entry_keys(after.projects, where, resolved=True))
        except (Failed, OSError, ValueError) as e:
            check("removing the throwaway repository's entry: %s" % e, False)
        if copy is not None:
            now = TW._read(config)
            if now == copy:
                print("     %s is byte for byte as it was." % config)
            else:
                diff = differences(copy, now)
                print("     %s differs from the copy taken at the start in: %s" % (config, ", ".join(diff[:12])
                                                                                    + (" ..." if len(diff) > 12 else "")))
                print("     (Claude Code changes some of these itself as sessions run.)")
                print("     Type restore to put the copy back (that also undoes those changes), or press Enter "
                      "to keep the file as it is: ", end="", flush=True)
                if sys.stdin.readline().strip() == "restore":
                    try:
                        put_back(config, copy)
                        print("     Put back.")
                    except (Failed, OSError) as e:
                        check("putting the copy back: %s" % e, False)
        keep = not all(ok for _n, ok in results)
        os.environ.clear()
        os.environ.update(original_env)
        if keep:
            print("     Kept the scratch folder (the copy of the file is claude.json.before): %s" % scratch)
        elif scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
            print("     Deleted the scratch folder.")
        print("     Claude Code keeps the session's own transcript; remove it in Claude Code if you like.")
    failed = [n for n, ok in results if not ok]
    print("\n%s: %d checks, %d failed." % ("PASS" if not failed else "FAIL", len(results), len(failed)))
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--real", action="store_true", help="run the check (edits Claude Code's file; asks first)")
    mode.add_argument("--dry-run", action="store_true", help="print each step; nothing is made or changed")
    ap.add_argument("--scratch", metavar="FOLDER", help="where the scratch folder goes (default: the scratch root)")
    args = ap.parse_args(argv)
    config = T.claude_json_path()
    scratch_root = os.path.abspath(args.scratch) if args.scratch else make_scratch.scratch_root()
    found = shutil.which("claude")
    if not (args.real or args.dry_run):
        print("refused: this check edits the Claude Code file your sessions use (%s) and starts a session, so it "
              "runs only when asked: --dry-run to see the steps, --real to run them." % config, file=sys.stderr)
        return 2
    if args.dry_run:
        print("Dry run: nothing is made or changed, and Claude Code's file is not opened. --real would:")
        for n, text in enumerate(steps(config, scratch_root, found), 1):
            print("  %d. %s" % (n, text))
        if not found:
            print("\n(claude is not on PATH here, so --real would stop before step 1.)")
        return 0
    if not C.is_terminal():
        print("refused: --real runs only in a terminal where you type the answer yourself; nothing was changed.",
              file=sys.stderr)
        return 2
    if not found:
        print("refused: the claude command is not on PATH; nothing was changed.", file=sys.stderr)
        return 2
    if not os.path.isfile(config):
        print("refused: there is no %s; nothing was changed." % config, file=sys.stderr)
        return 2
    print("This edits %s (and puts it back), and starts one short background session:" % config)
    for n, text in enumerate(steps(config, scratch_root, found), 1):
        print("  %d. %s" % (n, text))
    word = "check-%04d" % secrets.randbelow(10000)
    try:
        C.require_tty(word)
    except C.CommandError as e:
        print(str(e), file=sys.stderr)
        return 2
    return real(args, [found], config, scratch_root)


if __name__ == "__main__":
    sys.exit(main())
