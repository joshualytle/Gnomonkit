"""The lessons register: what past sessions taught, kept in one JSON file the user owns.

    register.json   {"schema": 1, "items": [item, ...]}
    item            id (int; shown as L<id>, or E<id> for an entry), text, kind ("lesson" | "entry"),
                    scope ("everywhere" | "repo:<name>" | "topic:<name>"), n (distinct sessions it came
                    from), sources (their ids), pinned, retired, by ("user" | "agent" | "import"),
                    created, updated

Where: GNOMONKIT_LESSONS_REGISTER when set; else lessons/register.json in the overlay, when there is
one (GNOMONKIT_OVERLAY, or the folder two levels above the personal profile when it holds
gnomonkit-overlay.toml or a lessons/ folder); else ~/.gnomonkit/lessons/register.json.

There is no model here. Lessons are written by the agent with the user (at session close, from
`gnomonkit lessons candidates`), by the user, or imported from a register another tool produced.
Near-duplicates are found by token shingles (words and word pairs, Jaccard similarity): it misses
paraphrases, so `n` runs low rather than high.

Matching (the lessons_inject hook) is bm25 over the register with gates, documented on match_prompt.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid

from . import _common as C
from . import _text as T

DUP_THRESHOLD = 0.5        # shingle Jaccard at or above this: "is this the same lesson?"
GROUP_THRESHOLD = 0.3      # candidates this similar are one group
TEXT_MAX = 600
ALWAYS_MAX = 8             # the always-load few at session start
MATCH_MAX = 5              # matched to one prompt
FILE_MAX = 5               # named by one file
MIN_SHARED = 2             # distinct query terms a lesson must share with the prompt
MIN_IDF_MASS = 2.0         # and the idf of those shared terms must add up to this much
WITHIN_TOP = 0.5           # and its score must be at least this share of the best score
REPO_BOOST = 1.5           # this repository's lessons score higher
MIN_PROMPT_TERMS = 3       # shorter prompts are not matched at all
IDF_PRIOR_DOCS = 20        # idf is computed as if the register held at least this many items, so a
                           # new register's few lessons can pass the idf gate on two rare shared words
_SCOPE = re.compile(r"(everywhere|repo:[A-Za-z0-9_.-]{1,100}|topic:[A-Za-z0-9_.-]{1,60})$")
_ID = re.compile(r"[LEle]?(\d{1,7})$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_ACK = re.compile(r"(?i)^\s*(ok(ay)?|k|yes|yep|yeah|no|nope|sure|thanks?( you)?|thx|ty|great|good|nice|perfect|"
                  r"done|go( ahead)?|continue|proceed|carry on|sounds good|lgtm|do it|please do|yes please|"
                  r"approved?|ship it|merge it|next)[\s.!]*$")


class RegisterError(ValueError):
    """The register file can't be read. It is never overwritten while it is in that state."""


class NearDuplicate(Exception):
    def __init__(self, matches):
        super().__init__("near-duplicate")
        self.matches = matches


