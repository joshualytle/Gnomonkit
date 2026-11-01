"""Split a shell command into simple commands and words the way the shell would, for hooks that need to
know what a command runs and which files it names.

Not a shell, and no security boundary on its own: enough of bash, PowerShell and cmd.exe quoting to find
each command word and its arguments with the quotes removed, the commands nested in $(...), backticks and
here-documents, and the operators between commands. What it can't follow (a variable, a glob) stays in
the word as written, for the caller to treat as unknown.

    commands(text, lang) -> [Command]     lang: "bash" | "ps" | "cmd"

A Command has `words` (Word: `text` without quotes, `quoted`, `subs` = the command texts nested in it,
`role` = "arg" | "redir" (a redirection target) | "data" (a here-string) | "delim"), `end` (the operator
after it: ";", "&&", "|", "&", "\\n", "(", ")", ... or ""), `depth` (how many groups it is inside) and
`heredocs` ([(body, expands)] fed to it).

Standard library only.
"""
from __future__ import annotations

import re

LANGS = ("bash", "ps", "cmd")


class Word:
    __slots__ = ("text", "quoted", "subs", "role")

    def __init__(self, text: str, quoted: bool = False, subs=(), role: str = "arg"):
        self.text, self.quoted, self.subs, self.role = text, quoted, list(subs), role

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Word(%r%s)" % (self.text, ", subs=%r" % self.subs if self.subs else "")


class Command:
    __slots__ = ("words", "end", "depth", "heredocs")

    def __init__(self, depth: int = 0):
        self.words: list = []
        self.end = ""
        self.depth = depth
        self.heredocs: list = []

    def texts(self) -> list:
        return [w.text for w in self.words if w.role in ("arg", "redir")]


# ---- bash ---------------------------------------------------------------------------------------------
_BASH_OPS = ("&&", "||", ";;&", ";;", ";&", "|&", "&>>", "&>", ">>", ">&", ">|", "<<<", "<<-", "<<", "<>", "<&",
             "(", ")", ";", "&", "|", "<", ">")
_REDIRS = {"&>>", "&>", ">>", ">&", ">|", "<>", "<&", "<", ">", "2>", "2>>", "*>", "*>>", "2>&1", "*>&1"}
_ANSI = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "v": "\v",
         "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _backtick_end(s: str, j: int) -> int:
    """Index just past the backtick closing the one before s[j]."""
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == "`":
            return j + 1
        j += 1
    return len(s)


def _dq_end(s: str, j: int) -> int:
    """Index just past the double quote closing the one before s[j]."""
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1
        if s.startswith("$(", j):
            _, j = _balanced(s, j + 2)
            continue
        if c == "`":
            j = _backtick_end(s, j + 1)
            continue
        j += 1
    return len(s)


def _balanced(s: str, i: int, open_: str = "(", close: str = ")") -> tuple:
    """(inner text, index past the close) for a group whose opening bracket is just before s[i]."""
    depth, j, n = 1, i, len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            k = s.find("'", j + 1)
            j = n if k < 0 else k + 1
            continue
        if c == '"':
            j = _dq_end(s, j + 1)
            continue
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return s[i:j], j + 1
        j += 1
    return s[i:], n


def _ansi_c(s: str, j: int) -> tuple:
    """$'...': (decoded text, index past the closing quote); s[j] is the first character inside."""
    out, n = [], len(s)
    while j < n and s[j] != "'":
        if s[j] == "\\" and j + 1 < n:
            e = s[j + 1]
            if e == "x":
                m = re.match(r"[0-9A-Fa-f]{1,2}", s[j + 2:])
                if m:
                    out.append(chr(int(m.group(0), 16)))
                    j += 2 + m.end()
                    continue
            elif e in "uU":
                m = re.match(r"[0-9A-Fa-f]{1,%d}" % (4 if e == "u" else 8), s[j + 2:])
                if m:
                    out.append(chr(int(m.group(0), 16)))
                    j += 2 + m.end()
                    continue
            elif e in "01234567":
                m = re.match(r"[0-7]{1,3}", s[j + 1:])
                out.append(chr(int(m.group(0), 8)))
                j += 1 + m.end()
                continue
            out.append(_ANSI.get(e, "\\" + e))
            j += 2
            continue
        out.append(s[j])
        j += 1
    return "".join(out), min(j + 1, n)


