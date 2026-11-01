#!/usr/bin/env python3
"""Read-only inventory of local repo copies and folders, each with a verdict.

    python local_inventory.py                       table for the default roots
    python local_inventory.py --root C:\\Projects   one or more roots (repeatable)
    python local_inventory.py --json out.json       machine-readable, the input to local_cleanup.py
    python local_inventory.py --page out.html       a report page with Remove / Keep / Look first per item

Roots come from --root, else the core plugin's cleanup_roots option or GNOMONKIT_CLEANUP_ROOTS
(os.pathsep-separated: ';' on Windows, ':' elsewhere), else the folder that
holds the current repo plus, on Windows, the root of its drive. Each root is listed one level deep;
every worktree registered by a clone found there is added wherever it lives.

Verdicts:
  safe    a worktree that is clean, has nothing unpushed, whose branch is merged (contained in, or
          patch-equivalent to, the default branch, or its upstream was deleted), and that no session
          has used recently; or a folder whose .gnomonkit-made.json has expired.
  review  anything else that is not a primary clone: uncommitted or unpushed work, unmerged
          branches, folders nobody recorded making.
  keep    primary clones, system folders, and anything the user marked keep before
          (~/.gnomonkit/cleanup/keep.json).

Nothing here writes, moves or deletes. It never runs a git command that changes state.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

MARKER = ".gnomonkit-made.json"
RECENT_SESSION_HOURS = 6
SIZE_FILE_CAP = 300_000
# Top-level names an OS or vendor owns. Never offered for cleanup.
SYSTEM_NAMES = {
    "windows", "program files", "program files (x86)", "programdata", "users", "perflogs", "recovery",
    "system volume information", "documents and settings", "$recycle.bin", "$sysreset", "$windows.~bt",
    "$windows.~ws", "msocache", "intel", "amd", "nvidia", "inetpub", "xboxgames", "onedrivetemp",
    "bin", "boot", "dev", "etc", "lib", "lib64", "proc", "sbin", "sys", "usr", "var", "run", "opt", "srv",
    "mnt", "media", "home", "root", "snap", "lost+found", "applications", "library", "system", "volumes",
    "private", "cores",
}


def gnomonkit_home() -> str:
    return os.environ.get("GNOMONKIT_HOME") or os.path.join(os.path.expanduser("~"), ".gnomonkit")


def claude_home() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def git(d: str, *args: str, timeout: int = 30) -> str | None:
    try:
        r = subprocess.run(["git", "-C", d, *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def default_roots() -> list[str]:
    env = os.environ.get("CLAUDE_PLUGIN_OPTION_CLEANUP_ROOTS") or os.environ.get("GNOMONKIT_CLEANUP_ROOTS")
    if env:
        return [r for r in env.split(os.pathsep) if r]
    top = git(os.getcwd(), "rev-parse", "--show-toplevel")
    here = os.path.dirname(os.path.abspath(top)) if top else os.getcwd()
    roots = [here]
    if os.name == "nt":
        drive = os.path.splitdrive(here)[0]
        if drive:
            roots.append(drive + os.sep)
    return roots


def is_hidden_or_system(path: str) -> bool:
    name = os.path.basename(path.rstrip("\\/"))
    if name.startswith("$") or name.lower().startswith("windows.old"):
        return True
    if os.name == "nt":
        try:
            attrs = os.stat(path, follow_symlinks=False).st_file_attributes
            return bool(attrs & 0x2 or attrs & 0x4)  # HIDDEN, SYSTEM
        except (OSError, AttributeError):
            return False
    return False


def size_mb(path: str) -> tuple[int, bool]:
    total, n = 0, 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
            n += 1
            if n >= SIZE_FILE_CAP:
                return total // 2**20, True
    return total // 2**20, False


def session_dir_name(path: str) -> str:
    """Claude Code names a project's transcript folder by replacing every non-alphanumeric with '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def last_session_age_hours(path: str, projects: dict) -> float | None:
    d = projects.get(session_dir_name(path).lower())
    if not d:
        return None
    newest = 0.0
    try:
        for f in os.listdir(d):
            if f.endswith(".jsonl"):
                newest = max(newest, os.path.getmtime(os.path.join(d, f)))
    except OSError:
        return None
    return (time.time() - newest) / 3600 if newest else None


