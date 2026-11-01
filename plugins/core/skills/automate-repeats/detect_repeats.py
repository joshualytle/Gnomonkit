#!/usr/bin/env python3
"""Find work done by hand more than once: candidates for a script or a skill.

    python detect_repeats.py                      # last 30 days, top 10 of each kind
    python detect_repeats.py --since 90 --top 20 --json out.json
    python detect_repeats.py --project billing    # transcript folders whose name contains this

READ-ONLY, local files only, standard library only. It reads Claude Code transcripts under
$CLAUDE_CONFIG_DIR/projects (default ~/.claude/projects): <project>/<session>.jsonl and, unless
--main-only, the subagent transcripts <project>/<session>/subagents/**/agent-*.jsonl. Nothing is
written except --json, and nothing goes over the network.

THREE KINDS OF CANDIDATE, each counted in distinct sessions (a pattern repeated inside one session is
a loop, not a recurring process):

  sequences  n-grams (--min-n..--max-n, default 3..5) of consecutive shell steps. Each Bash or
             PowerShell call is split on && ; | || and newlines; each step is reduced to its shape:
             the program (basename, no extension) plus up to two plain words after it (`git commit`,
             `gh pr create`, `python -m pytest`, `python leakcheck.py`), with quoted strings, flags,
             numbers and paths dropped (a path keeps only its file name). Steps that only look
             around (cd, ls, cat, echo, git status/diff/log, ...) are left out unless
             --keep-inspection, and an n-gram made of one repeated step is skipped. MCP tool calls
             join the sequence under their tool name. An n-gram inside a longer one seen in nearly as
             many sessions is folded into the longer one.
  commands   one shell call whose normalised text recurs (same shape, same arguments once quoted
             text, numbers and paths are masked) and is long enough to be worth naming.
  requests   typed user prompts with the same shape across sessions: the first content words of
             the prompt (stop words, code, URLs, paths and numbers removed), grouped when their word
             sets overlap by --similarity (Jaccard, default 0.5). Main sessions only.

TOKENS are ESTIMATED: characters / 4. For a sequence or command, the tool inputs plus the tool
results of the calls in each occurrence. For a request, everything the turn produced until the next
typed prompt: assistant text, tool inputs and tool results. Counts are MEASURED.

The ranking is the estimated tokens spent across every occurrence, among patterns seen in at least
--min-sessions sessions (the gate keeps one session's loop from topping the list). Treat the output
as leads: check that a script for it does not already exist before building one.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

LINE_CAP = 2_000_000
CHARS_PER_TOKEN = 4
SHELLS = {"Bash", "PowerShell"}
MIN_COMMAND_CHARS = 40          # a recurring one-liner shorter than this is not worth naming
PROMPT_WORDS = 8                 # content words kept per prompt shape
EXAMPLE_MAX = 160

# Steps that only look around. A process is what acts; these are left out of sequences by default.
INSPECT_PROGRAMS = {
    "cd", "pushd", "popd", "ls", "dir", "pwd", "cat", "type", "head", "tail", "less", "more", "echo",
    "printf", "true", "false", "sleep", "wc", "sort", "uniq", "grep", "rg", "findstr", "find", "which",
    "where", "tree", "set-location", "get-location", "get-childitem", "get-content", "write-host",
    "write-output", "select-object", "select-string", "out-null", "out-string", "format-table",
    "format-list", "measure-object", "where-object", "foreach-object", "sort-object", "start-sleep",
    "test-path", "jq", "sed", "awk", "cut", "tr", "xargs", "tee", "date", "clear", "export", "set",
}
# Shell grammar, not programs: `for f in *.py; do black $f; done` is one step, `black`.
SHELL_KEYWORDS = {"for", "while", "until", "if", "fi", "done", "esac", "case", "function", "in", "}", ")",
                  "foreach", "elseif", "return", "break", "continue", "try", "catch", "finally", "param"}
LEAD_WORDS = {"do", "then", "else", "elif", "sudo", "time", "nohup", "exec", "&", "command", "builtin",
              "{", "(", "!"}
WRAPPERS = {"timeout", "nice", "env", "stdbuf", "caffeinate"}     # run another command; the step is that one
# Prompts that only keep a session moving ("keep going", "what's next") are not a request shape.
CONTINUE_WORDS = set("""keep going continue continuing next anything else pick pickup left off carry proceed
resume finish rest remaining work working stuff things thing done good great nice cool sounds ahead
right fine perfect""".split())
INSPECT_GIT = {"status", "diff", "log", "show", "branch", "rev-parse", "remote", "config", "ls-files",
               "describe", "blame", "reflog"}

STOP = set("""a an the and or but of to in on at for with from by into onto about as is are was were be
been being it its this that these those there here i me my we us our you your he she they them their
please pls can could would will shall should may might must do does did done let lets just now then also
so very really quite some any all hey hi hello ok okay thanks thank yes no not want need like try make
sure what which who how why when where if whether up out over again same one more get go see look
still use using via""".split())

# Everything below runs on transcript text, so each pattern is linear in its input: a double-quoted
# string opens only at a quote with no backslash before it, steps split on the bare separators and are
# stripped afterwards, and heredoc, here-string and tag bodies are found with str.find (see below).
_QUOTED = re.compile(r"""((?<!\\)"(?:[^"\\]|\\.)*"|'[^']*')""")
_SPLIT = re.compile(r"&&|\|\||;|\||\n")
COMMAND_SCAN_MAX = 200_000       # characters of one shell call that are read
PROMPT_SCAN_MAX = 200_000        # characters of one prompt that are read
_PATHLIKE = re.compile(r"[\\/]|^[A-Za-z]:|^~")
_WORD = re.compile(r"^[a-z][a-z0-9_.+-]*$")
_NUM = re.compile(r"\b\d+(?:\.\d+)*\b")
_HEX = re.compile(r"\b[0-9a-f]{7,}\b")
_PATHTOK = re.compile(r"""(?:[A-Za-z]:)?(?:[\\/~][^\s"'|;&<>]*)+""")
_URL = re.compile(r"\bhttps?://\S+")
_SECRETISH = re.compile(r"(?i)\b(bearer\s+\S+|(?=[A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD))[A-Z0-9_]+=\S+|"
                        r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-\S+)")
