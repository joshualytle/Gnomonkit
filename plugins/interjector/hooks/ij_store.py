"""Interjector store: per-session events, an inbox of notes, and per-turn state, as local files.

Layout under $GNOMONKIT_HOME/interjector/<session_id>/ (GNOMONKIT_HOME defaults to ~/.gnomonkit):
  events.jsonl     one compact line per hook event (never tool output or file contents)
  inbox/<id>.json  pending notes; a hook claims one by renaming it, so two hooks firing at once
                   cannot both deliver it
  delivered.jsonl  every note delivered, with when
  guesses.jsonl    predict-then-confirm searches, for measuring the hit rate later
  state.json       per-turn flags and the watcher comment budget
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

TARGET_MAX = 300
BUDGET_COMMENTS = 3
BUDGET_WINDOW_S = 600
LABELS = {"user": "INTERJECTOR (from the user, sent while you work): ",
          "watcher": "INTERJECTOR (from the watcher, not the user): ",
          "orchestrator": "INTERJECTOR (from the orchestrator, automatic, not the user): "}


def home() -> Path:
    base = os.environ.get("GNOMONKIT_HOME") or os.path.join(os.path.expanduser("~"), ".gnomonkit")
    return Path(base) / "interjector"


def _sid(sid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(sid or "")) or "unknown"


def session_dir(sid: str) -> Path:
    d = home() / _sid(sid)
    (d / "inbox").mkdir(parents=True, exist_ok=True)
    return d


def _append(path: Path, obj: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _atomic_write(path: Path, obj: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:            # Windows: another process has the target open
            if attempt == 5:
                os.unlink(tmp)
                raise
            time.sleep(0.05 * (attempt + 1))


# Secrets that commonly ride in commands. Redacted before anything is stored.
_SECRETS = [
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic|token)\s+)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[redacted]"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:token|secret|password|passwd|pwd|api[_-]?key|access[_-]?key)[A-Z0-9_]*\s*[=:]\s*)"
                r"['\"]?[^\s'\"&]+"), r"\1[redacted]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[redacted]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[redacted]"),
]


def redact(text: str) -> str:
    for rx, repl in _SECRETS:
        text = rx.sub(repl, text)
    return text


def sanitize(text: str) -> str:
    """Text from history, notes or commands must never read as a real Interjector label."""
    return re.sub(r"(?i)INTERJECTOR\s*\(", "INTERJECTOR[quoted] (", str(text))


def append_event(sid: str, ev: dict) -> None:
    ev = dict(ev)
    if isinstance(ev.get("target"), str):
        ev["target"] = redact(ev["target"])[:TARGET_MAX]
    ev.setdefault("t", time.time())
    _append(session_dir(sid) / "events.jsonl", ev)


def write_note(sid: str, kind: str, text: str, source: str, match: str | None = None) -> str:
    nid = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    note = {"id": nid, "kind": kind, "text": text, "source": source, "match": match, "created": time.time()}
    _atomic_write(session_dir(sid) / "inbox" / f"{nid}.json", note)
    return nid


def pending(sid: str, kinds: tuple[str, ...]) -> list[dict]:
    out = []
    for p in sorted((session_dir(sid) / "inbox").glob("*.json")):
        try:
            note = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if note.get("kind") in kinds:
            note["_path"] = str(p)
            out.append(note)
    return out


def claim(note: dict) -> bool:
    """Take ownership of one pending note. False if another hook got it first.

    Exclusive creation of a claim marker is the ownership test: it is atomic on Windows and POSIX.
    (Renaming the note was not: on Windows two threads renaming the same file both succeeded.)"""
    src = Path(note["_path"])
    try:
        with open(src.with_suffix(".claim"), "x", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        os.replace(src, src.with_suffix(".taken"))
    except OSError:
        pass
    return True


def take_notes(sid: str, kinds: tuple[str, ...]) -> list[dict]:
    taken = []
    for note in pending(sid, kinds):
        if claim(note):
            note.pop("_path", None)
            _append(session_dir(sid) / "delivered.jsonl", {**note, "delivered": time.time()})
            taken.append(note)
    return taken


def load_state(sid: str) -> dict:
    try:
        return json.loads((session_dir(sid) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(sid: str, st: dict) -> None:
    _atomic_write(session_dir(sid) / "state.json", st)


def log_guess(sid: str, entry: dict) -> None:
    _append(session_dir(sid) / "guesses.jsonl", {**entry, "t": time.time()})


def comment_budget_ok(sid: str, now: float | None = None) -> bool:
    """Watcher comments: at most BUDGET_COMMENTS per BUDGET_WINDOW_S per session. Consumes on success.
    Kept in its own file so the watcher never rewrites state the hooks own."""
    now = time.time() if now is None else now
    path = session_dir(sid) / "budget.json"
    try:
        times = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        times = []
    recent = [t for t in times if now - t < BUDGET_WINDOW_S]
    if len(recent) >= BUDGET_COMMENTS:
        return False
    _atomic_write(path, recent + [now])
    return True


# Per-turn flags are marker files created exclusively: atomic across processes, and no
# read-modify-write of a shared file that a concurrent writer could clobber.
def _turn_marker(sid: str, prompt_id: str, kind: str) -> Path:
    d = session_dir(sid) / "turns"
    d.mkdir(exist_ok=True)
    return d / f"{_sid(prompt_id)}.{kind}"


def mark_turn(sid: str, prompt_id: str, kind: str) -> bool:
    """True if this call set the flag, False if it was already set."""
    try:
        with open(_turn_marker(sid, prompt_id, kind), "x", encoding="utf-8"):
            return True
    except FileExistsError:
        return False


def has_turn(sid: str, prompt_id: str, kind: str) -> bool:
    return _turn_marker(sid, prompt_id, kind).exists()


def unmark_turn(sid: str, prompt_id: str, kind: str) -> None:
    try:
        _turn_marker(sid, prompt_id, kind).unlink()
    except OSError:
        pass


TASK_MAX = 300


def save_task(sid: str, prompt_id: str, prompt: str) -> None:
    """The turn's prompt, redacted and capped, so a vague question ("what should it be called?") can be
    searched with its subject. Lives with the turn markers and is pruned with the session."""
    if prompt_id and prompt:
        _turn_marker(sid, prompt_id, "task").write_text(redact(prompt)[:TASK_MAX], encoding="utf-8")


def turn_task(sid: str, prompt_id: str) -> str:
    try:
        return _turn_marker(sid, prompt_id, "task").read_text(encoding="utf-8")
    except OSError:
        return ""


def label(note: dict) -> str:
    return LABELS.get(note.get("source"), LABELS["watcher"]) + sanitize(note.get("text", ""))
