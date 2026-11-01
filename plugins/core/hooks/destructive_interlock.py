#!/usr/bin/env python3
"""PreToolUse interlock: before an irreversible command, surface what we learned.

WHAT THIS IS FOR: stop tangents and adverse actions during investigation --
dropping a database while debugging, wiping a pool that turned out to be in use.
The shape of the near-miss: a listing command answers a narrower question than
the one asked (`zpool import` lists pools that are *not yet* imported, so "no
pools available to import" says nothing about pools already online), the empty
answer is read as *empty*, and a forced create follows against storage that
holds data. Only the tool's own refusal stands between that and a wipe.

That is not a knowledge problem. The lesson can be written down and still
nothing asks for it at the moment it matters.

SO THIS IS THE TRIGGER, not another place to search. It fires on the command,
before it runs, and it does two things:

  1. BLOCKS, so the enumerate-first rule cannot be skipped by momentum.
  2. RETRIEVES the lessons that apply to THIS command and prints them.

THE BLOCK DOES NOT DEPEND ON THE SEARCH WORKING. If no lesson matches, the
command is still blocked and the requirement still printed -- a safety interlock
that fails open the moment its index is unhelpful is not an interlock. It
degrades to "you must enumerate first", which is the rule anyway.

IT READS CONCLUSIONS, NOT RAW MATERIAL -- curated memory files only, no
transcript search, by design. A transcript corpus is largely machine text (tool
output, schemas, logs) and a destructive command's vocabulary is common, so
keyword ranking finds unrelated machine text that merely shares the command's
words, and filtering
(prose-only classification, a literal-token requirement, over-fetch-and-narrow)
does not fix that reliably. Precision that low is
actively harmful HERE, though the same search is useful elsewhere: irrelevant
evidence under a safety block makes the block read as boilerplate, and
boilerplate gets dismissed. This gate's entire value is that it is never
ignored.

Memory files are the layer where an incident has already been judged worth
keeping, so a match there is on point: a lesson about a storage command for that
command, a runbook for deleting that kind of resource. Promoting
raw incidents INTO that layer is the consolidation pipeline's job, not this
hook's. It also keeps the hook fast -- a few dozen small files, no index, no
model, no process to warm.

SCOPE IS AN EXPLICIT LIST of irreversible actions, not a general "dangerous
command" heuristic. Blocking every `rm` would train instant dismissal, and a
gate people dismiss is worse than no gate.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

# GNOMONKIT_MEMORY_GLOB overrides where lessons are read from; an empty value reads none, which a
# sandboxed run (e.g. Gnomonkit Bench) uses so the user's real memory never enters a test session.
MEMORY_GLOB = os.environ.get("GNOMONKIT_MEMORY_GLOB", os.path.join(
    os.path.expanduser("~"), ".claude", "projects", "*", "memory", "*.md"))

# The enumerated list of irreversible actions. Each entry is
# (label, pattern, what the command destroys) -- the third field is what the
# agent must enumerate before re-issuing.
DESTRUCTIVE = [
    ("ZFS pool", r"\bzpool\s+(?:create|destroy|labelclear)\b",
     "the pool's datasets and every zvol on it"),
    ("filesystem wipe", r"\b(?:wipefs|mkfs(?:\.\w+)?|sgdisk\s+--zap|parted\s+.*\bmklabel)\b",
     "every partition and filesystem on the target device"),
    # Reading INTO /dev/null (a read benchmark) destroys nothing.
    ("raw disk write", r"\bdd\s+[^|]*\bof=/dev/(?!null\b|zero\b|stdout\b|stderr\b|fd/)",
     "whatever currently occupies the target device"),
    ("LVM removal", r"\b(?:vgremove|lvremove|pvremove)\b",
     "the volume group or logical volume and its contents"),
    ("Kubernetes volume", r"kubectl\s+delete\s+(?:pvc|pv|persistentvolume\w*|statefulset)\b",
     "the PersistentVolume and the data on it"),
    ("force pod delete", r"kubectl\s+delete\b[^\n]*(?:--force|--grace-period[= ]0)",
     "orphaned CSI mounts the kubelet will recreate from the pod directory"),
    ("database drop", r"\b(?:DROP\s+(?:DATABASE|TABLE|SCHEMA)|TRUNCATE\s+TABLE)\b",
     "the table or database and every row in it"),
    ("restore over live", r"\b(?:psql|mysql|mariadb)\b[^\n]*<[^\n]*\.(?:sql|dump)\b",
     "the current contents of the target database"),
    ("recursive delete", r"\brm\s+(?:-\w*[rR]\w*f|-\w*f\w*[rR])\b",
     "the directory tree and everything under it"),
    ("force push", r"\bgit\s+push\b[^\n]*--force(?!-with-lease)",
     "commits on the remote that nobody has fetched"),
    ("bucket delete", r"\b(?:aws\s+s3\s+rm\b[^\n]*--recursive|rclone\s+(?:purge|delete)\b|gsutil\s+rm\b[^\n]*-r)",
     "the objects under that prefix, with no undo"),
    ("branch delete", r"\bgit\s+(?:branch\s+-D|push\b[^\n]*--delete)\b",
     "the branch and any commits reachable only from it"),
]

# Commands whose quoted arguments are TEXT: a pattern, a message, a body, file content. A
# destructive phrase inside their quotes is being described, not run. Prose about a hazard is not
# the hazard -- the same distinction the standards detectors hit.
#
# Everything else keeps its quotes, because there a quoted argument is often itself a command:
# `bash -c '...'`, `powershell -Command "..."`, `ssh host '...'`, `psql -c "DROP ..."`,
# `sqlite3 db "..."`. Matching inside those is the point of the gate. A false block trains
# dismissal, but a missed one is the failure the gate exists to stop, so the list is of text
# consumers, never of "dangerous" commands.
TEXT_COMMANDS = {
    "echo", "printf", "grep", "egrep", "fgrep", "rg", "ag", "findstr", "cat", "less", "more", "head",
    "tail", "tee", "type", "gh", "git", "jq", "yq", "select-string", "sls", "write-host",
    "write-output", "write-error", "write-warning", "write-verbose", "set-content", "add-content",
    "out-file", "get-content",
}
# Of those, the ones that never act on their arguments at all: every word they get is text, quoted
# or not (`echo rm -rf is irreversible`). git, gh and tee are not here: `git push --force` acts.
PRINT_COMMANDS = {
    "echo", "printf", "grep", "egrep", "fgrep", "rg", "ag", "findstr", "select-string", "sls",
    "write-host", "write-output", "write-error", "write-warning", "write-verbose",
}
# Where text may be piped and stay text. Piped anywhere else -- a shell, `kubectl apply -f -` (a
# manifest whose container runs `rm -rf`), ssh, a database client, anything unknown -- it is a
# command, and nothing in it is masked: a heredoc piped into one of those runs.
TEXT_SINKS = TEXT_COMMANDS | {
    "head", "tail", "sort", "uniq", "wc", "cut", "tr", "column", "nl", "fold", "fmt", "base64", "xxd",
    "od", "md5sum", "sha1sum", "sha256sum", "iconv", "clip", "pbcopy", "select-object", "where-object",
    "format-table", "format-list", "out-string", "out-null", "measure-object", "sort-object",
    "convertfrom-json", "convertto-json", "out-host",
}
# Words that run the NEXT word as the command.
PREFIXES = {"sudo", "doas", "env", "time", "nohup", "exec", "command", "builtin", "nice", "xargs", "&", "."}
SEPARATORS = ("&&", "||", "|", ";", "\n")


def segments(cmd: str, shell: str = "bash"):
    """Split at unquoted pipes, ;, &&, || and newlines. Each segment is a list of (text, quoted)
    parts. Quoting follows POSIX shells ('...' literal, "..." with \\ escapes) and PowerShell
    (backtick escapes; '' inside '...'). A double-quoted part holding $( or a backtick is kept
    as unquoted, because the shell RUNS it. An unquoted # starts a comment. A heredoc body
    (<<EOF ... EOF) is a quoted part of the segment whose command reads it."""
    segs, seps, parts, buf = [], [], [], []
    i, n = 0, len(cmd)
    pending_heredocs = []

    def flush_buf():
        if buf:
            parts.append(("".join(buf), False))
            buf.clear()

    def end_segment(sep=""):
        flush_buf()
        if parts:
            segs.append(list(parts))
            seps.append(sep)
            parts.clear()

    while i < n:
        c = cmd[i]
        if c in ("'", '"'):
            j, out = i + 1, []
            while j < n and cmd[j] != c:
                if cmd[j] in ("\\", "`") and c == '"' and j + 1 < n:
                    out.append(cmd[j:j + 2])
                    j += 2
                    continue
                if c == "'" and cmd[j:j + 2] == "''":  # PowerShell's escaped quote
                    out.append("'")
                    j += 2
                    continue
                out.append(cmd[j])
                j += 1
            body = "".join(out)
            flush_buf()
            # In PowerShell a backtick is an escape, not a substitution; $( ) runs in both.
            runs = c == '"' and ("$(" in body or (shell != "powershell" and "`" in body))
            parts.append((body, not runs))
            i = j + 1
            continue
        if c == "#" and (i == 0 or cmd[i - 1].isspace()):
            while i < n and cmd[i] != "\n":
                i += 1
            continue
        if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = re.match(r"<<-?\s*(['\"]?)([A-Za-z_][\w]*)\1", cmd[i:])
            if m:
                # Remember WHICH segment opened it: `cat <<EOF | kubectl apply -f - | tail -1` feeds
                # the body to cat (and so to kubectl), not to tail, the last segment on the line.
                pending_heredocs.append((m.group(2), len(segs)))
                buf.append(cmd[i:i + m.end()])
                i += m.end()
                continue
        if c == "\n" and pending_heredocs:
            # The body runs to the delimiter line; it belongs to THIS segment, quoted.
            delim, owner = pending_heredocs.pop(0)
            k = i + 1
            body = []
            while k < n:
                e = cmd.find("\n", k)
                line = cmd[k:e if e != -1 else n]
                k = (e + 1) if e != -1 else n
                if line.strip() == delim:
                    break
                body.append(line)
            flush_buf()
            if owner < len(segs):
                segs[owner].append(("\n".join(body), True))
            else:
                parts.append(("\n".join(body), True))
            i = k
            end_segment()
            continue
        sep = next((s for s in SEPARATORS if cmd.startswith(s, i)), None)
        if sep:
            end_segment(sep)
            i += len(sep)
            continue
        buf.append(c)
        i += 1
    end_segment()
    return segs, seps


def command_word(parts) -> str:
    words = " ".join(t if not q else "Q" for t, q in parts).split()
    k = 0
    while k < len(words):
        w = words[k].lower()
        if w in PREFIXES or re.match(r"^[A-Za-z_]\w*=", w) or (w.startswith("-") and k and words[k - 1].lower() in PREFIXES):
            k += 1
            continue
        return os.path.basename(w).removesuffix(".exe")
    return ""


def substitutions(text: str, shell: str = "bash") -> list[str]:
    """$( ... ) and `...` bodies: the shell runs these wherever they appear, even inside echo."""
    out, i = [], 0
    while True:
        i = text.find("$(", i)
        if i == -1:
            break
        depth, j = 1, i + 2
        while j < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            j += 1
        out.append(text[i + 2:j - 1])
        i = j
    if shell != "powershell":
        out += re.findall(r"`([^`]+)`", text)
    return out


def classify(cmd: str, shell: str = "bash", _depth: int = 0):
    hits = []

    def add(found):
        for h in found:
            if h[0] not in (x[0] for x in hits):
                hits.append(h)

    segs, seps = segments(cmd, shell)
    words = [command_word(p) for p in segs]
    for k, parts in enumerate(segs):
        word = words[k]
        # Text piped into something that may execute it is a command, so nothing in it is masked.
        feeds_shell = seps[k] == "|" and k + 1 < len(segs) and words[k + 1] not in TEXT_SINKS
        if _depth < 3:
            for t, q in parts:
                if not q:
                    for sub in substitutions(t, shell):
                        add(classify(sub, shell, _depth + 1))
        if word in PRINT_COMMANDS and not feeds_shell:
            continue
        text_cmd = word in TEXT_COMMANDS and not feeds_shell
        seg = " ".join(t for t, q in parts if not (q and text_cmd))
        for label, pat, destroys in DESTRUCTIVE:
            m = re.search(pat, seg, re.I)
            if m:
                add([(label, m.group(0)[:48], destroys)])
    return hits


def keywords(cmd: str, hits) -> str:
    """A query built from what the command actually touches."""
    toks = re.findall(r"[A-Za-z][\w.-]{2,}", cmd)
    stop = {"sudo", "then", "else", "true", "false", "kubectl", "bash", "sh"}
    toks = [t for t in toks if t.lower() not in stop][:12]
    return " ".join([h[0] for h in hits] + toks)


def search_memory(query: str, limit: int = 3):
    """Curated memory first: a hand-written rule outranks raw transcript."""
    if not MEMORY_GLOB:
        return []
    want = {w.lower() for w in re.findall(r"[a-z0-9]{4,}", query.lower())}
    scored = []
    for path in glob.glob(MEMORY_GLOB):
        if os.path.basename(path) == "MEMORY.md":
            continue
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        have = {w.lower() for w in re.findall(r"[a-z0-9]{4,}", text.lower())}
        overlap = len(want & have)
        if overlap >= 3:
            desc = re.search(r"^description:\s*(.+)$", text, re.M)
            scored.append((overlap, os.path.basename(path)[:-3],
                           (desc.group(1) if desc else "")[:200]))
    scored.sort(reverse=True)
    return scored[:limit]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    ti = payload.get("tool_input") or {}
    cmd = str(ti.get("command") or "")
    if not cmd.strip():
        return 0
    if "# interlock-ok:" in cmd:
        return 0

    hits = classify(cmd, "powershell" if payload.get("tool_name") == "PowerShell" else "bash")
    if not hits:
        return 0

    L = ["", "=" * 72,
         "  IRREVERSIBLE ACTION BLOCKED -- enumerate before destroying", "=" * 72, ""]
    for label, frag, destroys in hits:
        L.append(f"  {label}: `{frag}`")
        L.append(f"      destroys: {destroys}")
    L.append("")

    q = keywords(cmd, hits)
    mem = search_memory(q)
    if mem:
        L.append("  WHAT WE ALREADY LEARNED ABOUT THIS:")
        for _, name, desc in mem:
            L.append(f"    [{name}]")
            L.append(f"      {desc}")
        L.append("")

    # THERE IS NO RAW-TRANSCRIPT SEARCH HERE, and the reason is worth keeping.
    #
    # A keyword search over a transcript corpus surfaces unrelated machine text
    # that merely shares the command's words, and filters (prose-only classification, a
    # literal-token requirement, over-fetch-and-narrow) do not reliably fix it.
    #
    # That is not a tuning failure, it is the corpus. Much of it is not prose,
    # BM25 ranks on overlap, and a destructive command's vocabulary ("delete",
    # "create", "drop") is common. Precision that low is actively harmful HERE
    # even though the same search is useful elsewhere: irrelevant evidence under
    # a safety block makes the block read as boilerplate, and boilerplate gets
    # dismissed. The interlock's whole value is that it is never ignored.
    #
    # So this reads CONCLUSIONS, not raw material. Curated memory is the layer
    # where an incident has already been judged worth keeping, so a match is
    # on point -- a lesson about a command for that command, a runbook for
    # deleting that kind of resource. Promoting raw incidents into
    # that layer is the consolidation pipeline's job, not the interlock's.
    if not mem:
        L.append("  No prior lesson matched this command. That is not clearance --")
        L.append("  it means nothing has been written down yet, so enumerate with")
        L.append("  more care, not less.")
        L.append("")

    L += [
        "  REQUIRED BEFORE RE-ISSUING:",
        "    1. List the target's ACTUAL CONTENTS with a command that enumerates",
        "       what IS there, not what is AVAILABLE. `zpool import` says 'no pools",
        "       available' about a pool that is already imported and online.",
        "    2. State the size and what it is.",
        "    3. For anything holding data the user has not written off, get an",
        "       explicit go-ahead first.",
        "",
        "  Do NOT reach for -f/--force to get past a safety refusal. That refusal",
        "  is usually correct -- it may be the only thing between you and a wipe.",
        "",
        "  Override once verified: add `# interlock-ok: <what you enumerated>`.",
        "=" * 72, "",
    ]
    print("\n".join(L), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