# A heredoc or here-string body is data for one step, not more steps. The opening is a regex; the end is
# looked up (heredoc) or found with str.find (here-string), never searched for by a lazy regex.
_HEREDOC_OPEN = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1")
_HERESTRING_OPEN = re.compile(r"@['\"]")
_WORD_LINE = re.compile(r"\w+")
_TAG_OPEN = re.compile(r"<[^>]{1,40}>")
_TAG_CLOSE = re.compile(r"</[^>]{1,40}>")
_ENV_ASSIGN =re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")


def est(chars: float) -> int:
    return int(round(chars / CHARS_PER_TOKEN))


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------

def _program(tok: str) -> str:
    base = re.split(r"[\\/]", tok.strip("&.\"'"))[-1].lower()
    return re.sub(r"\.(exe|cmd|bat|ps1|sh)$", "", base)


def step_shape(segment: str, keep_inspection: bool = False) -> str | None:
    """`git commit -m "x" --amend` -> `git commit`. None for an empty or (by default) look-around step."""
    # A quoted path is still the script being run ("C:/x/run.py" -> run.py); other quoted text goes.
    s = _QUOTED.sub(lambda m: (" " + m.group(0)[1:-1] + " ") if _PATHLIKE.search(m.group(0)[1:-1])
                    and not re.search(r"\s", m.group(0)[1:-1].strip()) else " ", segment.strip())
    s = _ENV_ASSIGN.sub("", s)
    toks = s.split()
    while toks and (toks[0] in LEAD_WORDS or _program(toks[0]) in WRAPPERS):
        wrapper = _program(toks[0]) in WRAPPERS
        toks = toks[1:]
        while wrapper and toks and (toks[0].startswith("-") or _NUM.fullmatch(toks[0].rstrip("smhd"))):
            toks = toks[1:]                           # timeout 60, nice -n 5
    if toks and _program(toks[0]).startswith("python") and len(toks) > 1 and (toks[1] in ("-c", "-") or toks[1].startswith("<<")):
        return f"{_program(toks[0])} {'-c' if toks[1] == '-c' else 'stdin'}"
    if not toks or toks[0] in SHELL_KEYWORDS:
        return None
    prog = _program(toks[0])
    if not prog or not re.match(r"^[a-z0-9_.+-]+$", prog):
        return None
    words: list[str] = []
    rest = toks[1:]
    i = 0
    while i < len(rest) and len(words) < 2:
        t = rest[i]
        i += 1
        if prog.startswith("python") and t == "-m" and i < len(rest):
            words += ["-m", rest[i].lower()]
            i += 1
            continue
        if t.startswith("-"):
            continue
        if _PATHLIKE.search(t):
            name = re.split(r"[\\/]", t.rstrip("\\/"))[-1].lower()
            if re.match(r"^[a-z0-9_.-]+\.[a-z0-9]{1,5}$", name):          # a script or file name: keep
                words.append(name)
            continue
        tl = t.lower()
        if _WORD.match(tl) and not _NUM.fullmatch(tl):
            words.append(tl)
        else:
            break
    if not keep_inspection:
        if prog in INSPECT_PROGRAMS:
            return None
        if prog == "git" and (not words or words[0] in INSPECT_GIT):
            return None
    return " ".join([prog, *words[:3]])