# ---- where -------------------------------------------------------------------------------------
def overlay_dir() -> str | None:
    raw = (os.environ.get("GNOMONKIT_OVERLAY") or "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    try:
        path, source = C.personal_profile_path()
    except Exception:  # noqa: BLE001
        return None
    if source == "default location":
        return None
    top = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    if os.path.isfile(os.path.join(top, "gnomonkit-overlay.toml")) or os.path.isdir(os.path.join(top, "lessons")):
        return top
    return None


def register_path() -> tuple:
    """(path, where it comes from)."""
    raw = (os.environ.get("GNOMONKIT_LESSONS_REGISTER") or "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw)), "GNOMONKIT_LESSONS_REGISTER"
    ov = overlay_dir()
    if ov:
        return os.path.join(ov, "lessons", "register.json"), "overlay"
    return os.path.join(C.gnomonkit_home(), "lessons", "register.json"), "default location"


def lessons_mode(cwd: str | None = None) -> tuple:
    """(on, why): whether lessons are shown here. GNOMONKIT_LESSONS, then memory.lessons, then auto
    (off when MemPalace is set up here); see _common.memory_feature. Never raises."""
    return C.memory_feature("memory.lessons", "GNOMONKIT_LESSONS", cwd)


def lessons_off(cwd: str | None = None) -> bool:
    return not lessons_mode(cwd)[0]


# ---- the file ----------------------------------------------------------------------------------
def load(path: str | None = None, strict: bool = True) -> list:
    """The register's items. [] when there is no file. A file that can't be read raises RegisterError
    when strict (writers), and gives [] otherwise (hooks)."""
    path = path or register_path()[0]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError, UnicodeDecodeError) as e:
        if strict:
            raise RegisterError("can't read %s (%s). Fix or move it first; nothing was written." % (path, e)) from None
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        if strict:
            raise RegisterError("%s has no \"items\" list. Fix or move it first; nothing was written." % path)
        return []
    return [normalise(i) for i in items if isinstance(i, dict) and isinstance(i.get("id"), int)
            and isinstance(i.get("text"), str)]


def save(items: list, path: str | None = None) -> str:
    path = path or register_path()[0]
    if os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                C.atomic_write(path + ".bak", fh.read())
        except OSError:
            pass
    body = {"schema": 1, "note": "Gnomonkit lessons register. Change it with `gnomonkit lessons ...`.",
            "items": sorted(items, key=lambda i: i["id"])}
    C.atomic_write(path, json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    write_index(body["items"], path)
    return path


# ---- the token index (what the hook reads) -------------------------------------------------------
# Parsing and tokenising a register of tens of thousands on every prompt costs more than a hook may
# take. So the hook matches on at most SCAN_MAX live items, the ones that count most (pinned,
# entries, the most sessions, the newest), and save() writes their tokens once, as postings (term ->
# the items holding it and how often), to ~/.gnomonkit/lessons/index-<hash of the register's
# path>.json: beside the user's own state, never in the overlay. The hook reads only that file while
# the register's size and modification time match the ones it was built from. When they don't (the
# register was edited by hand, or arrived with an overlay pull), the first hook to run rebuilds it.
INDEX_VERSION = 2
SCAN_MAX = 3000            # live items the hook matches on; past this many, the ones that count least never are


def _rank(i: dict) -> tuple:
    return (not i["pinned"], i["kind"] != "entry", -i["n"], -i["id"])


def _first(items: list) -> list:
    """The SCAN_MAX of these items that count most (pinned, entries, the most sessions, then the
    newest), in their own order."""
    if len(items) <= SCAN_MAX:
        return items
    keep = {id(i) for i in sorted(items, key=_rank)[:SCAN_MAX]}
    return [i for i in items if id(i) in keep]


def _load_live(path: str) -> list:
    """The register's live items as normalise() gives them, only the SCAN_MAX that count most when
    there are more: on a large register this is much cheaper than load(), which normalises every
    item. [] for a missing or unreadable file."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    raw = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    live = [i for i in raw if isinstance(i, dict) and isinstance(i.get("id"), int)
            and isinstance(i.get("text"), str) and not i.get("retired")]
    if len(live) > SCAN_MAX:
        def rank(i):
            n = i.get("n") if isinstance(i.get("n"), int) and not isinstance(i.get("n"), bool) else 0
            s = i.get("sources")
            n = max(n, len(s) if isinstance(s, list) else 0, 1)
            return (not i.get("pinned"), i.get("kind") != "entry", -n, -i["id"])
        keep = {id(i) for i in sorted(live, key=rank)[:SCAN_MAX]}
        live = [i for i in live if id(i) in keep]
    return [normalise(i) for i in live]


def index_path(path: str) -> str:
    h = hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:12]
    return os.path.join(C.gnomonkit_home(), "lessons", "index-%s.json" % h)


def _stamp(path: str):
    try:
        s = os.stat(path)
    except OSError:
        return None
    return [s.st_size, s.st_mtime_ns]


def build_index(items: list, path: str, stamp=None) -> dict:
    """The hook's view of a register: its live items (the SCAN_MAX that count most, see _first),
    each one's token count, the postings ({term: [item, tf, item, tf, ...]}, items by position) and
    the file names items name ({name: [item, ...]}). `items` as load() gives them; `stamp` is the
    register's, taken before it was read (default: now)."""
    items = _first([i for i in items if not i["retired"]])
    post: dict = {}
    files: dict = {}
    lens = []
    for j, i in enumerate(items):
        tf: dict = {}
        toks = T.tokens(i["text"])
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        lens.append(len(toks))
        for t, c in tf.items():
            post.setdefault(t, []).extend((j, c))
        for name in _file_names(i["text"]):
            files.setdefault(name, []).append(j)
    return {"v": INDEX_VERSION, "stamp": _stamp(path) if stamp is None else stamp, "items": items, "len": lens,
            "post": post, "files": files}


def write_index(items: list, path: str) -> None:
    """Best effort: without an index the hook scans instead."""
    try:
        C.atomic_write(index_path(path), json.dumps(build_index(items, path), ensure_ascii=False,
                                                    separators=(",", ":")))
    except (OSError, TypeError, ValueError):
        pass


def load_for_hook(path: str | None = None) -> tuple:
    """(items, index) for the injection hook: the live items it matches on and their index. Read from
    the index file when that is current; otherwise built from the register and written back, so only
    the first prompt after a change pays for it. ([], None) without a register; never raises for a
    bad file."""
    path = path or register_path()[0]
    stamp = _stamp(path)
    if stamp is None:
        return [], None
    try:
        with open(index_path(path), encoding="utf-8") as fh:
            idx = json.load(fh)
        if (isinstance(idx, dict) and idx.get("v") == INDEX_VERSION and idx.get("stamp") == stamp
                and isinstance(idx.get("items"), list) and isinstance(idx.get("post"), dict)
                and isinstance(idx.get("len"), list) and len(idx["len"]) == len(idx["items"])):
            return idx["items"], idx
    except (OSError, ValueError):
        pass
    items = _load_live(path)
    if not items:
        return [], None
    idx = build_index(items, path, stamp=stamp)
    try:
        C.atomic_write(index_path(path), json.dumps(idx, ensure_ascii=False, separators=(",", ":")))
    except (OSError, TypeError, ValueError):
        pass
    return idx["items"], idx


def normalise(i: dict) -> dict:
    sources = [str(s) for s in (i.get("sources") or []) if str(s).strip()]
    sources = list(dict.fromkeys(sources))
    n = i.get("n")
    n = max(int(n) if isinstance(n, int) and not isinstance(n, bool) else 0, len(sources), 1)
    scope = i.get("scope") if isinstance(i.get("scope"), str) and _SCOPE.match(i["scope"]) else "everywhere"
    return {"id": int(i["id"]), "text": str(i["text"]), "kind": "entry" if i.get("kind") == "entry" else "lesson",
            "scope": scope, "n": n, "sources": sources, "pinned": bool(i.get("pinned")),
            "retired": bool(i.get("retired")), "by": i.get("by") if i.get("by") in ("user", "agent", "import") else "user",
            "created": str(i.get("created") or ""), "updated": str(i.get("updated") or i.get("created") or "")}


def label(i: dict) -> str:
    return ("E" if i["kind"] == "entry" else "L") + str(i["id"])


def parse_id(s: str) -> int:
    m = _ID.match(str(s or "").strip())
    if not m:
        raise ValueError("a lesson id looks like L12 (or E3 for an entry)")
    return int(m.group(1))


def find(items: list, ident) -> dict:
    n = parse_id(ident) if not isinstance(ident, int) else ident
    for i in items:
        if i["id"] == n:
            return i
    raise ValueError("no item %s in the register" % ident)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def check_text(text: str) -> str:
    """The text as stored: one paragraph, scrubbed. ValueError when it can't be."""
    from . import _scrub
    t = " ".join(str(text or "").split())
    if not t:
        raise ValueError("a lesson needs text")
    if len(t) > TEXT_MAX:
        raise ValueError("a lesson is one paragraph of up to %d characters (this is %d)" % (TEXT_MAX, len(t)))
    if _CONTROL.search(t):
        raise ValueError("a lesson can't hold control characters")
    try:
        return _scrub.scrub(t)
    except Exception as e:  # noqa: BLE001 - never store text the scrubber could not check
        raise ValueError("the scrubber could not check this text (%s); nothing was written" % e) from None


def check_scope(scope: str) -> str:
    s = (scope or "everywhere").strip()
    if not _SCOPE.match(s):
        raise ValueError("--scope is everywhere, repo:<name> or topic:<name> (letters, digits, . _ -)")
    return s


def near(items: list, text: str, threshold: float = DUP_THRESHOLD, include_retired: bool = False) -> list:
    """[(similarity, item)] at or above `threshold`, most similar first."""
    sh = T.shingles(text)
    out = []
    for i in items:
        if i["retired"] and not include_retired:
            continue
        s = T.jaccard(sh, T.shingles(i["text"]))
        if s >= threshold:
            out.append((round(s, 2), i))
    return sorted(out, key=lambda x: -x[0])


def _source_token(source: str | None) -> str:
    s = " ".join(str(source or "").split())[:120]
    return s or "manual-" + uuid.uuid4().hex[:12]


def add(items: list, text: str, scope: str = "everywhere", source: str | None = None, kind: str = "lesson",
        by: str = "user", same_as=None, new: bool = False, pinned: bool = False) -> tuple:
    """(item, "added" | "merged"). Raises NearDuplicate when a similar item exists and neither
    same_as nor new says what to do; ValueError on a bad value. Changes `items` in place."""
    clean = check_text(text)
    scope = check_scope(scope)
    src = _source_token(source)
    if same_as is not None:
        it = find(items, same_as)
        if src not in it["sources"]:           # a session already counted is not counted twice
            it["sources"].append(src)
            it["n"] = max(it["n"] + 1, len(it["sources"]))
        it["updated"] = _now()
        return it, "merged"
    if not new:
        dup = near(items, clean)
        if dup:
            raise NearDuplicate(dup)
    it = {"id": max([i["id"] for i in items] + [0]) + 1, "text": clean, "kind": "entry" if kind == "entry" else "lesson",
          "scope": scope, "n": 1, "sources": [src], "pinned": bool(pinned), "retired": False,
          "by": by if by in ("user", "agent", "import") else "user", "created": _now(), "updated": _now()}
    items.append(it)
    return it, "added"


# ---- import ------------------------------------------------------------------------------------
_TEXT_KEYS = ("text", "lesson", "rule", "content", "body")
_DEAD = ("retired", "rejected", "reject", "superseded", "deleted", "dropped")


def _read_any(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = []
        for n, line in enumerate(raw.splitlines(), 1):
            if line.strip():
                try:
                    data.append(json.loads(line))
                except ValueError:
                    raise ValueError("line %d is not JSON (a register is JSON, or JSON lines)" % n) from None
    if isinstance(data, dict):
        data = data.get("items") or data.get("lessons") or data.get("entries") or []
    if not isinstance(data, list):
        raise ValueError("expected a list of items")
    return [d for d in data if isinstance(d, dict)]


def _scope_from(v) -> str:
    s = str(v or "").strip()
    if _SCOPE.match(s):
        return s
    if s.lower() in ("", "all", "global", "everywhere", "any"):
        return "everywhere"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", s.split(":", 1)[-1]).strip("-")[:60]
    if s.lower().startswith(("repo:", "project:", "wing:")) and slug:
        return "repo:" + slug
    return ("topic:" + slug) if slug else "everywhere"


def import_items(items: list, path: str, keep_pins: bool = False) -> dict:
    """Merge a register from another tool into `items` (in place). Each incoming item is scrubbed and
    checked for a near-duplicate: a match gains its sources (n goes up), anything else is added with
    by = "import". Items marked retired, rejected or superseded are skipped. A pinned item would be
    put in front of every session, so it comes in unpinned (counted in "unpinned") unless keep_pins:
    the user reviews it and pins it with `lessons pin`."""
    counts = {"added": 0, "merged": 0, "skipped": 0, "unpinned": 0}
    for d in _read_any(path):
        text = next((d[k] for k in _TEXT_KEYS if isinstance(d.get(k), str) and d[k].strip()), None)
        state = str(d.get("status") or d.get("verdict") or "").lower()
        if text is None or d.get("retired") is True or state in _DEAD:
            counts["skipped"] += 1
            continue
        try:
            clean = check_text(text[:TEXT_MAX])
        except ValueError:
            counts["skipped"] += 1
            continue
        srcs = d.get("sources") or d.get("source_sessions") or d.get("sessions") or []
        srcs = [str(s) for s in srcs] if isinstance(srcs, list) else []
        n = next((d[k] for k in ("n", "count", "times") if isinstance(d.get(k), int) and not isinstance(d.get(k), bool)), 0)
        dup = near(items, clean)
        if dup:
            it = dup[0][1]
            for s in srcs:
                if s not in it["sources"]:
                    it["sources"].append(s)
            it["n"] = max(it["n"], len(it["sources"]), n)
            it["updated"] = _now()
            counts["merged"] += 1
            continue
        kind = "entry" if str(d.get("kind") or "").lower() in ("entry", "standing", "rule") else "lesson"
        pinned = d.get("pinned") is True
        if pinned and not keep_pins:
            counts["unpinned"] += 1
        it = {"id": max([i["id"] for i in items] + [0]) + 1, "text": clean, "kind": kind,
              "scope": _scope_from(d.get("scope")), "n": max(n, len(srcs), 1), "sources": list(dict.fromkeys(srcs)),
              "pinned": pinned and keep_pins, "retired": False, "by": "import", "created": _now(), "updated": _now()}
        items.append(it)
        counts["added"] += 1
    return counts


# ---- matching (the injection hook) -------------------------------------------------------------
def applies(i: dict, repo: str) -> bool:
    """Not retired, and not scoped to another repository."""
    if i["retired"]:
        return False
    if i["scope"].startswith("repo:"):
        return bool(repo) and i["scope"][5:].casefold() == repo.casefold()
    return True


def here(i: dict, repo: str) -> bool:
    return i["scope"].startswith("repo:") and bool(repo) and i["scope"][5:].casefold() == repo.casefold()


def tag(i: dict, repo: str) -> str:
    where = "this repo" if here(i, repo) else ("everywhere" if i["scope"] == "everywhere" else i["scope"])
    if i["kind"] == "entry":
        return "[%s · entry · %s]" % (label(i), where)
    return "[%s · %d session%s · %s]" % (label(i), i["n"], "" if i["n"] == 1 else "s", where)


def always_load(items: list, repo: str) -> list:
    """Pinned items (entries first), then lessons scoped everywhere seen in two or more sessions, by n."""
    live = [i for i in items if applies(i, repo)]
    pinned = sorted((i for i in live if i["pinned"]), key=lambda i: (i["kind"] != "entry", -i["n"], i["id"]))
    common = sorted((i for i in live if not i["pinned"] and i["scope"] == "everywhere" and i["n"] >= 2),
                    key=lambda i: (i["kind"] != "entry", -i["n"], i["id"]))
    return (pinned + common)[:ALWAYS_MAX]


def skip_prompt(prompt: str) -> bool:
    p = (prompt or "").strip()
    if not p or p.startswith("/") or _ACK.match(p):
        return True
    return len(set(T.tokens(p))) < MIN_PROMPT_TERMS


def match_prompt(items: list, prompt: str, repo: str, shown=(), index: dict | None = None) -> list:
    """Lessons matched to a prompt by bm25 over the register, kept only when all gates pass: at least
    MIN_SHARED distinct shared terms, their idf adds up to MIN_IDF_MASS, the score is within WITHIN_TOP
    of the best. Other repositories' lessons are dropped, this one's boosted; ids in `shown` (already
    shown this session) are skipped. [(score, item)], best first.

    With `index` (build_index of these items) only the postings of the prompt's own terms are read;
    without one, each item is tokenised here, the first SCAN_MAX of a large register only."""
    if skip_prompt(prompt):
        return []
    q = set(T.tokens(prompt))
    hits, df, lens = [], dict.fromkeys(q, 0), []      # hits: (item, {shared term: tf}, length)
    if index is not None:
        its, post = index["items"], index["post"]
        ok = [applies(i, repo) for i in its]
        lens = [n for n, k in zip(index["len"], ok) if k]
        by_doc: dict = {}
        for t in q:
            p = post.get(t) or ()
            for k in range(0, len(p) - 1, 2):
                j = p[k]
                if ok[j]:
                    df[t] += 1
                    by_doc.setdefault(j, {})[t] = p[k + 1]
        hits = [(its[j], tfs, index["len"][j]) for j, tfs in by_doc.items()]
    else:
        for i in _first([i for i in items if applies(i, repo)]):
            d = T.tokens(i["text"])
            lens.append(len(d))
            tfs: dict = {}
            for t in d:
                if t in q:
                    tfs[t] = tfs.get(t, 0) + 1
            for t in tfs:
                df[t] += 1
            if tfs:
                hits.append((i, tfs, len(d)))
    if not lens:
        return []
    N = max(len(lens), IDF_PRIOR_DOCS)
    idf = {t: math.log((N - c + 0.5) / (c + 0.5) + 1.0) for t, c in df.items()}
    avg = sum(lens) / len(lens) or 1.0
    scored = []
    for i, tfs, dl in hits:
        if len(tfs) < MIN_SHARED or sum(idf[t] for t in tfs) < MIN_IDF_MASS:
            continue
        s = 0.0
        for t, tf in tfs.items():
            s += idf[t] * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * dl / avg))
        if here(i, repo):
            s *= REPO_BOOST
        if i["kind"] == "entry":
            s *= 1.2                           # hand-written entries outrank lessons at the same match
        scored.append((s, i))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    keep = [(s, i) for s, i in scored if s >= WITHIN_TOP * top and i["id"] not in set(shown)]
    return sorted(keep, key=lambda x: (-x[0], x[1]["id"]))[:MATCH_MAX]


_FILE_TOKEN = re.compile(r"[A-Za-z0-9_.\-/\\]+")


def _file_names(text: str) -> set:
    """The file names (with an extension) a lesson names, case-folded: session_profile.py, or the
    last part of a longer path."""
    out = set()
    if "." not in text:
        return out
    for tok in _FILE_TOKEN.findall(text):
        if "." not in tok:
            continue                        # no extension: not a file name (the common case, so first)
        base = tok.rstrip(".,;:)").replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if "." in base.strip(".") and len(base) >= 4:
            out.add(base)
    return out


def match_file(items: list, path: str, repo: str, index: dict | None = None) -> list:
    """Lessons that name this file: its name with an extension (session_profile.py), or a longer
    path ending in it, as a whole token of the lesson text."""
    base = os.path.basename(str(path or "").replace("\\", "/").rstrip("/"))
    if "." not in base.strip(".") or len(base) < 4:
        return []
    want = base.casefold()
    if index is not None and isinstance(index.get("files"), dict):
        pool = [index["items"][j] for j in index["files"].get(want) or ()]
        out = [i for i in pool if applies(i, repo)]
    else:
        out = [i for i in _first([i for i in items if applies(i, repo)]) if want in _file_names(i["text"])]
    return sorted(out, key=lambda i: (here(i, repo) is False, i["kind"] != "entry", -i["n"]))[:FILE_MAX]


# ---- candidates --------------------------------------------------------------------------------
def candidates(rows: list, items: list, is_correction, limit: int = 10) -> list:
    """Groups of user messages that read as corrections. `rows` are dicts with text, session, project
    and ts (newest first). Each group: sessions (distinct), count, examples (verbatim, as stored:
    already scrubbed), projects, last, and `like` (a register item it resembles, if any)."""
    groups: list = []
    for r in rows:
        text = r.get("text") or ""
        if not is_correction(text):
            continue
        sh = T.shingles(text)
        if not sh:
            continue
        best, best_s = None, 0.0
        for g in groups:
            s = T.jaccard(sh, g["_sh"])
            if s > best_s:
                best, best_s = g, s
        if best is None or best_s < GROUP_THRESHOLD:
            best = {"_sh": sh, "sessions": set(), "count": 0, "examples": [], "projects": set(), "last": ""}
            groups.append(best)
        best["count"] += 1
        best["sessions"].add(r.get("session") or "")
        if r.get("project"):
            best["projects"].add(r["project"])
        best["last"] = max(best["last"], r.get("ts") or "")
        if len(best["examples"]) < 3 and text not in best["examples"]:
            best["examples"].append(text if len(text) <= 400 else text[:400].rstrip() + " ...")
    out = []
    for g in groups:
        like = near(items, g["examples"][0], threshold=GROUP_THRESHOLD)
        out.append({"sessions": len(g["sessions"]), "session_ids": sorted(s for s in g["sessions"] if s)[:5],
                    "count": g["count"], "examples": g["examples"],
                    "projects": sorted(g["projects"]), "last": g["last"],
                    "like": label(like[0][1]) if like else None})
    out.sort(key=lambda g: g["last"], reverse=True)            # newest first among equals...
    out.sort(key=lambda g: (-g["sessions"], -g["count"]))     # ...most sessions first overall
    return out[:limit]
