#!/usr/bin/env python3
"""Generate skill_map.json from skill_map.yaml, the runtime copy session_skill_map.py falls back to
when PyYAML is not installed (most machines). Run after editing the YAML:

    python build_skill_map_json.py           # regenerate
    python build_skill_map_json.py --check   # exit 1 when skill_map.json is stale (CI)
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.path.join(HERE, "skill_map.yaml")
JSON_PATH = os.path.join(HERE, "skill_map.json")


def render() -> str:
    import yaml
    with open(YAML_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return json.dumps({"situations": data.get("situations", {}) or {}}, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 if skill_map.json is stale")
    a = ap.parse_args()
    want = render()
    if a.check:
        try:
            have = open(JSON_PATH, encoding="utf-8").read()
        except OSError:
            have = ""
        if have != want:
            print("skill_map.json is stale: run python build_skill_map_json.py", file=sys.stderr)
            return 1
        return 0
    with open(JSON_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(want)
    print("wrote", JSON_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