def drop_heredocs(s: str) -> str:
    """`cat <<EOF x` + body + `EOF` -> `cat <<EOF x`: the body and its end line go, the rest stays.
    The end line is the first later line that is the delimiter alone; a heredoc with none is left as it is.
    Lines that are one word are indexed once, so each heredoc costs a lookup, not a scan."""
    if "<<" not in s:
        return s
    ends: dict = {}                       # word -> start offsets of the lines that are that word alone
    start = 0
    for line in s.split("\n"):
        w = line.strip()
        if w and len(w) <= 64 and _WORD_LINE.fullmatch(w):
            ends.setdefault(w, []).append(start)
        start += len(line) + 1
    out, pos = [], 0
    for m in _HEREDOC_OPEN.finditer(s):
        if m.start() < pos:
            continue                      # inside a body already dropped
        nl = s.find("\n", m.end())
        offs = ends.get(m.group(2), [])
        k = bisect.bisect_right(offs, nl) if nl >= 0 else len(offs)
        if k == len(offs):
            continue                      # no end line: not a heredoc this function removes
        stop = s.find("\n", offs[k])
        out.append(s[pos:m.start()] + "<<" + m.group(2) + s[m.end():nl])
        pos = len(s) if stop < 0 else stop
    out.append(s[pos:])
    return "".join(out)


def drop_herestrings(s: str) -> str:
    """PowerShell @'...'@ and @"..."@ (closing at the start of a line) -> <s>."""
    out, pos, missing = [], 0, set()
    m = _HERESTRING_OPEN.search(s)
    while m:
        q = m.group(0)[1]
        j = -1 if q in missing else s.find("\n" + q + "@", m.end())
        if j < 0:
            missing.add(q)                # no close of this kind after here means none after later ones
            m = _HERESTRING_OPEN.search(s, m.start() + 1)
            continue
        out.append(s[pos:m.start()] + " <s> ")
        pos = j + 3
        m = _HERESTRING_OPEN.search(s, pos)
    out.append(s[pos:])
    return "".join(out)


def drop_tagged(t: str) -> str:
    """<tag>...</tag> blocks (as Claude Code wraps pasted or injected text) -> a space."""
    out, pos = [], 0
    m = _TAG_OPEN.search(t)
    while m:
        c = _TAG_CLOSE.search(t, m.end())
        if not c:
            break                         # no close after this opening tag means none after any later one
        out.append(t[pos:m.start()] + " ")
        pos = c.end()
        m = _TAG_OPEN.search(t, pos)
    out.append(t[pos:])
    return "".join(out)


