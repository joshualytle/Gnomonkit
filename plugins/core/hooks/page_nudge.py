#!/usr/bin/env python3
"""UserPromptSubmit: when a prompt asks for a breakdown, comparison, ranking or decision over data, say so.

The report-pages skill's router trigger matches only when a report-shaped file is being written, so a
plain data question ("which regions grew, and by how much?") is answered as chat text and the skill
never loads. When the prompt reads like a question whose answer is several items with numbers (a
table, a ranking, a choice between options), this adds a short note pointing at core:report-pages.

It stays quiet for a single-number question ("how many rows", "what's the total"), for code tasks,
and for short chit-chat: a nudge on every prompt is noise. The skill does the judging; this only
makes sure it is considered.

Two tiers. When the prompt states a decision the reader must make ("I have to decide", "which should
we fund"), the note is firm: a page, one data-ask section per option, even when the result is short.
The softer note's "answer in chat if it is one line" let a short decision become a chat table.

Never blocks, never echoes the prompt back, never calls the network. GNOMONKIT_PAGE_NUDGE=off turns
it off. Any failure exits 0 silently.
"""
from __future__ import annotations

import json
import os
import re
import sys

MIN_WORDS = 6
_FENCE = re.compile(r"```.*?(```|$)", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")

# The answer is several items laid side by side. Fixtures in test_page_nudge.py hold synthetic
# prompts that must fire and prompts that must stay quiet (detector-calibration).
_SHAPE = [
    r"\bbreak(s|ing)? (it |this |that |them |these |those )?down\b",
    r"\bbreakdown\b",
    r"\bbroken (down|out)\b",
    r"\b(group(ed)?|split|bucket(ed)?|broken out) by\b",
    r"\b(compare|comparing|comparison|contrast)\b",
    r"\bside[- ]by[- ]side\b",
    r"\b\w+ (vs\.?|versus) \w+",
    r"\b(rank|ranked|ranking|rankings|leaderboard)\b",
    r"\b(top|bottom) (\d+|three|five|ten|twenty)\b",
    r"\bsort(ed)? (\w+ ){0,3}by\b",
    r"\b(biggest|largest|smallest|fastest|slowest|cheapest|most expensive) (\w+ )?(\w+s)\b",
    r"\b(in|as|into) a table\b",
    r"\btabulate\b",
    r"\b(for|of) (each|every)\b",
    r"\bper (region|month|week|day|quarter|year|team|customer|client|product|user|category|country|store|"
    r"site|vendor|project|account|plan|channel|service)\b",
    r"\bby (region|month|week|day|quarter|year|team|customer|client|product|user|category|country|store|"
    r"site|vendor|project|account|plan|channel|service)\b",
    r"\bpros and cons\b",
    r"\btrade-?offs? (between|of)\b",
    # a choice among options, or a grew/shrank question over several items
    r"\bwhich (one |ones |of (these|them|the|those) (\w+ )?)?(should|would you|do you recommend|is (the )?(best|better|worst|cheapest))\b",
    r"\bwhich \w+s? (should i|should we|to (pick|choose|keep|cut|drop|fund))\b",
    r"\bwhich \w+s (grew|shrank|rose|fell|dropped|increased|decreased|improved|declined|changed|"
    r"performed|regressed|are (over|under|above|below)|(had|have|saw|show(ed)?) (rising|falling|growing|"
    r"shrinking|the (most|least|highest|lowest)))\b",
    r"\bhelp me (decide|choose|pick)\b",
    r"\b(decide|choose|pick) (between|among|which|where)\b",
    r"\b(where|how) (should|do) (i|we) (spend|split|allocate|put) (the |our |my )?(\w+ )?(budget|money|spend|time|headcount)\b",
]
# The question is about data: figures, a dataset, or measured things.
_DATA = re.compile(
    r"\d|\b(data|dataset|csv|xlsx|spreadsheet|sheet|table|database|query|report|metrics?|numbers?|figures?|stats|"
    r"statistics|sales|revenue|costs?|prices?|pricing|spend|spending|budget|orders?|invoices?|usage|traffic|"
    r"visits|signups?|users|customers|growth|grew|scores?|results?|benchmarks?|latency|throughput|uptime|"
    r"errors?|incidents?|tickets?|plans?|options|vendors?|providers?|candidates?|regions?|months?|quarters?|"
    r"teams?|products?|hours|minutes|times|durations|rates?|counts?|sizes|days|weeks?|years?|percent|share|"
    r"totals?|averages?|margin)\b", re.I)
# A single number is a sentence in chat, not a page.
_ONE_NUMBER = re.compile(
    r"^\s*(so,? |ok,? |and |quick question:? )?(how (many|much|long|often|big)|what('?s| is| was) the "
    r"(total|count|number|sum|average|mean|median|max(imum)?|min(imum)?|size|percentage)|"
    r"what (time|year|day|date)|count (the|how)|is there|are there|does|did|is it)\b", re.I)
# Code tasks: the answer is a change to code, not a page of findings.
_CODE = re.compile(
    r"\b(fix|debug|refactor|implement|rename|compile|lint|deploy|rebase|merge conflict|stack ?trace|"
    r"traceback|exception|segfault|unit tests?|test suite|pull request|commit|regex|endpoint|function|method|"
    r"class|module|variable|api call|bug|typo|import|dependency|dependencies|code|the (script|hook|build))\b|"
    r"\b(write|add|create|make) (a|an|the) (script|function|test|class|hook|component|migration|query)\b|"
    r"\.(py|js|ts|tsx|jsx|go|rs|java|kt|cs|cpp|c|h|rb|php|dart|swift|sh|ps1|sql|ya?ml|toml|lock)\b", re.I)
_SHAPE_RX = re.compile("|".join(_SHAPE), re.I)
# The reader states a decision they must make over the data. Then the softer note's escape hatch
# ("if it comes down to one line, answer in chat") is the wrong call: a short result that someone has
# to decide on still needs a verdict control, so the firmer note applies.
_DECISION = re.compile(
    r"\bi (have|need|got|'?ve got) to (decide|choose|pick|make (a|the) (call|decision))\b|"
    r"\bhelp (me|us) (decide|choose|pick)\b|"
    r"\b(we|i) (must|need to|have to) (decide|choose|pick)\b|"
    r"\b(decide|choose|pick) (between|among|which|where|what to (fund|cut|keep|drop))\b|"
    r"\bwhere (should|do|would) (the |our |my |this |next \w+'?s? )?(\w+ )?(budget|money|spend|funding|headcount)"
    r"( \w+)? go\b|"
    r"\bwhere (should|do) (i|we) (spend|split|allocate|put|invest)\b|"
    r"\bwhich (\w+ ){0,4}(should|do|would) (we|i) (fund|cut|drop|keep|pick|choose|buy|back|prioriti[sz]e)\b|"
    r"\bwhich (\w+ ){0,4}to (fund|cut|drop|keep|pick|choose|back|prioriti[sz]e)\b", re.I)
_BREAKDOWN_OVERRIDE = re.compile(r"\b(per|by|for each|for every|broken down|breakdown|grouped by|split by)\b", re.I)


def _clean(prompt: str) -> str:
    return _QUOTED.sub(" ", _INLINE.sub(" ", _FENCE.sub(" ", prompt or "")))


def wants_page(prompt: str) -> bool:
    text = _clean(prompt)
    if len(re.findall(r"[A-Za-z0-9]+", text)) < MIN_WORDS:
        return False
    if _CODE.search(text):
        return False
    if not (_SHAPE_RX.search(text) or _DECISION.search(text)) or not _DATA.search(text):
        return False
    # "how many orders are there?" is one number; "how many orders per region?" is a breakdown.
    first = re.split(r"(?<=[.?!])\s+", text.strip(), maxsplit=1)[0]
    if _ONE_NUMBER.search(first) and not _BREAKDOWN_OVERRIDE.search(first):
        return False
    return True


def _off() -> bool:
    return os.environ.get("GNOMONKIT_PAGE_NUDGE", "").strip().lower() in ("0", "off", "false", "no")


NOTE = ("GNOMONKIT: this question asks for a breakdown, comparison, ranking or decision over several items. "
        "If the answer is more than a few lines of findings, or items someone has to decide on, answer it as "
        "a page with the core:report-pages skill (its template, the answer first, one section per item that "
        "needs a verdict), not as a chat table. If it comes down to one number or one line, answer in chat.")
DECISION_NOTE = ("GNOMONKIT: this prompt states a decision the reader has to make over this data. Answer it as a "
                 "page with the core:report-pages skill: use its template, put the recommendation first, and give "
                 "each option its own data-ask section so the reader can record a verdict on it. Do this even when "
                 "the result is short (two or three options, a few numbers): a decision needs a verdict control, "
                 "not a chat table.")


def is_decision(prompt: str) -> bool:
    return bool(_DECISION.search(_clean(prompt)))


def note(prompt: str) -> str:
    return DECISION_NOTE if is_decision(prompt) else NOTE


def main() -> int:
    try:
        if _off():
            return 0
        data = json.loads(sys.stdin.read() or "{}")
        prompt = str(data.get("prompt") or "") if isinstance(data, dict) else ""
        if not wants_page(prompt):
            return 0
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": note(prompt)}}))
    except Exception:  # noqa: BLE001 -- a hook must never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