def default_branch(d: str) -> str | None:
    ref = git(d, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if ref:
        return ref
    for cand in ("origin/main", "origin/master", "origin/dev"):
        if git(d, "rev-parse", "--verify", "--quiet", cand) is not None:
            return cand
    return None


def merged_state(d: str, base: str | None, upstream_gone: bool) -> str:
    """'contained' | 'equivalent' | 'upstream-gone' | 'no' | 'unknown'."""
    if not base:
        return "upstream-gone" if upstream_gone else "unknown"
    if git(d, "merge-base", "--is-ancestor", "HEAD", base) is not None:
        return "contained"
    cherry = git(d, "cherry", base, "HEAD")
    if cherry is not None and not any(line.startswith("+") for line in cherry.splitlines()):
        return "equivalent"  # rebase or squash-like merge: every patch is already on the default branch
    return "upstream-gone" if upstream_gone else "no"


def inspect_repo(path: str, kind: str, main: str | None) -> dict:
    item = {"path": path, "kind": kind, "main": main}
    item["branch"] = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    status = git(path, "status", "--porcelain")
    lines = status.splitlines() if status is not None else []
    item["dirty"] = len(lines) if status is not None else None
    # Tracked files that vanished: a temp-folder purge eats worktrees left in scratch space.
    item["deleted"] = sum(1 for line in lines if line[:2].strip() == "D")
    track = git(path, "for-each-ref", "--format=%(upstream:track)", "refs/heads/%s" % (item["branch"] or ""))
    gone = bool(track and "gone" in track)
    # Unpushed means "exists on no remote": what removal could lose. Counting against the upstream
    # overstated it badly for a branch that tracks origin/main: every commit not yet merged counted.
    n = git(path, "rev-list", "--count", "HEAD", "--not", "--remotes")
    item["unpushed"] = int(n) if n and n.isdigit() else None
    base = default_branch(path)
    item["base"] = base
    item["merged"] = merged_state(path, base, gone)
    last = git(path, "log", "-1", "--format=%ct")
    item["last_commit"] = int(last) if last and last.isdigit() else None
    return item


def safe_listdir(path: str) -> list[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []


def marker(path: str) -> dict | None:
    m = read_json(os.path.join(path, MARKER))
    return m if isinstance(m, dict) else None


def classify(item: dict, keep: set) -> None:
    reasons = []
    if norm(item["path"]) in keep:
        item["verdict"], item["why"] = "keep", "you marked it keep"
        return
    kind = item["kind"]
    if kind == "system":
        item["verdict"], item["why"] = "keep", "system or vendor folder"
        return
    if kind == "clone":
        item["verdict"], item["why"] = "keep", "primary clone (never offered automatically)"
        if item.get("dirty"):
            item["why"] += "; %d uncommitted" % item["dirty"]
        return
    if kind == "container":
        item["verdict"], item["why"] = "keep", "holds repos; listed one level in"
        return
    if kind == "worktree":
        if item.get("dirty") and item.get("deleted", 0) * 2 >= item["dirty"]:
            reasons.append("%d uncommitted, %d of them deleted files (folder partly purged?)"
                           % (item["dirty"], item["deleted"]))
        elif item.get("dirty"):
            reasons.append("%d uncommitted" % item["dirty"])
        if item.get("unpushed"):
            reasons.append("%d unpushed" % item["unpushed"])
        if item.get("merged") not in ("contained", "equivalent", "upstream-gone"):
            reasons.append("branch not merged" if item.get("merged") == "no" else "merge state unknown")
        age = item.get("session_age_h")
        if age is not None and age < RECENT_SESSION_HOURS:
            reasons.append("a session used it %.1fh ago" % age)
        if not os.path.isdir(item["path"]):
            item["verdict"], item["why"] = "safe", "registered but its folder is gone (git worktree prune)"
            return
        if reasons:
            item["verdict"], item["why"] = "review", "; ".join(reasons)
        else:
            item["verdict"], item["why"] = "safe", "clean, pushed, branch %s" % item["merged"]
        return
    m = item.get("marker")
    if m and m.get("expires") and float(m["expires"]) < time.time():
        item["verdict"], item["why"] = "safe", "made by %s for %s; expired" % (m.get("by", "?"), m.get("purpose", "?"))
        return
    if m:
        item["verdict"], item["why"] = "review", "made by %s for %s" % (m.get("by", "?"), m.get("purpose", "?"))
        return
    item["verdict"], item["why"] = "review", "no record of who made it"


def stale_installs() -> list[dict]:
    data = read_json(os.path.join(claude_home(), "plugins", "installed_plugins.json")) or {}
    out = []
    for key, entries in (data.get("plugins") or {}).items():
        for e in entries:
            p = e.get("projectPath")
            if p and not os.path.isdir(p):
                out.append({"path": p, "kind": "plugin-record", "plugin": key, "scope": e.get("scope"),
                            "verdict": "safe", "why": "%s %s install for a folder that no longer exists" % (key, e.get("scope"))})
    return out


def collect(roots: list[str], with_size: bool = True) -> list[dict]:
    keep = {norm(p) for p in (read_json(os.path.join(gnomonkit_home(), "cleanup", "keep.json")) or [])}
    pdir = os.path.join(claude_home(), "projects")
    try:
        projects = {n.lower(): os.path.join(pdir, n) for n in os.listdir(pdir)}
    except OSError:
        projects = {}
    seen: dict[str, dict] = {}
    root_keys = [norm(r) for r in roots]

    def holds_root(key: str) -> bool:
        # C:\ as a root must not list C:\Projects (another root) as one giant folder.
        return any(r == key or r.startswith(key.rstrip(os.sep) + os.sep) for r in root_keys)

    def scan(parent: str, depth: int) -> None:
        try:
            names = sorted(os.listdir(parent))
        except OSError:
            return
        for name in names:
            path = os.path.join(parent, name)
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            key = norm(path)
            if key in seen or (holds_root(key) and depth == 0 and norm(parent) != key):
                if holds_root(key) and key not in seen and key not in root_keys:
                    seen[key] = {"path": path, "kind": "container"}
                continue
            dot_git = os.path.join(path, ".git")
            if name.lower() in SYSTEM_NAMES or is_hidden_or_system(path):
                seen[key] = {"path": path, "kind": "system"}
            elif os.path.isdir(dot_git):
                seen[key] = {"path": path, "kind": "clone"}
            elif os.path.isfile(dot_git):
                seen[key] = {"path": path, "kind": "worktree"}
            elif depth == 0 and any(os.path.exists(os.path.join(path, c, ".git"))
                                    for c in safe_listdir(path)):
                seen[key] = {"path": path, "kind": "container"}
                scan(path, depth + 1)
            else:
                seen[key] = {"path": path, "kind": "plain", "marker": marker(path)}

    for root in roots:
        scan(root, 0)
    # every worktree a clone registers, wherever it lives
    for item in [i for i in seen.values() if i["kind"] == "clone"]:
        listing = git(item["path"], "worktree", "list", "--porcelain") or ""
        for line in listing.splitlines():
            if line.startswith("worktree "):
                wt = line[len("worktree "):].strip()
                if norm(wt) != norm(item["path"]) and norm(wt) not in seen:
                    seen[norm(wt)] = {"path": os.path.normpath(wt), "kind": "worktree", "main": item["path"]}

    def fill(item):
        if item["kind"] in ("clone", "worktree") and os.path.isdir(item["path"]):
            main = item.get("main")
            if item["kind"] == "worktree" and not main:
                common = git(item["path"], "rev-parse", "--path-format=absolute", "--git-common-dir")
                main = os.path.normpath(os.path.dirname(common)) if common else None
            item.update(inspect_repo(item["path"], item["kind"], main))
        if item["kind"] not in ("system", "container") and with_size and os.path.isdir(item["path"]):
            item["size_mb"], item["size_capped"] = size_mb(item["path"])
        if os.path.isdir(item["path"]):
            item["modified"] = os.path.getmtime(item["path"])
            item["session_age_h"] = last_session_age_hours(item["path"], projects)
        classify(item, keep)
        return item

    with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 4) * 2)) as pool:
        items = list(pool.map(fill, seen.values()))
    items += stale_installs()
    order = {"safe": 0, "review": 1, "keep": 2}
    return sorted(items, key=lambda i: (order[i["verdict"]], -(i.get("size_mb") or 0), i["path"].lower()))


