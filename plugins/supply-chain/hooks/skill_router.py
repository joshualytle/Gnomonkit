#!/usr/bin/env python3
"""Suggest the skill that applies to a tool call, before and after it. Never blocks.

A trimmed copy of core's router (plugins/core/hooks/skill_router.py), which documents why it is
built this way. Same contract: rules come from triggers.yaml (or its generated twin triggers.json
where PyYAML is missing), each skill is suggested once per phase per sitting, the agent's own
scratchpad is ignored, commands that only display text (echo, grep) never match, and any error
exits 0 with nothing printed. State is kept per session and per plugin, so this copy and core's can
run side by side.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "triggers.yaml")
STATE_DIR = os.environ.get("GNOMONKIT_ROUTER_STATE_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "skill-router")
EPISODE_GAP_MINUTES = 45          # a sitting ends after this much idle time (same value as core)


def _plugin_name() -> str:
    """The owning plugin's name from its manifest (installed folders are named after the version)."""
    try:
        with open(os.path.join(HERE, "..", ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return re.sub(r"[^A-Za-z0-9_-]", "", json.load(fh).get("name", "")) or "router"
    except (OSError, ValueError):
        return "router"


def load_rules(path: str = RULES) -> list:
    """triggers.yaml when PyYAML is present, else the generated triggers.json twin."""
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            with open(path, encoding="utf-8") as fh:
                return (yaml.safe_load(fh) or {}).get("triggers", []) or []
        except Exception:  # noqa: BLE001 - yaml errors vary by version
            return []
    try:
        with open(os.path.splitext(path)[0] + ".json", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("triggers", []) or []
    except (OSError, ValueError):
        return []


def _repo_of(cwd: str) -> str:
    return os.path.basename((cwd or "").rstrip("\\/")).lower()


_DISPLAYED = re.compile(r"(?:^|[|;&]\s*)\s*(?:echo|printf|grep|rg|cat|less)\b")
_SCAFFOLD = re.compile(r"(?:[/\\]|^)(?:AppData[/\\]Local[/\\])?Temp[/\\]claude[/\\]|[/\\]scratchpad[/\\]", re.I)


def match(rules: list, tool: str, command: str, path: str, cwd: str) -> list:
    """Every rule whose tool, repo scope and pattern match, minus its exclusions."""
    out = []
    in_scratch = bool(_SCAFFOLD.search(path) or _SCAFFOLD.search(command))
    repo = _repo_of(cwd)
    for r in rules:
        if in_scratch and not r.get("scratchpad"):
            continue
        tools = r.get("tools") or []
        if tools and tool not in tools:
            continue
        scope = [s.lower() for s in (r.get("repos") or [])]
        if scope and repo not in scope:
            continue
        hit = False
        if command and not _DISPLAYED.search(command):
            hit = any(re.search(p, command, re.I) for p in r.get("command_patterns") or [])
        if not hit and path:
            hit = any(re.search(p, path, re.I) for p in r.get("path_patterns") or [])
        if hit and not any(re.search(x, command or path, re.I) for x in r.get("exclude_patterns") or []):
            out.append(r)
    return out


def _already_fired(session: str, skill: str, phase: str = "pre") -> bool:
    """One suggestion per skill per phase per sitting, recorded in a per-session, per-plugin file."""
    if not session:
        return False
    os.makedirs(STATE_DIR, exist_ok=True)
    p = os.path.join(STATE_DIR, "%s-%s.json" % (re.sub(r"[^A-Za-z0-9_-]", "", session), _plugin_name()))
    now = time.time()
    seen, last = {}, 0.0
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and "skills" in raw:
            seen = {k: float(v) for k, v in (raw.get("skills") or {}).items() if isinstance(v, (int, float))}
            last = float(raw.get("last") or 0.0)
    except (OSError, ValueError, TypeError):
        seen, last = {}, 0.0
    if last and (now - last) > EPISODE_GAP_MINUTES * 60:
        seen = {}
    key = "%s:%s" % (phase, skill)
    fired = key in seen
    if not fired:
        seen[key] = now
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"last": now, "skills": seen}, fh)
    except OSError:
        pass
    return fired


PHRASING = {   # names the load: "evaluate against this skill" invites an answer from the name alone
    "pre": ("Before continuing, load this skill with the Skill tool (skip it if already loaded this session), "
            "check what you are ABOUT TO DO against it, and adjust if it applies:"),
    "post": ("Load this skill with the Skill tool (skip it if already loaded this session), "
             "check what you JUST DID against it, and correct it now if it applies:"),
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    event = str(payload.get("hook_event_name") or "PreToolUse")
    phase = "post" if event == "PostToolUse" else "pre"
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    command = str(ti.get("command") or "")
    path = str(ti.get("file_path") or ti.get("path") or "")
    if not command and not path:
        return 0
    hits = match(load_rules(), tool, command, path, payload.get("cwd") or "")
    fresh = [r for r in hits if not _already_fired(payload.get("session_id") or "", r["skill"], phase)]
    if not fresh:
        return 0
    lines = []
    for r in fresh:
        lines.append("  skill: %s" % (r["skill"] if ":" in r["skill"] else "supply-chain:" + r["skill"]))
        lines.append("    %s" % " ".join((r.get("say") or "").split()))
        lines.append("    its stated trigger: %s" % " ".join((r.get("when") or "").split()))
    out = {"hookEventName": event, "additionalContext": PHRASING[phase] + "\n" + "\n".join(lines)}
    # no permissionDecision: "allow" would skip the user's permission prompt
    print(json.dumps({"hookSpecificOutput": out}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        sys.exit(0)
