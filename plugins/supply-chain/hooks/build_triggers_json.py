#!/usr/bin/env python3
"""Write triggers.json from triggers.yaml, so the router runs without pyyaml.

A copy of core's builder (plugins/core/hooks/build_triggers_json.py explains the
twin): the router runs under whatever Python is on the machine, often without
pyyaml, and then reads this JSON instead. This is a build-time tool: it needs pyyaml, the router does not. Run it after
every edit to triggers.yaml. test_skill_router.py fails when the twin is
stale, and `--check` does the same from a shell or CI.

Usage:
    python build_triggers_json.py           # rewrite triggers.json
    python build_triggers_json.py --check   # exit 1 when triggers.json is stale
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.path.join(HERE, "triggers.yaml")
JSON_PATH = os.path.join(HERE, "triggers.json")


def load_yaml(path: str = YAML_PATH) -> list:
    import yaml  # build-time only; the router itself must not need it

    with open(path, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("triggers", []) or []


def load_json(path: str = JSON_PATH) -> list:
    with open(path, encoding="utf-8") as fh:
        return (json.load(fh) or {}).get("triggers", []) or []


def render(rules: list) -> str:
    return json.dumps({"triggers": rules}, indent=2, ensure_ascii=False,
                      sort_keys=False) + "\n"


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when triggers.json does not match triggers.yaml")
    args = ap.parse_args(argv)

    rules = load_yaml()
    if args.check:
        try:
            have = load_json()
        except (OSError, ValueError) as e:
            print(f"STALE: cannot read {JSON_PATH}: {e}")
            return 1
        if have != rules:
            print("STALE: triggers.json does not match triggers.yaml "
                  "-- run build_triggers_json.py")
            return 1
        print(f"ok: triggers.json matches triggers.yaml ({len(rules)} rules)")
        return 0

    with open(JSON_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(rules))
    print(f"wrote {JSON_PATH} ({len(rules)} rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
