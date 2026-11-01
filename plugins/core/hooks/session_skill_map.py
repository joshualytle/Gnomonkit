#!/usr/bin/env python3
"""Inject the skill map once per session, with the procedure that makes it work.

WHAT THIS IS FOR. Claude Code has no routing layer: every installed skill's
description is formatted into the Skill tool's prompt and the model picks from
that flat list. There is no retrieval stage to improve, so the only way to add
structure is to put the structure IN CONTEXT and let the model use it.

WHY THIS SHAPE. The design is: distil the ask first, then consult a
situation-shaped action map, then reflect. The alternatives it was chosen over
are the flat list of descriptions; flat plus generated user phrasings; flat plus
"NOT for" boundaries; a topic taxonomy; and an action taxonomy alone. The way to
compare them is to collect (user message -> skill invoked) pairs from your own
history and route them with a judge model under each strategy; do that before
trusting any ranking.

Generated phrasings are the trap to expect: they tend to do WORSE than the
authors' own descriptions, because every phrase makes its skill sound more
broadly applicable and so increases overlap with its neighbours. The map and
the distil step ship together because distillation alone is not a dependable
gain; the structure in the map is what it routes against.

MIND THE NOISE FLOOR. With a small labelled set, repeated runs of the same
strategy differ by a case or so, so treat a gap of a case or two as noise.

WHY SESSIONSTART AND NOT USERPROMPTSUBMIT. The map costs ~600 tokens. Once per
session that is negligible; on every prompt it would be a per-turn tax on a
benefit that only applies when starting a task.

WHY THIS DOES NOT REPLACE THE TRIGGER HOOK. They solve different halves.
skill_router.py fires on a tool call -- the moment you are ABOUT TO DO
something -- and narrows to one skill deterministically. This one covers the
moment someone ASKS for something, where no path or command exists yet.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAP = os.path.join(HERE, "skill_map.yaml")
# Runtime twin generated from the YAML (python build_skill_map_json.py). Most machines have no
# PyYAML; without this fallback the map silently never loaded there.
MAP_JSON = os.path.join(HERE, "skill_map.json")


def load_map() -> dict:
    # The JSON twin first: a test keeps it identical to the YAML, and importing PyYAML and parsing
    # the YAML in pure Python costs more than the rest of this hook on every session start.
    try:
        with open(MAP_JSON, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("situations", {}) or {}
    except (OSError, ValueError):
        pass
    try:
        import yaml
        with open(MAP, encoding="utf-8") as fh:
            return (yaml.safe_load(fh) or {}).get("situations", {}) or {}
    except Exception:
        return {}


def render(situations: dict) -> str:
    rows = []
    for name, skills in situations.items():
        if not skills:
            continue
        rows.append(f"  {name}: " + ", ".join(skills))
    return "\n".join(rows)


def main() -> int:
    situations = load_map()
    if not situations:
        return 0

    body = (
        "SKILL ROUTING MAP. Installed skills grouped by the SITUATION they "
        "belong to.\n\n"
        + render(situations)
        + "\n\nHOW TO USE IT. When a request arrives, first restate the ask as "
          "a short\nimperative task, discarding schedule, background and "
          "pleasantries. Then find\nthe situation that task puts you in, and "
          "check that row before acting.\n\n"
          "WHY. Distilling the ask first and then consulting this map "
          "narrows the\nchoice to a few skills that fit the moment, which is "
          "easier to get right than\npicking from the flat list of every skill. "
          "Reading the whole list and hoping\nto recognise something is the "
          "weakest approach.\n\n"
          "This map is a prompt to CONSIDER a skill, not an instruction to "
          "invoke one.\nMost requests need no skill, and a wrong skill costs "
          "more than none."
    )

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": body,
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Never break a session start over a routing hint.
        sys.exit(0)