def split_steps(cmd: str) -> list[str]:
    """One shell call -> its steps. Heredoc bodies go; separators inside quotes do not split."""
    s = drop_herestrings(drop_heredocs((cmd or "")[:COMMAND_SCAN_MAX]))
    s = _QUOTED.sub(lambda m: re.sub(r"[|;&\n]", " ", m.group(0)), s)
    return [x.strip() for x in _SPLIT.split(s)]


def command_text(cmd: str) -> str:
    """A shell call with what varies masked: secrets, quoted strings, URLs, paths, hashes, numbers."""
    s = _SECRETISH.sub("<secret>", cmd[:COMMAND_SCAN_MAX].strip())
    s = _QUOTED.sub("<s>", s)
    s = _URL.sub("<url>", s)
    s = _PATHTOK.sub("<path>", s)
    s = _HEX.sub("<hex>", s)
    s = _NUM.sub("<n>", s)
    return re.sub(r"\s+", " ", s).strip()


def prompt_words(text: str) -> list[str]:
    t = re.sub(r"```.*?(```|$)", " ", (text or "")[:PROMPT_SCAN_MAX], flags=re.S)
    t = drop_tagged(t)
    t = _URL.sub(" ", t)
    t = _PATHTOK.sub(" ", t)
    out = []
    for w in re.findall(r"[a-z][a-z0-9'-]*", t.lower()):
        w = w.split("'")[0].strip("-")                # what's -> what, don't -> don
        if len(w) < 3 or w in STOP or len(w) > 24:
            continue
        if w not in out:
            out.append(w)
        if len(out) == PROMPT_WORDS:
            break
    return out


def call_steps(name: str, inp: dict, keep_inspection: bool = False) -> list[str]:
    """One tool call -> the step shapes it adds to a sequence: a shell call's acting steps, or an MCP
    tool's name. Other tools add none. Shared with core's effectiveness collector, which weights the
    same sequences by attributed cost."""
    if name in SHELLS:
        out = []
        for seg in split_steps(str((inp or {}).get("command") or "")):
            shape = step_shape(seg, keep_inspection)
            if shape:
                out.append(shape)
        return out
    if name.startswith("mcp__"):
        return [name]
    return []


