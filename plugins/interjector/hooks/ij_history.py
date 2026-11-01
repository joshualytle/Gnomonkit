"""History search and question detection for predict-then-confirm.

search(query): runs the user's configured history command (GNOMONKIT_HISTORY_SEARCH, a JSON list
argv; the query is appended). Returns None when nothing is configured, "" when it found nothing or
failed or timed out, otherwise its output trimmed. Any history store works: MemPalace, claude-mem,
a grep over notes. With GNOMONKIT_HISTORY_SEARCH unset, Gnomonkit core's own local history is used
when it has written its discovery file ($GNOMONKIT_HOME/history/search.json, {"argv": [...]}).
GNOMONKIT_HISTORY_SEARCH=off turns history search off, the discovery file included. Core's search
opens its index read-only (it never waits on an index run) and stops its own query after 2 s
(--budget in that argv); it is killed after CORE_TIMEOUT_S, a configured command after TIMEOUT_S.

asks_user(message): the question a reply ends by asking the user, or None. Questions inside code
blocks and quoted lines don't count; neither does a rhetorical question answered in the same reply.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

# A hook's children must never open a console window on Windows (a detached parent has none to share).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TIMEOUT_S = 10            # a configured command (GNOMONKIT_HISTORY_SEARCH) is killed after this long
CORE_TIMEOUT_S = 5        # core's own search: its argv stops the query at 2 s; this covers start-up too
MAX_CHARS = 2000
_FENCE = re.compile(r"```.*?(```|$)", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"\bhttps?://\S+")
_SENTENCE = re.compile(r"[^.!?\n]*\?")
# A question to the user addresses them. "Why did it fail?" answered in the same reply does not.
_ADDRESSED = re.compile(r"(?i)\b(you|your|should i|shall i|can i|may i|do i|want me|would you|could you|"
                        r"which|prefer|okay to|ok to)\b")
# A last paragraph that waits on the user without a question mark, or offers to redo a choice Claude
# made on its own ("If you'd rather ..., I can rename it"): a silent assumption history may settle.
_HANDOFF = re.compile(r"(?i)\b(tell me|let me know|once (i|you) have|once you|confirm|waiting (on|for) you|"
                      r"i (also )?need (you|your|to know)|unless you|if you'?d rather|if you prefer|"
                      r"if you want (a |it |something )?(different|other))\b")
MAX_QUERY = 600


def _discovered() -> list | None:
    """The argv in core's search.json, when the file is there and the script it names still is."""
    if (os.environ.get("GNOMONKIT_HISTORY") or "").strip().lower() in ("off", "0", "false", "no"):
        return None
    home = os.environ.get("GNOMONKIT_HOME") or os.path.join(os.path.expanduser("~"), ".gnomonkit")
    try:
        with open(os.path.join(home, "history", "search.json"), encoding="utf-8") as fh:
            argv = json.load(fh).get("argv")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return None
    if any(a.endswith(".py") and not os.path.isfile(a) for a in argv):
        return None                        # core moved or was removed since it wrote the file
    return argv


def search_argv() -> list | None:
    """The history command to run, or None when there is none (or it is turned off)."""
    raw = os.environ.get("GNOMONKIT_HISTORY_SEARCH", "")
    if raw.strip().lower() == "off":
        return None
    if not raw:
        return _discovered()
    try:
        argv = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return None
    return argv


def search(query: str) -> str | None:
    argv = search_argv()
    if argv is None:
        return None
    timeout = TIMEOUT_S if os.environ.get("GNOMONKIT_HISTORY_SEARCH") else CORE_TIMEOUT_S
    if argv[0].lower().endswith((".bat", ".cmd")):
        # cmd.exe re-parses a batch file's arguments, so a model-written question could run commands
        # (the BatBadBut class). Point GNOMONKIT_HISTORY_SEARCH at the interpreter and script instead.
        return None
    query = query.lstrip("-").strip()      # never let the question be parsed as an option
    try:
        r = subprocess.run(argv + [query], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout, creationflags=NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return (r.stdout or "").strip()[:MAX_CHARS]


def _questions(text: str) -> list[str]:
    return [q.strip(" *_-:\t") for q in _SENTENCE.findall(text) if q.strip(" *_-:\t") not in ("", "?")]


def asks_user(message: str) -> str | None:
    """The reply's questions to the user, joined, when the reply hands the turn back to them.

    A reply hands off when its last paragraph asks an addressed question or says it is waiting
    ("tell me", "once I have"). Real replies often list their questions earlier and end with that
    instruction, so once a hand-off is seen every question in the reply is searched, not just the last."""
    text = _URL.sub(" ", _INLINE_CODE.sub(" ", _FENCE.sub("", message or "")))
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    paragraphs = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
    if not paragraphs:
        return None
    last = paragraphs[-1]
    addressed_last = [q for q in _questions(last) if _ADDRESSED.search(q)]
    if not addressed_last and not _HANDOFF.search(last):
        return None
    asked = [q for p in paragraphs for q in _questions(p)]
    joined = " ".join(asked) if asked else re.sub(r"\s+", " ", last)
    return joined[:MAX_QUERY] or None