def fmt_size(i: dict) -> str:
    if i.get("size_mb") is None:
        return ""
    return ("%s+" if i.get("size_capped") else "%s") % "{:,}".format(i["size_mb"]) + " MB"


def table(items: list[dict]) -> str:
    rows = []
    for v in ("safe", "review", "keep"):
        group = [i for i in items if i["verdict"] == v and i["kind"] != "system"]
        if not group:
            continue
        mb = sum(i.get("size_mb") or 0 for i in group)
        rows.append("\n%s (%d, %s MB)" % (v.upper(), len(group), "{:,}".format(mb)))
        for i in group:
            rows.append("  %-9s %10s  %-60s %s" % (i["kind"][:9], fmt_size(i), i["path"], i["why"]))
    return "\n".join(rows).lstrip("\n")


def section_id(path: str) -> str:
    return "p-" + hashlib.sha1(norm(path).encode("utf-8")).hexdigest()[:12]


def page(items: list[dict], template: str) -> str:
    shown = [i for i in items if i["kind"] != "system" and i["verdict"] != "keep"]
    safe = [i for i in shown if i["verdict"] == "safe"]
    mb = sum(i.get("size_mb") or 0 for i in safe)
    total = sum(i.get("size_mb") or 0 for i in shown)
    parts = ['<header>\n  <h1>Local cleanup</h1>\n  <p class="meta">As of %s · %d items to decide, %s MB in all</p>\n</header>\n<main>'
             % (time.strftime("%Y-%m-%d"), len(shown), "{:,}".format(total))]
    parts.append('  <section id="answer">\n    <h2>The answer</h2>\n    <p>%d items are safe to remove (%s MB): clean, pushed and merged. '
                 '%d need a look first. Mark each one; nothing is removed until you do, and removal moves '
                 'folders to a holding area you can restore from.</p>\n  </section>'
                 % (len(safe), "{:,}".format(mb), len(shown) - len(safe)))
    for i in shown:
        bits = [html.escape(i["why"])]
        if i.get("branch"):
            bits.append("branch <code>%s</code>" % html.escape(i["branch"]))
        if fmt_size(i):
            bits.append(fmt_size(i))
        parts.append('  <section id="%s" data-ask="Remove|Keep|Look first">\n    <h2>%s</h2>\n    <p><code>%s</code></p>\n    <p>%s · %s</p>\n  </section>'
                     % (section_id(i["path"]), html.escape(os.path.basename(i["path"].rstrip("\\/")) or i["path"]),
                        html.escape(i["path"]), i["verdict"], " · ".join(bits)))
    parts.append("</main>")
    start, end = template.index("<header>"), template.index("</main>") + len("</main>")
    out = template[:start] + "\n".join(parts) + template[end:]
    out = re.sub(r"<title>.*?</title>", "<title>Local cleanup</title>", out, count=1, flags=re.S)
    desc = "Which local folders, clones and worktrees can go: a verdict per item."
    return re.sub(r'(<meta name="description" content=")[^"]*(")', lambda m: m.group(1) + desc + m.group(2), out, count=1)