def _flat(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                if isinstance(b.get("text"), str):
                    parts.append(b["text"])
                elif "content" in b:
                    parts.append(_flat(b["content"]))
        return "\n".join(parts)
    return ""


def _typed_prompt(d: dict) -> str | None:
    if d.get("isMeta") or d.get("isCompactSummary"):
        return None
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return None
        c = " ".join(b.get("text") or "" for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return None
    t = c.strip()
    if not t or t.startswith(("<", "/", "[Request interrupted", "Caveat:")):
        return None
    return t


# ---------------------------------------------------------------------------
# one transcript -> its steps, commands and prompts
# ---------------------------------------------------------------------------

def scan_file(path: str, keep_inspection: bool = False, want_prompts: bool = True,
              line_cap: int = LINE_CAP) -> dict:
    """steps: [shape, call index]; calls: chars per call; commands: [text, call index];
    prompts: [words, example, chars spent until the next prompt]."""
    steps: list = []
    call_chars: list = []
    commands: list = []
    prompts: list = []
    by_tid: dict = {}
    cwd = ""
    span = 0                                         # chars since the last typed prompt
    try:
        fh = open(path, "rb")
    except OSError:
        return {"steps": [], "calls": [], "commands": [], "prompts": [], "cwd": ""}
    with fh:
        for raw in fh:
            if len(raw) > line_cap:
                if b'"tool_result"' in raw:
                    m = re.search(rb'"tool_use_id":"([A-Za-z0-9_-]+)"', raw[:16384])
                    if m and m.group(1).decode() in by_tid:
                        call_chars[by_tid[m.group(1).decode()]] += len(raw)
                    span += len(raw)
                continue
            if not (b'"assistant"' in raw or b'"user"' in raw):         # cheap skip of other line types
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if not cwd and isinstance(d.get("cwd"), str):
                cwd = d["cwd"]
            t = d.get("type")
            msg = d.get("message") or {}
            if t == "assistant":
                for b in msg.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        span += len(b.get("text") or "")
                    if b.get("type") != "tool_use":
                        continue
                    name = str(b.get("name") or "")
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    size = len(json.dumps(inp, ensure_ascii=False))
                    span += size
                    idx = len(call_chars)
                    call_chars.append(size)
                    by_tid[str(b.get("id") or idx)] = idx
                    shapes = call_steps(name, inp, keep_inspection)
                    steps += [[shape, idx] for shape in shapes]
                    if name in SHELLS:
                        ct = command_text(str(inp.get("command") or ""))
                        # a command that only looks around is not a process to script
                        if len(ct) >= MIN_COMMAND_CHARS and (keep_inspection or shapes):
                            commands.append([ct[:400], idx])
            elif t == "user":
                c = msg.get("content")
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            n = len(_flat(b.get("content")))
                            span += n
                            tid = str(b.get("tool_use_id") or "")
                            if tid in by_tid:
                                call_chars[by_tid[tid]] += n
                if want_prompts:
                    p = _typed_prompt(d)
                    if p is not None:
                        if prompts:
                            prompts[-1][2] = span
                        span = 0
                        words = prompt_words(p)
                        if len(words) >= 3 and not set(words) <= CONTINUE_WORDS:
                            prompts.append([words, re.sub(r"\s+", " ", _SECRETISH.sub("<secret>", p))[:EXAMPLE_MAX],
                                            0])
                        else:
                            prompts.append([[], "", 0])
    if prompts:
        prompts[-1][2] = span
    return {"steps": steps, "calls": call_chars, "commands": commands,
            "prompts": [p for p in prompts if p[0]], "cwd": cwd}


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def discover(projects: Path, cutoff: float, main_only: bool, project_filter: str | None) -> list[tuple]:
    """[(path, project folder name, session key, is_main)]."""
    out = []
    if not projects.is_dir():
        return out
    want = (project_filter or "").lower()
    for pdir in sorted(projects.iterdir()):
        if not pdir.is_dir() or (want and want not in pdir.name.lower()):
            continue
        globs = [("*.jsonl", True)] + ([] if main_only else [("*/subagents/**/agent-*.jsonl", False)])
        for pattern, is_main in globs:
            for f in pdir.glob(pattern):
                try:
                    if f.stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue
                out.append((str(f), pdir.name, str(f.with_suffix("")), is_main))
    return out


def _scan_job(job):
    path, project, key, is_main, keep_inspection = job
    return project, key, is_main, scan_file(path, keep_inspection, want_prompts=is_main)


def scan_all(files: list[tuple], keep_inspection: bool, workers: int | None) -> list[tuple]:
    jobs = [(p, proj, key, main, keep_inspection) for p, proj, key, main in files]
    if workers == 1 or len(jobs) < 8:
        return [_scan_job(j) for j in jobs]
    try:
        with ProcessPoolExecutor(max_workers=workers or min(8, os.cpu_count() or 2)) as pool:
            return list(pool.map(_scan_job, jobs, chunksize=4))
    except (OSError, RuntimeError):
        return [_scan_job(j) for j in jobs]


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def find_sequences(scans, min_n: int, max_n: int, min_sessions: int) -> list[dict]:
    """A scan may also carry "cost": a number per call (core's effectiveness collector passes each call's
    attributed API-equivalent USD). Each row then also has cost_total and cost_each, summed the same way
    as the characters: over the distinct calls of every occurrence."""
    grams: dict = defaultdict(lambda: {"sessions": set(), "projects": set(), "occurrences": 0, "chars": 0,
                                       "cost": None})
    for project, key, _main, s in scans:
        st = s["steps"]
        calls = s["calls"]
        cost = s.get("cost")
        for n in range(min_n, max_n + 1):
            for i in range(len(st) - n + 1):
                win = st[i:i + n]
                shape = tuple(x[0] for x in win)
                if len(set(shape)) == 1:
                    continue
                g = grams[shape]
                g["sessions"].add(key)
                g["projects"].add(project)
                g["occurrences"] += 1
                ids = sorted({x[1] for x in win})
                g["chars"] += sum(calls[c] for c in ids if c < len(calls))
                if cost is not None:
                    g["cost"] = (g["cost"] or 0.0) + sum(cost[c] for c in ids if c < len(cost))
    keep = {k: v for k, v in grams.items() if len(v["sessions"]) >= min_sessions}
    # Fold an n-gram into a longer one that contains it and is seen in nearly as many sessions.
    out = []
    for k, v in keep.items():
        n_s = len(v["sessions"])
        folded = False
        for k2, v2 in keep.items():
            if len(k2) > len(k) and len(v2["sessions"]) >= 0.8 * n_s and _contains(k2, k):
                folded = True
                break
        if not folded:
            r = _row(" -> ".join(k), v, len(k))
            r["shape"] = list(k)
            out.append(r)
    return out


def _shared_run(a: list, b: list) -> list:
    """The longest run of steps a and b share, in order."""
    best: list = []
    for i in range(len(a)):
        for j in range(len(b)):
            n = 0
            while i + n < len(a) and j + n < len(b) and a[i + n] == b[j + n]:
                n += 1
            if n > len(best):
                best = a[i:i + n]
    return best


def fold_variants(rows: list[dict]) -> list[dict]:
    """Ranked rows -> one row per family. `add -> commit -> push` and `commit -> push -> pr create` are
    one process seen from two windows: a row sharing all but one of its steps, in order, with a
    higher-ranked row is listed under it as a variant."""
    kept: list[dict] = []
    for r in rows:
        for k in kept:
            run = _shared_run(r["shape"], k["shape"])
            if len(set(run)) >= 2 and len(run) >= min(len(r["shape"]), len(k["shape"])) - 1:
                k.setdefault("variants", []).append(r["pattern"])
                break
        else:
            kept.append(r)
    return kept


def _contains(big: tuple, small: tuple) -> bool:
    n = len(small)
    return any(big[i:i + n] == small for i in range(len(big) - n + 1))


def _row(label: str, v: dict, n: int | None = None) -> dict:
    occ = max(1, v["occurrences"])
    per = est(v["chars"] / occ)
    r = {"pattern": label, "sessions": len(v["sessions"]), "projects": len(v["projects"]),
         "occurrences": v["occurrences"], "est_tokens_total": est(v["chars"]), "est_tokens_each": per,
         "score": est(v["chars"])}
    if v.get("cost") is not None:
        r["cost_total"] = round(v["cost"], 6)
        r["cost_each"] = round(v["cost"] / occ, 6)
    if n is not None:
        r["steps"] = n
    if v.get("examples"):
        r["examples"] = v["examples"][:3]
    return r


def find_commands(scans, min_sessions: int) -> list[dict]:
    cmds: dict = defaultdict(lambda: {"sessions": set(), "projects": set(), "occurrences": 0, "chars": 0})
    for project, key, _main, s in scans:
        calls = s["calls"]
        for text, idx in s["commands"]:
            c = cmds[text]
            c["sessions"].add(key)
            c["projects"].add(project)
            c["occurrences"] += 1
            c["chars"] += calls[idx] if idx < len(calls) else 0
    return [_row(k, v) for k, v in cmds.items() if len(v["sessions"]) >= min_sessions]


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def find_requests(scans, min_sessions: int, similarity: float) -> list[dict]:
    clusters: list[dict] = []
    index: dict = defaultdict(list)                  # word -> cluster ids, so each prompt checks a few
    for project, key, is_main, s in scans:
        if not is_main:
            continue
        for words, example, chars in s["prompts"]:
            ws = set(words)
            best, best_j = None, 0.0
            for cid in {c for w in ws for c in index[w]}:
                j = _jaccard(ws, clusters[cid]["words"])
                if j > best_j:
                    best, best_j = cid, j
            if best is None or best_j < similarity:
                best = len(clusters)
                clusters.append({"words": ws, "label": " ".join(words[:5]), "sessions": set(), "projects": set(),
                                 "occurrences": 0, "chars": 0, "examples": []})
                for w in ws:
                    index[w].append(best)
            c = clusters[best]
            if key not in c["sessions"] and len(c["examples"]) < 3:
                c["examples"].append(example)
            c["sessions"].add(key)
            c["projects"].add(project)
            c["occurrences"] += 1
            c["chars"] += chars
    return [_row(c["label"], c) for c in clusters if len(c["sessions"]) >= min_sessions]


def collect(projects: Path, since_days: float, min_sessions: int = 3, min_n: int = 3, max_n: int = 5,
            similarity: float = 0.5, keep_inspection: bool = False, main_only: bool = False,
            project_filter: str | None = None, top: int = 10, workers: int | None = None) -> dict:
    cutoff = time.time() - since_days * 86400
    files = discover(projects, cutoff, main_only, project_filter)
    scans = scan_all(files, keep_inspection, workers)
    def order(rows):
        return sorted(rows, key=lambda r: (-r["score"], -r["sessions"], r["pattern"]))

    def rank(rows):
        return order(rows)[:top]

    return {
        "since_days": since_days, "transcripts": len(files),
        "main_sessions": sum(1 for f in files if f[3]), "min_sessions": min_sessions,
        "sequences": fold_variants(order(find_sequences(scans, min_n, max_n, min_sessions)))[:top],
        "commands": rank(find_commands(scans, min_sessions)),
        "requests": rank(find_requests(scans, min_sessions, similarity)),
    }


def render(report: dict) -> str:
    lines = [f"Repeated work in {report['transcripts']} transcripts ({report['main_sessions']} main sessions), "
             f"last {report['since_days']:g} days; seen in >= {report['min_sessions']} sessions. "
             "Counts MEASURED, tokens ESTIMATED (chars/4)."]
    for kind, title in (("sequences", "Command sequences (script candidates)"),
                        ("commands", "Recurring commands (alias or script candidates)"),
                        ("requests", "Recurring requests (skill or script candidates)")):
        rows = report[kind]
        lines.append("")
        lines.append(f"{title}: {len(rows)}")
        for i, r in enumerate(rows, 1):
            lines.append(f"{i:>3}. {r['sessions']} sessions / {r['projects']} projects, {r['occurrences']}x, "
                         f"~{r['est_tokens_each']:,} tok each, ~{r['est_tokens_total']:,} total")
            lines.append(f"     {r['pattern'][:200]}")
            if r.get("variants"):
                lines.append(f"       + {len(r['variants'])} overlapping variant(s), e.g. {r['variants'][0][:160]}")
            for ex in r.get("examples") or []:
                lines.append(f"       e.g. {ex}")
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Find work done by hand more than once (script/skill candidates).")
    ap.add_argument("--projects", help="transcript root (default: $CLAUDE_CONFIG_DIR/projects)")
    ap.add_argument("--project", help="only transcript folders whose name contains this")
    ap.add_argument("--since", type=float, default=30, help="days back (default 30)")
    ap.add_argument("--min-sessions", type=int, default=3)
    ap.add_argument("--min-n", type=int, default=3)
    ap.add_argument("--max-n", type=int, default=5)
    ap.add_argument("--similarity", type=float, default=0.5, help="prompt word-set overlap to group requests")
    ap.add_argument("--keep-inspection", action="store_true", help="keep look-around steps (ls, git status, ...)")
    ap.add_argument("--main-only", action="store_true", help="skip subagent transcripts")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--workers", type=int, help="parallel readers (default: up to 8)")
    ap.add_argument("--json", help="also write the full report here")
    a = ap.parse_args(argv)
    if a.min_n < 2 or a.max_n < a.min_n:
        ap.error("need 2 <= --min-n <= --max-n")
    report = collect(Path(a.projects) if a.projects else claude_home() / "projects", a.since, a.min_sessions,
                     a.min_n, a.max_n, a.similarity, a.keep_inspection, a.main_only, a.project, a.top, a.workers)
    print(render(report))
    if a.json:
        Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
