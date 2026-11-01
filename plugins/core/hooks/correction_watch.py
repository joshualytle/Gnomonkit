#!/usr/bin/env python3
"""UserPromptSubmit: notice when the user is correcting the agent, and say what to do about it.

A correction the user has to repeat is a trigger that should have fired on its own. When a prompt
reads like one ("you didn't ...", "I had to tell you again", "make sure you ..."), this adds a short
note pointing at the learned-triggers skill and the user's private corrections repo. The skill does
the judging: what was missed, and whether an issue for it already exists.

Never blocks, never echoes the prompt back, never calls the network. Any failure exits 0 silently.
"""
from __future__ import annotations

import json
import os
import re
import sys

_FENCE = re.compile(r"```.*?(```|$)", re.S)
_QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
# Text the user pasted rather than typed: inline code, > quoted lines, and indented lines (a log, a
# tool listing, a brief written by another agent). A correction in there is someone else's words.
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_PASTED_LINE = re.compile(r"^(?:[ \t]*>|\t|[ ]{2,}).*$", re.M)

# Each pattern is phrased the way people actually correct an assistant. Fixtures in
# test_correction_watch.py hold synthetic corrections (must fire) and requests (must stay quiet).
#
# Before tuning, label a sample of your own typed prompts (detector-calibration) and check both
# directions: corrections the list misses and requests it fires on. The lookbehinds and lookaheads
# below exist because requests and reassurances share words with corrections.
_PATTERNS = [
    # "can you keep going" is a request, not "you keep doing this"
    r"(?<!can )(?<!could )(?<!will )(?<!to )(?<!ensure )(?<!please )(?<!if )"
    r"\byou (also |still |even )?(didn'?t|did not|never|forgot|keep|kept|missed|ignored|still haven'?t)\b"
    # "you didn't need to change it" is reassurance, not a correction
    r"(?! (?:need|have|had) to\b)",
    # added: "you misunderstood", "no you got it wrong", "that's not the problem", "that's why I said"
    r"\b(you )?(mis ?understood|got it wrong|got that wrong)\b",
    r"\b(that'?s|thats|this is) not the (\w+ )?(problem|issue|point)\b",
    r"\bthat'?s why i (said|asked|told you)\b",
    r"\bi told you (i'?m|that|it'?s|we|my|the|to)\b",
    r"\bwhy (did|do|would) you\b",
    # added: "why are you using X", "why the hell would you", "not sure why you're", "don't get why you".
    # Only about YOU: "why is this still slow" is a question about a system, not a correction.
    r"\bwhy (?:the \w+ )?(are|were|would|can'?t|didn'?t|won'?t|aren'?t|haven'?t) you\b",
    r"\b(not sure|don'?t (get|understand|know)) why (you|your|you'?re)\b",
    r"\b(yet again|once again|again and again)\b",
    r"\b(already|again)\b[^.?!\n]{0,30}\b(told|said|asked|explained|mentioned)\b",
    # "I told you before"; "it said it was already done" has no I/you/we and is not a correction
    r"\b(i|i'?ve|ive|we|you)\s+(have\s+)?(told|said|asked|explained|prompt(ed)?|remind(ed)?)\b[^.?!\n]{0,30}"
    r"\b(already|again|before|many times)\b",
    # added: "like I said", "as I've said", "earlier I said", "I said X but I meant Y", "at no point did I say"
    r"\b(like|as|earlier|before) (i|i'?ve|ive) (said|told you|mentioned|explained)\b",
    r"\bi said\b[^.?!\n]{0,40}\bbut i meant\b",
    r"\bat no point did i\b",
    # "you should have documented it" -- not "you should have a section for this"
    r"\byou (could|should) have (already |just |at least )?(been|done|known|seen|told|asked|checked|said|run|"
    r"written|made|kept|left|used|caught|found|gone|looked|searched|queried|read|\w+ed)\b",
    # "make sure you give me the link" is a task instruction. It corrects only when it sets a standing
    # rule or points back at a miss: always/never/actually, or next time, before, first, this time.
    r"\bmake sure (?:you|to) (?:always|never|don'?t|do not|actually|stop)\b",
    r"\bmake sure (?:you|to)\b[^.?!\n]{0,60}\b(?:next time|from now on|this time|every time|before (?:you|asking|"
    r"saying|telling|claiming|pushing|merging)|first)\b",
    # added: "when I ask for X, that means do X" is a standing rule said after a miss
    r"\bwhen i (?:ask|tell|say)\b[^.?!\n]{0,60}\b(?:that|it) means\b",
    r"\b(from now on|going forward|every time i ask)\b",
    # added: standing rules said as corrections
    r"\bin the future\b[^.?!\n]{0,40}\b(do not|don'?t|never|always|tell me|ask me)\b",
    r"\b(do not|don'?t|never)\b[^.?!\n]{0,60}\bin the future\b",
    r"\byou (should|need to|have to|must) (always|never)\b",
    r"\byou always need to\b",
    r"\bstop (doing|asking|using|saying|making|adding)\b",
    # added: "stop guessing", "stop it"
    r"\bstop (\w+ )?(guessing|assuming|ignoring|repeating|waiting|talking|mentioning|referencing|bringing|"
    r"suggesting|worrying|dumping|touching|overthinking|overcomplicating)\b",
    r"\bstop it\b(?!['’]s)",
    r"\b(that'?s|this is) not what i (asked|meant|said|wanted)\b",
    r"\bnot what i (asked|meant|said|wanted)\b",
    # added: not listening, contradictions, work already done
    r"\b(you'?re|you are|you aren'?t|you'?re not|you are not) (not )?(listening|tracking|paying attention|reading)\b",
    r"\b(not|aren'?t|isn'?t) (listening|tracking what i)\b",
    # At the start of a sentence: "find something that's incorrect" is not the user contradicting you.
    r"(?:^|[.!?]\s+|\bno,?\s+)(that'?s|this is|it'?s|what you said is) (entirely |just |simply )?"
    r"(not true|untrue|wrong|incorrect|inaccurate)\b",
    r"\b(do|did) what i (ask|asked|said)\b",
    r"\btrying to (explain|tell) (to )?you\b",
    r"\bi already (did|set|told|gave|sent|fixed|added|said)\b",
    r"\bdid you not\b",
]
_RX = re.compile("|".join(_PATTERNS), re.I)


