#!/usr/bin/env python3
"""Fixtures for the supply-chain plugin's router, read from its triggers.yaml.

skill_router.py and build_triggers_json.py here are copies of core's, the same arrangement an
overlay uses: each plugin that ships the router reads its own triggers and keeps its own state
file. This runner checks every `fire` case fires, every `quiet` case stays quiet, the agent's
scratchpad stays quiet, and triggers.json matches the YAML.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import skill_router as sr  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rules = sr.load_rules()
    if not rules:
        print("  FAIL: no rules loaded")
        return 1
    passed = failed = 0
    for rule in rules:
        fx = rule.get("fixtures") or {}
        tool = (rule.get("tools") or ["Bash"])[0]
        for want, cases in (("fire", fx.get("fire") or []), ("quiet", fx.get("quiet") or [])):
            for case in cases:
                for t in rule.get("tools") or [tool]:
                    hit = any(h["skill"] == rule["skill"] for h in sr.match(rules, t, case, "", "anyrepo"))
                    if hit == (want == "fire"):
                        passed += 1
                    else:
                        failed += 1
                        print("  FAIL  %s via %s: should %s -> %r" % (rule["skill"], t, want.upper(), case))
        if not fx.get("quiet"):
            failed += 1
            print("  FAIL  %s: has no quiet fixtures" % rule["skill"])

    scratch = "cd C:\\Users\\me\\AppData\\Local\\Temp\\claude\\s1\\scratchpad\\ && npm install left-pad"
    if sr.match(rules, "Bash", scratch, "", "anyrepo"):
        failed += 1
        print("  FAIL  a scratchpad command fired")
    else:
        passed += 1

    if sr._plugin_name() != "supply-chain":
        failed += 1
        print("  FAIL  router state is not keyed on this plugin's name")
    else:
        passed += 1

    try:
        import yaml  # noqa: F401
        with open(os.path.join(HERE, "triggers.json"), encoding="utf-8") as fh:
            twin = (json.load(fh) or {}).get("triggers", [])
        if twin == rules:
            passed += 1
        else:
            failed += 1
            print("  FAIL  triggers.json is stale -- run build_triggers_json.py")
    except ImportError:
        print("  SKIP  triggers.json sync check (pyyaml not installed)")

    print("\n  %d/%d fixtures behaved as specified" % (passed, passed + failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
