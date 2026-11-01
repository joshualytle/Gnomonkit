#!/usr/bin/env python3
"""PreToolUse hook: block a Bash heredoc that carries a backslash.

WHY A HOOK AND NOT A RULE. A written rule, even a bright line ("a single
backslash, a regex, or triple quotes: write the file"), tends to be broken under
time pressure -- often by the same agent that wrote it, on a script that happened
to survive once, so the habit sticks. A rule that needs judgement to apply is a
rule that will be broken; this removes the judgement.

WHAT IT CATCHES. A Bash tool call whose command contains a heredoc (<<EOF,
<<'EOF', <<"EOF", <<-EOF) and, inside that heredoc body, a backslash or a
triple-quote. Those are the two things that get mangled between the tool call
and the interpreter -- the failure shows up as a syntax error that cannot be
true of the code you can see.

WHAT IT DELIBERATELY ALLOWS. Heredocs without escapes: commit messages, prose,
plain command lists. Backslashes OUTSIDE a heredoc (Windows paths on a command
line are fine). And any command with `# heredoc-ok:` and a reason, because a
guard with no override gets deleted the first time it is wrong.

EXIT 2 blocks the call and the message goes to the agent. Anything else lets
the call through -- including this hook crashing, because a broken guard must
not take the shell down with it.
"""

import bisect
import json
import re
import sys


# An opener: << or <<- (never the <<< here-string), an optional quote, the delimiter word, the same
# quote. Text may follow on the opener line (`cat <<'EOF' > f.py`), as bash allows.
OPENER = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def heredocs(cmd: str):
    """Yield (delimiter, body) for every terminated heredoc, reading each line a bounded number of times.

    WHY NOT ONE REGEX. The first version matched `<<WORD\n(.*?)^WORD$` with re.S over the whole
    command. With many openers and no terminator (C++ `<<` streams, a pasted log, a long generated
    script) every opener scanned to the end of the text: quadratic, seconds on tens of kilobytes,
    and this hook runs before every Bash call. Here the lines that could close a heredoc are
    indexed once, and each opener finds its terminator by bisecting that index.
    """
    lines = cmd.split("\n")
    plain, dashed = {}, {}  # terminator text -> line numbers; dashed allows leading tabs (<<-)
    for k, ln in enumerate(lines):
        t = ln.rstrip()
        if WORD.fullmatch(t):
            plain.setdefault(t, []).append(k)
        t = t.lstrip("\t")
        if WORD.fullmatch(t):
            dashed.setdefault(t, []).append(k)

    i = 0
    while i < len(lines):
        found = OPENER.findall(lines[i]) if "<<" in lines[i] else []
        start = nxt = i + 1
        for dash, _quote, word in found:
            ends = (dashed if dash else plain).get(word, [])
            j = bisect.bisect_left(ends, start)
            if j == len(ends):
                continue  # unterminated: not treated as a heredoc
            yield word, "\n".join(lines[start:ends[j]])
            start = nxt = ends[j] + 1  # several heredocs on one line read their bodies in turn
        i = nxt


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    if "<<" not in cmd:
        return 0
    if "# heredoc-ok:" in cmd:
        return 0

    for word, body in heredocs(cmd):
        has_backslash = "\\" in body
        has_triple = '"""' in body or "'''" in body
        if has_backslash or has_triple:
            what = "a backslash" if has_backslash else "triple quotes"
            print(
                f"BLOCKED: this heredoc (<<{word}) contains {what}. Escapes do "
                f"not reliably survive the tool-call -> shell -> interpreter path; "
                f"the failure surfaces as a syntax error that cannot be true of the "
                f"code you wrote. Write the content to a file with the Write tool and "
                f"run the file. (Override for a genuine exception with "
                f"`# heredoc-ok: <reason>` in the command.)",
                file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