def is_correction(prompt: str) -> bool:
    text = _FENCE.sub(" ", prompt or "")
    text = _QUOTED.sub(" ", _INLINE_CODE.sub(" ", _PASTED_LINE.sub(" ", text)))
    return bool(_RX.search(text))


def _profile(key: str):
    """The personal profile's value for `key` (checked against its schema), or None. Never raises."""
    try:
        core = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if core not in sys.path:
            sys.path.append(core)
        from gnomonkit_cmds import _common
        return _common.checked_profile_value(key)
    except Exception:  # noqa: BLE001 - the profile is optional
        return None


def _repo() -> str:
    """The core plugin's corrections_repo option, else the personal profile's corrections.repo."""
    return (os.environ.get("CLAUDE_PLUGIN_OPTION_CORRECTIONS_REPO")
            or os.environ.get("GNOMONKIT_CORRECTIONS_REPO") or _profile("corrections.repo") or "").strip()


def note(repo: str) -> str:
    head = ("GNOMONKIT: the user's last message reads like a correction. Address it first. Then, if it "
            "corrects something you should have done without being told, use the learned-triggers skill: ")
    if repo:
        return head + (f"record it against {repo} (comment on the matching open issue, or open one). "
                       "If it was a one-off preference rather than a missed trigger, record nothing.")
    return head + ("no corrections repo is set, so say once, briefly, that setting one (a PRIVATE repo, as "
                   "the profile's corrections.repo or the core plugin's corrections_repo option) would track "
                   "repeats like this one.")


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        if not isinstance(data, dict) or not is_correction(str(data.get("prompt") or "")):
            return 0
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": note(_repo())}}))
    except Exception:  # noqa: BLE001 -- a hook must never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