def repair_argv(argv: list[str]) -> list[str]:
    """Undo the Windows trailing-backslash trap. PowerShell 5.1 passes `--root 'C:\\'` as `"C:\\"`, and
    the C runtime reads `\\"` as a literal quote, so one argument swallows the rest of the line
    (`C:" --json out.json`). Split it back: the root keeps its backslash, the rest become arguments."""
    out = []
    for arg in argv:
        if '"' in arg:
            head, _, tail = arg.partition('"')
            out.append(head + "\\")
            out.extend(tail.split())
        else:
            out.append(arg)
    return out


def main(argv=None) -> int:
    argv = repair_argv(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", action="append")
    ap.add_argument("--json", metavar="FILE")
    ap.add_argument("--page", metavar="FILE")
    ap.add_argument("--no-size", action="store_true", help="skip folder sizes (faster)")
    a = ap.parse_args(argv)
    items = collect(a.root or default_roots(), with_size=not a.no_size)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"generated": time.time(), "items": items}, f, indent=1)
    if a.page:
        here = os.path.dirname(os.path.abspath(__file__))
        tpl = os.path.join(here, "..", "report-pages", "template.html")
        with open(tpl, encoding="utf-8") as f:
            text = page(items, f.read())
        with open(a.page, "w", encoding="utf-8") as f:
            f.write(text)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(table(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
