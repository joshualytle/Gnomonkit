#!/usr/bin/env python3
"""Fixtures for the skill router, read from triggers.yaml itself.

The fixtures are NOT written here. They live beside the rules they test, so a
new trigger cannot be added without its own fire/quiet cases, and a rule whose
pattern is edited fails here rather than silently changing behaviour. This is
the same single-source arrangement as rejects.yaml, adopted for the same
reason: the previous split copies had already drifted apart.

A `quiet` case is worth more than a `fire` case. A trigger that misses is a
missed suggestion; a trigger that fires constantly trains the reader to ignore
the channel, and this channel is shared with the destructive interlock.
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import skill_router as sr  # noqa: E402


def _tool_for(rule: dict, is_cmd: bool) -> str:
    tools = rule.get("tools") or []
    if is_cmd:
        return "Bash" if "Bash" in tools else (tools[0] if tools else "Bash")
    return "Write" if "Write" in tools else (tools[0] if tools else "Write")


def _run(rules: list, rule: dict, case, is_cmd: bool, cwd: str) -> list:
    """Match one fixture. A dict fixture {command or path, branch} stands in for the checkout's branch."""
    branch = ""
    if isinstance(case, dict):
        branch = case.get("branch") or ""
        case = case.get("command") or case.get("path") or ""
    cmd, path = (case, "") if is_cmd else ("", case)
    real = sr.branch_of
    sr.branch_of = lambda _cwd: branch
    try:
        return sr.match(rules, _tool_for(rule, is_cmd), cmd, path, cwd)
    finally:
        sr.branch_of = real


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rules = sr.load_rules()
    if not rules:
        print("  FAIL: no rules loaded (is pyyaml installed?)")
        return 1

    passed = failed = 0
    for rule in rules:
        skill = rule["skill"]
        fx = rule.get("fixtures") or {}
        is_cmd = bool(rule.get("command_patterns"))
        # A repo-scoped rule must be tested INSIDE its scope, otherwise every
        # fire fixture fails for the right reason and hides a wrong pattern.
        cwd = (rule.get("repos") or ["anyrepo"])[0]

        for case in fx.get("fire") or []:
            hits = _run(rules, rule, case, is_cmd, cwd)
            if any(h["skill"] == skill for h in hits):
                passed += 1
            else:
                failed += 1
                print(f"  FAIL  {skill}: should FIRE but did not -> {case!r}")

        for case in fx.get("quiet") or []:
            hits = _run(rules, rule, case, is_cmd, cwd)
            if any(h["skill"] == skill for h in hits):
                failed += 1
                print(f"  FAIL  {skill}: should stay QUIET but fired -> {case!r}")
            else:
                passed += 1

        if not fx.get("quiet"):
            failed += 1
            print(f"  FAIL  {skill}: has no `quiet` fixtures -- a trigger "
                  f"nobody checked for false positives")

        # follow_through is read by the effectiveness report, not the router:
        # each `follow` fixture must match one of the rule's patterns.
        follow = rule.get("follow_through") or []
        if follow and not fx.get("follow"):
            failed += 1
            print(f"  FAIL  {skill}: has follow_through but no `follow` fixtures")
        for case in fx.get("follow") or []:
            if any(re.search(p, case, re.I) for p in follow):
                passed += 1
            else:
                failed += 1
                print(f"  FAIL  {skill}: follow fixture matches no follow_through pattern -> {case!r}")

    # Cross-rule check: a fire fixture for one skill must not trip a DIFFERENT
    # skill's rule. That is how a router starts emitting three suggestions per
    # edit and becomes wallpaper.
    for rule in rules:
        for case in (rule.get("fixtures") or {}).get("fire") or []:
            is_cmd = bool(rule.get("command_patterns"))
            cwd = (rule.get("repos") or ["anyrepo"])[0]
            others = [h["skill"] for h in _run(rules, rule, case, is_cmd, cwd)
                      if h["skill"] != rule["skill"]]
            if others:
                failed += 1
                print(f"  FAIL  {case!r} also fired {others} "
                      f"(expected only {rule['skill']})")
            else:
                passed += 1

    # The agent's scratchpad stays quiet, except for a rule that opts in with
    # `scratchpad: true`: a report page is drafted there before it is published.
    scratch = "C:\\Users\\me\\AppData\\Local\\Temp\\claude\\s1\\scratchpad\\"
    for path, want in ((scratch + "package.json", []), (scratch + "probe-results.md", ["report-pages"])):
        got = sorted(h["skill"] for h in sr.match(rules, "Write", "", path, "anyrepo"))
        if got == want:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL  scratchpad {path!r} fired {got}, expected {want}")

    # branch_of reads HEAD from a synthetic checkout, a linked worktree whose .git is a file, a
    # subfolder of either, a detached HEAD, and a folder outside any repository.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        def put(rel: str, text: str) -> None:
            full = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(text)
        put(os.path.join("app", ".git", "HEAD"), "ref: refs/heads/main\n")
        put(os.path.join("app", "src", "keep"), "")
        put(os.path.join("app", ".git", "worktrees", "wt", "HEAD"), "ref: refs/heads/feat/x\n")
        put(os.path.join("wt", ".git"), "gitdir: " + os.path.join(tmp, "app", ".git", "worktrees", "wt") + "\n")
        put(os.path.join("bare-head", ".git", "HEAD"), "0123456789abcdef0123456789abcdef01234567\n")
        os.makedirs(os.path.join(tmp, "none"))
        for rel, want in (("app", "main"), (os.path.join("app", "src"), "main"), ("wt", "feat/x"),
                          ("bare-head", ""), ("none", "")):
            got = sr.branch_of(os.path.join(tmp, rel))
            if got == want:
                passed += 1
            else:
                failed += 1
                print(f"  FAIL  branch_of({rel!r}) -> {got!r}, expected {want!r}")

    # triggers.json is what the router reads where pyyaml is missing (a
    # stock Windows Python, for one). It is generated from the yaml, so it is checked,
    # not trusted: a stale twin means those machines route on rules nobody
    # tested here. Without pyyaml the fixtures above already ran from the
    # twin, so the comparison is skipped rather than failed.
    try:
        import yaml  # noqa: F401
        have_yaml = True
    except ImportError:
        have_yaml = False
    if have_yaml:
        try:
            with open(os.path.join(HERE, "triggers.json"), encoding="utf-8") as fh:
                twin = (json.load(fh) or {}).get("triggers", []) or []
        except (OSError, ValueError) as e:
            twin = f"unreadable: {e}"
        if twin == rules:
            passed += 1
        else:
            failed += 1
            print("  FAIL  triggers.json is stale -- run build_triggers_json.py")
    else:
        print("  SKIP  triggers.json sync check (pyyaml not installed; "
              "the fixtures above ran from the json twin)")

    # And the loader itself must hand back the same list when `import yaml`
    # fails, which is the path every pyyaml-less machine takes.
    missing = object()
    saved = sys.modules.get("yaml", missing)
    sys.modules["yaml"] = None
    try:
        fallback = sr.load_rules()
    finally:
        if saved is missing:
            del sys.modules["yaml"]
        else:
            sys.modules["yaml"] = saved
    if fallback == rules:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  loader without pyyaml returned {len(fallback)} rules, "
              f"expected {len(rules)}")

    print(f"\n  {passed}/{passed + failed} fixtures behaved as specified")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
