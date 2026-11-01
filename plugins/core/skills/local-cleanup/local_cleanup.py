#!/usr/bin/env python3
"""Act on cleanup verdicts, reversibly. Dry run unless --apply.

    python local_cleanup.py plan --inventory inv.json --answers answers.json   what Remove/Keep verdicts would do
    python local_cleanup.py plan --inventory inv.json --safe                   every item the inventory called safe
    python local_cleanup.py plan ... --apply                                   do it
    python local_cleanup.py list                                               what the holding area holds
    python local_cleanup.py restore <batch> [<name>]                           put it back
    python local_cleanup.py purge --older-than 14 [--apply]                    the only permanent delete

--answers takes the page's answers: the JSON list from its answers store ({section, verdict, ...}) or
the pasted "FEEDBACK:" block (lines "- <section id> [Verdict] note"). "Remove" acts, "Keep" is
remembered in ~/.gnomonkit/cleanup/keep.json so the next inventory does not ask again, anything
else is left alone. --path P (repeatable) names items directly.

What "remove" does, by kind (every item is re-inspected first; a changed item is skipped):
  worktree       `git worktree remove` (never --force). If git refuses (uncommitted files), the
                 folder is moved to holding and the registration pruned. Unpushed commits on a
                 detached HEAD get a branch first (gnomonkit-rescue/<name>); a branch is deleted
                 only when it is merged into the default branch, and never with --keep-branches.
  plain          moved to holding.
  plugin-record  the stale entry is dropped from installed_plugins.json (backed up to holding).
  clone/system   refused. A primary clone is removed by hand, after you have looked.

Holding: <home>/.gnomonkit/holding/<batch>/ when the item is on the same drive as the home folder,
else <drive root>/.gnomonkit-holding/<batch>/ (a move within one drive is instant and needs no
copy). Each batch has a manifest.json; `restore` reads it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "local-compare"))
import local_inventory as inv  # noqa: E402


def gk_home() -> str:
    return inv.gnomonkit_home()


def holding_root(path: str) -> str:
    home_drive = os.path.splitdrive(os.path.abspath(os.path.expanduser("~")))[0].lower()
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if os.name != "nt" or drive.lower() == home_drive:
        return os.path.join(gk_home(), "holding")
    return os.path.join(drive + os.sep, ".gnomonkit-holding")


def read_answers(path: str) -> dict[str, str]:
    text = open(path, encoding="utf-8").read()
    out: dict[str, str] = {}
    try:
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("answers") or data.get("documents") or []
        for r in rows:
            r = r.get("data", r) if isinstance(r, dict) else r
            if isinstance(r, dict) and r.get("section") and r.get("verdict"):
                out[r["section"]] = r["verdict"]
        return out
    except ValueError:
        pass
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*(\S+)\s*\[([^\]]+)\]", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def load_keep() -> list[str]:
    return inv.read_json(os.path.join(gk_home(), "cleanup", "keep.json")) or []


def save_keep(paths: list[str]) -> None:
    p = os.path.join(gk_home(), "cleanup", "keep.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(sorted(set(paths), key=str.lower), f, indent=1)


def reinspect(item: dict) -> dict:
    """The inventory may be minutes old; act on what is there now."""
    if item["kind"] == "plugin-record":
        return dict(item, verdict="safe" if not os.path.isdir(item["path"]) else "review")
    if not os.path.exists(item["path"]):
        if item["kind"] == "worktree":
            return dict(item, verdict="safe", why="folder already gone")
        return dict(item, verdict="gone")
    fresh = dict(item)
    if item["kind"] in ("clone", "worktree"):
        fresh.update(inv.inspect_repo(item["path"], item["kind"], item.get("main")))
    inv.classify(fresh, set())
    return fresh


def changed(before: dict, now: dict) -> str | None:
    for k in ("dirty", "unpushed", "branch"):
        if k in before and before.get(k) != now.get(k):
            return "%s changed (%s -> %s) since the inventory" % (k, before.get(k), now.get(k))
    return None


class Batch:
    def __init__(self, apply: bool, keep_branches: bool = False):
        self.apply = apply
        self.keep_branches = keep_branches
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.entries: list[dict] = []

    def hold(self, path: str) -> str:
        dest_dir = os.path.join(holding_root(path), self.id)
        name = os.path.basename(path.rstrip("\\/")) or "item"
        dest = os.path.join(dest_dir, name)
        n = 1
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(dest_dir, "%s~%d" % (name, n))
        if self.apply:
            os.makedirs(dest_dir, exist_ok=True)
            os.rename(path, dest)  # same drive: a rename, never a copy-then-delete
            self.entries.append({"from": path, "to": dest, "at": time.time()})
            self.save(dest_dir)
        return dest

    def save(self, dest_dir: str) -> None:
        mine = [e for e in self.entries if os.path.dirname(e["to"]) == dest_dir]
        with open(os.path.join(dest_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"batch": self.id, "items": mine}, f, indent=1)


def git_ok(d: str, *args: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def remove_worktree(item: dict, batch: Batch, log) -> None:
    path, main = item["path"], item.get("main")
    if not main:
        common = inv.git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
        main = os.path.dirname(common) if common else None
    if not main or not os.path.isdir(main):
        log("skip", path, "cannot find the clone this worktree belongs to")
        return
    branch = item.get("branch")
    if os.path.isdir(path) and branch == "HEAD" and item.get("unpushed"):
        rescue = "gnomonkit-rescue/" + re.sub(r"[^A-Za-z0-9._-]", "-", os.path.basename(path))
        log("branch", path, "keep %d unpushed commit(s) on %s" % (item["unpushed"], rescue))
        if batch.apply:
            head = inv.git(path, "rev-parse", "HEAD")
            git_ok(main, "branch", rescue, head)
    if not os.path.isdir(path):
        log("prune", path, "registered, folder gone")
        if batch.apply:
            git_ok(main, "worktree", "prune")
        return
    log("worktree remove", path, item.get("why", ""))
    if batch.apply:
        ok, err = git_ok(main, "worktree", "remove", path)
        if not ok:
            dest = batch.hold(path)
            git_ok(main, "worktree", "prune")
            log("held", path, "git refused (%s); moved to %s" % (err.splitlines()[0] if err else "?", dest))
    if branch and branch != "HEAD" and item.get("merged") in ("contained", "equivalent"):
        if batch.keep_branches:
            # Some repos forbid deleting a branch the session did not create; leave it for its owner.
            log("keep branch", branch, "merged, kept (--keep-branches)")
            return
        log("branch -D", branch, "merged into %s (%s)" % (item.get("base"), item["merged"]))
        if batch.apply:
            git_ok(main, "branch", "-D", branch)


def drop_plugin_record(item: dict, batch: Batch, log) -> None:
    f = os.path.join(inv.claude_home(), "plugins", "installed_plugins.json")
    log("plugin record", item["path"], "drop %s (%s)" % (item["plugin"], item["scope"]))
    if not batch.apply:
        return
    data = inv.read_json(f) or {}
    backup_dir = os.path.join(holding_root(f), batch.id)
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, "installed_plugins.json")
    if not os.path.exists(backup):
        shutil.copy2(f, backup)
        batch.entries.append({"from": f, "to": backup, "at": time.time(), "copy": True})
        batch.save(backup_dir)
    entries = data.get("plugins", {}).get(item["plugin"], [])
    keep = [e for e in entries if not (inv.norm(e.get("projectPath") or "") == inv.norm(item["path"])
                                       and e.get("scope") == item["scope"])]
    data["plugins"][item["plugin"]] = keep
    if not keep:
        del data["plugins"][item["plugin"]]
    tmp = f + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, f)


def plan(a) -> int:
    data = inv.read_json(a.inventory) or {}
    items = data.get("items") or []
    by_id = {inv.section_id(i["path"]): i for i in items}
    targets: list[dict] = []
    keeps: list[str] = []
    if a.answers:
        for sid, verdict in read_answers(a.answers).items():
            it = by_id.get(sid)
            if not it:
                continue
            if verdict.lower() == "remove":
                targets.append(it)
            elif verdict.lower() == "keep":
                keeps.append(it["path"])
    if a.safe:
        targets += [i for i in items if i.get("verdict") == "safe"]
    for p in a.path or []:
        targets += [i for i in items if inv.norm(i["path"]) == inv.norm(p)]
    batch = Batch(a.apply, a.keep_branches)
    word = "" if a.apply else "would "

    def log(action, what, why):
        print("  %s%-16s %s  -- %s" % (word, action, what, why))

    done = set()
    for item in targets:
        key = (inv.norm(item["path"]), item.get("plugin"))
        if key in done:
            continue
        done.add(key)
        if item["kind"] in ("clone", "system", "container"):
            log("refuse", item["path"], "%s folders are removed by hand, after you have looked" % item["kind"])
            continue
        now = reinspect(item)
        if now.get("verdict") == "gone":
            log("skip", item["path"], "already gone")
            continue
        why = changed(item, now)
        if why:
            log("skip", item["path"], why + "; run the inventory again")
            continue
        if a.safe and not a.answers and now.get("verdict") != "safe":
            log("skip", item["path"], "no longer safe: " + now.get("why", ""))
            continue
        if item["kind"] == "worktree":
            remove_worktree(now, batch, log)
        elif item["kind"] == "plugin-record":
            drop_plugin_record(now, batch, log)
        else:
            dest = batch.hold(item["path"]) if a.apply else os.path.join(holding_root(item["path"]), batch.id)
            log("hold", item["path"], "to " + dest)
    if keeps:
        log("keep", "%d item(s)" % len(keeps), "remembered in keep.json")
        if a.apply:
            save_keep(load_keep() + keeps)
    if not a.apply:
        print("\nDry run. Add --apply to do it. Held folders can be put back with `restore %s`." % batch.id)
    else:
        print("\nBatch %s. `list` shows it; `restore %s` puts it back." % (batch.id, batch.id))
    return 0


def manifests() -> list[tuple[str, dict]]:
    roots = {os.path.join(gk_home(), "holding")}
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            r = "%s:\\.gnomonkit-holding" % letter
            if os.path.isdir(r):
                roots.add(r)
    out = []
    for r in roots:
        for b in sorted(inv.safe_listdir(r)):
            m = inv.read_json(os.path.join(r, b, "manifest.json"))
            if m:
                out.append((os.path.join(r, b), m))
    return out


def cmd_list(a) -> int:
    for d, m in manifests():
        for e in m["items"]:
            print("  %s  %s  <- %s" % (m["batch"], e["to"], e["from"]))
    return 0


def cmd_restore(a) -> int:
    for d, m in manifests():
        if m["batch"] != a.batch:
            continue
        for e in m["items"]:
            if a.name and os.path.basename(e["to"]) != a.name:
                continue
            if os.path.exists(e["from"]) and not e.get("copy"):
                print("  skip %s: %s exists again" % (e["to"], e["from"]))
                continue
            if e.get("copy"):
                shutil.copy2(e["to"], e["from"])
            else:
                os.rename(e["to"], e["from"])
            print("  restored %s" % e["from"])
    return 0


def cmd_purge(a) -> int:
    cutoff = time.time() - a.older_than * 86400
    for d, m in manifests():
        stamp = time.mktime(time.strptime(m["batch"], "%Y%m%d-%H%M%S"))
        if stamp > cutoff:
            continue
        parent = os.path.basename(os.path.dirname(d))
        if parent not in ("holding", ".gnomonkit-holding") or not re.fullmatch(r"\d{8}-\d{6}", os.path.basename(d)):
            print("  refuse %s: not a holding batch folder" % d)  # purge never deletes outside holding
            continue
        mb, _ = inv.size_mb(d)
        print("  %s %s (%s MB, %d items)" % ("delete" if a.apply else "would delete", d, "{:,}".format(mb), len(m["items"])))
        if a.apply:
            shutil.rmtree(d)
    if not a.apply:
        print("\nDry run. purge is the only permanent step; add --apply once the list above is what you expect.")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--inventory", required=True)
    p.add_argument("--answers")
    p.add_argument("--safe", action="store_true")
    p.add_argument("--path", action="append")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--keep-branches", action="store_true",
                   help="never delete a branch, even a merged one (for repos that forbid deleting branches you did not create)")
    sub.add_parser("list")
    r = sub.add_parser("restore")
    r.add_argument("batch")
    r.add_argument("name", nargs="?")
    g = sub.add_parser("purge")
    g.add_argument("--older-than", type=float, default=14)
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    return {"plan": plan, "list": cmd_list, "restore": cmd_restore, "purge": cmd_purge}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