def _bash_items(s: str) -> list:
    """Words and ("op", text) / ("heredoc", body, expands) items, in order."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    items: list = []
    st = {"buf": [], "quoted": False, "subs": [], "started": False}
    pending: list = []          # here-documents waiting for the end of their line: (delim, strip_tabs, expands)
    want_delim = [False]

    def flush():
        if st["started"]:
            w = Word("".join(st["buf"]), st["quoted"], st["subs"])
            if want_delim[0]:
                w.role = "delim"
                pending.append((w.text, want_delim[0] == "<<-", not w.quoted))
                want_delim[0] = False
            items.append(w)
        st.update(buf=[], quoted=False, subs=[], started=False)

    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and s[i + 1:i + 2] == "\n":
            i += 2
            continue
        if c in " \t":
            flush()
            i += 1
            continue
        if c == "\n":
            flush()
            items.append(("op", "\n"))
            i += 1
            if pending:
                i = _read_heredocs(s, i, pending, items)
                pending.clear()
            continue
        if c == "#" and not st["started"]:
            k = s.find("\n", i)
            i = n if k < 0 else k
            continue
        buf = st["buf"]
        if c == "\\":
            st["started"] = True
            if i + 1 < n:
                buf.append(s[i + 1])
            i += 2
            continue
        if c == "'":
            st["started"] = st["quoted"] = True
            k = s.find("'", i + 1)
            buf.append(s[i + 1:] if k < 0 else s[i + 1:k])
            i = n if k < 0 else k + 1
            continue
        if c == "$" and s[i + 1:i + 2] == "'":
            st["started"] = st["quoted"] = True
            text, i = _ansi_c(s, i + 2)
            buf.append(text)
            continue
        if c == '"' or (c == "$" and s[i + 1:i + 2] == '"'):
            st["started"] = st["quoted"] = True
            j = i + (2 if c == "$" else 1)
            while j < n and s[j] != '"':
                ch = s[j]
                if ch == "\\" and j + 1 < n and s[j + 1] in '$`"\\\n':
                    if s[j + 1] != "\n":
                        buf.append(s[j + 1])
                    j += 2
                    continue
                if s.startswith("$((", j):
                    inner, k = _balanced(s, j + 3)
                    buf.append(s[j:k])
                    j = k
                    continue
                if s.startswith("$(", j):
                    inner, k = _balanced(s, j + 2)
                    st["subs"].append(inner)
                    buf.append(s[j:k])
                    j = k
                    continue
                if ch == "`":
                    k = _backtick_end(s, j + 1)
                    st["subs"].append(s[j + 1:k - 1])
                    buf.append(s[j:k])
                    j = k
                    continue
                buf.append(ch)
                j += 1
            i = j + 1
            continue
        if s.startswith("$((", i):
            st["started"] = True
            _, k = _balanced(s, i + 3)
            buf.append(s[i:k])
            i = k
            continue
        if s.startswith("$(", i):
            st["started"] = True
            inner, k = _balanced(s, i + 2)
            st["subs"].append(inner)
            buf.append(s[i:k])
            i = k
            continue
        if s.startswith("${", i):
            st["started"] = True
            _, k = _balanced(s, i + 2, "{", "}")
            buf.append(s[i:k])
            i = k
            continue
        if c == "`":
            st["started"] = True
            k = _backtick_end(s, i + 1)
            st["subs"].append(s[i + 1:k - 1])
            buf.append(s[i:k])
            i = k
            continue
        if c in ";&|()<>":
            if c in "<>" and s[i + 1:i + 2] == "(":        # process substitution: <(cmd) >(cmd)
                flush()
                inner, k = _balanced(s, i + 2)
                items.append(Word(s[i:k], False, [inner]))
                i = k
                continue
            if c in "<>" and st["started"] and not st["quoted"] and not st["subs"] and "".join(st["buf"]).isdigit():
                st.update(buf=[], quoted=False, subs=[], started=False)   # `2>&1`: the 2 is a file descriptor
            flush()
            op = next(o for o in _BASH_OPS if s.startswith(o, i))
            i += len(op)
            if op in ("<<", "<<-"):
                want_delim[0] = op
            items.append(("op", op))
            continue
        st["started"] = True
        buf.append(c)
        i += 1
    flush()
    return items


def _read_heredocs(s: str, i: int, pending: list, items: list) -> int:
    for delim, strip_tabs, expands in pending:
        body = []
        while i < len(s):
            k = s.find("\n", i)
            line = s[i:] if k < 0 else s[i:k]
            i = len(s) if k < 0 else k + 1
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                break
            body.append(line)
        items.append(("heredoc", "\n".join(body), expands))
    return i


# ---- PowerShell ---------------------------------------------------------------------------------------
_PS_SQ = "'‘’‚‛"
_PS_DQ = '"“”„'
_PS_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "e": "\x1b"}


def _ps_paren_end(s: str, i: int) -> tuple:
    """(inner, index past the close) for a PowerShell ( group opened just before s[i]."""
    depth, j, n = 1, i, len(s)
    while j < n:
        c = s[j]
        if c == "`":
            j += 2
            continue
        if c in _PS_SQ:
            j += 1
            while j < n and not (s[j] in _PS_SQ and s[j + 1:j + 2] not in _PS_SQ):
                j += 2 if s[j] in _PS_SQ else 1
            j += 1
            continue
        if c in _PS_DQ:
            j += 1
            while j < n and s[j] not in _PS_DQ:
                j += 2 if s[j] == "`" else 1
            j += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return s[i:j], j + 1
        j += 1
    return s[i:], n


def _ps_items(s: str) -> list:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    items: list = []
    st = {"buf": [], "quoted": False, "subs": [], "started": False}

    def flush():
        if st["started"]:
            items.append(Word("".join(st["buf"]), st["quoted"], st["subs"]))
        st.update(buf=[], quoted=False, subs=[], started=False)

    i, n = 0, len(s)
    while i < n:
        c = s[i]
        buf = st["buf"]
        if c == "`":
            if s[i + 1:i + 2] == "\n":
                i += 2
                continue
            st["started"] = True
            if i + 1 < n:
                buf.append(s[i + 1])
            i += 2
            continue
        if c in " \t":
            flush()
            i += 1
            continue
        if c in "\n;":
            flush()
            items.append(("op", c))
            i += 1
            continue
        if c == "<" and s[i + 1:i + 2] == "#":
            k = s.find("#>", i + 2)
            i = n if k < 0 else k + 2
            continue
        if c == "#" and not st["started"]:
            k = s.find("\n", i)
            i = n if k < 0 else k
            continue
        if c == "@" and s[i + 1:i + 2] and s[i + 1] in _PS_SQ + _PS_DQ and s[i + 2:i + 3] == "\n":
            q = s[i + 1]
            close = "\n" + q + "@"
            k = s.find(close, i + 3)
            body = s[i + 3:] if k < 0 else s[i + 3:k]
            st["started"] = st["quoted"] = True
            if q in _PS_DQ:
                st["subs"].extend(_ps_subexpressions(body))
            buf.append(body)
            i = n if k < 0 else k + len(close)
            continue
        if c in _PS_SQ:
            st["started"] = st["quoted"] = True
            j = i + 1
            while j < n:
                if s[j] in _PS_SQ:
                    if s[j + 1:j + 2] in _PS_SQ and s[j + 1:j + 2]:
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(s[j])
                j += 1
            i = j + 1
            continue
        if c in _PS_DQ:
            st["started"] = st["quoted"] = True
            j = i + 1
            while j < n:
                ch = s[j]
                if ch in _PS_DQ:
                    if s[j + 1:j + 2] and s[j + 1] in _PS_DQ:
                        buf.append('"')
                        j += 2
                        continue
                    break
                if ch == "`" and j + 1 < n:
                    buf.append(_PS_ESC.get(s[j + 1], s[j + 1]))
                    j += 2
                    continue
                if s.startswith("$(", j):
                    inner, k = _ps_paren_end(s, j + 2)
                    st["subs"].append(inner)
                    buf.append(s[j:k])
                    j = k
                    continue
                buf.append(ch)
                j += 1
            i = j + 1
            continue
        if s.startswith("${", i):
            st["started"] = True
            k = s.find("}", i + 2)
            buf.append(s[i:] if k < 0 else s[i:k + 1])
            i = n if k < 0 else k + 1
            continue
        if s.startswith("$(", i) or s.startswith("@(", i):
            flush()
            items.append(("op", "("))
            i += 2
            continue
        if s.startswith("@{", i):
            flush()
            items.append(("op", "{"))
            i += 2
            continue
        if c in "123456*" and not st["started"] and s[i + 1:i + 2] == ">":
            flush()
            op = "2>&1" if s.startswith(">&1", i + 1) else (c + ">>" if s.startswith(">>", i + 1) else c + ">")
            items.append(("op", "2>&1" if op.endswith("&1") else op))
            i += len(op) if not op.endswith("&1") else 4
            continue
        if c in "&|(){},<>":
            flush()
            two = s[i:i + 2]
            if two in ("&&", "||", ">>"):
                items.append(("op", two))
                i += 2
                continue
            items.append(("op", c))
            i += 1
            continue
        st["started"] = True
        buf.append(c)
        i += 1
    flush()
    return items


def _ps_subexpressions(text: str) -> list:
    out, i = [], 0
    while True:
        k = text.find("$(", i)
        if k < 0:
            return out
        inner, i = _ps_paren_end(text, k + 2)
        out.append(inner)


# ---- cmd.exe ------------------------------------------------------------------------------------------
def _cmd_items(s: str) -> list:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    items: list = []
    st = {"buf": [], "quoted": False, "started": False}

    def flush():
        if st["started"]:
            items.append(Word("".join(st["buf"]), st["quoted"]))
        st.update(buf=[], quoted=False, started=False)

    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t":
            flush()
            i += 1
            continue
        if c == "^":
            st["started"] = True
            if i + 1 < n and s[i + 1] != "\n":
                st["buf"].append(s[i + 1])
            i += 2
            continue
        if c == '"':
            st["started"] = st["quoted"] = True
            k = s.find('"', i + 1)
            st["buf"].append(s[i + 1:] if k < 0 else s[i + 1:k])
            i = n if k < 0 else k + 1
            continue
        if c in "12" and not st["started"] and s[i + 1:i + 2] == ">":
            flush()
            if s.startswith(">&", i + 1):
                items.append(("op", "2>&1"))
                i += 4
            else:
                items.append(("op", "2>"))
                i += 2
            continue
        if c in "&|()<>\n":
            flush()
            two = s[i:i + 2]
            if two in ("&&", "||", ">>"):
                items.append(("op", two))
                i += 2
                continue
            items.append(("op", c))
            i += 1
            continue
        st["started"] = True
        st["buf"].append(c)
        i += 1
    flush()
    return items


# ---- grouping -----------------------------------------------------------------------------------------
_OPENERS = {"(": ")", "{": "}"}


def commands(text: str, lang: str = "bash") -> list:
    """The simple commands in `text`, in order. Never raises on odd input: unbalanced quotes run to the end."""
    items = {"bash": _bash_items, "ps": _ps_items, "cmd": _cmd_items}[lang](text or "")
    out: list = []
    depth = 0
    cur = Command(0)
    role_next = None
    owners: list = []           # commands with a here-document still to come, in order
    for it in items:
        if isinstance(it, Word):
            if role_next:
                it.role = role_next
                role_next = None
            if it.role == "delim":
                owners.append(cur)
            cur.words.append(it)
            continue
        if it[0] == "heredoc":
            owner = owners.pop(0) if owners else (cur if cur.words else (out[-1] if out else cur))
            owner.heredocs.append((it[1], it[2]))
            continue
        op = it[1]
        if op in _REDIRS:
            role_next = "redir"
            continue
        if op in ("<<", "<<-"):
            role_next = "delim"
            continue
        if op == "<<<":
            role_next = "data"
            continue
        if op == "," and lang == "ps":
            continue
        if op == "&" and lang == "ps" and not cur.words:
            continue                # the call operator: the next word is the command
        cur.end = op
        if cur.words or cur.heredocs:
            out.append(cur)
        if op in _OPENERS and (lang != "bash" or op == "("):
            depth += 1
        elif op in (")", "}") and depth:
            depth -= 1
        cur = Command(depth)
    if cur.words or cur.heredocs:
        out.append(cur)
    return out
