"""Remove credentials from history text before it is stored, and again before it is shown.

Standard library regular expressions only. Each match becomes `[REDACTED:<kind>]`; values are replaced,
never recorded. Built-in rules cover private keys, cloud and platform tokens, JWTs, credentials in
URLs, authorization headers, `password=`-style assignments (also inside escaped JSON, as in a
`curl -d "{\\"password\\":...}"` command), password and token flags (`--password X`, `-storepass X`),
`curl -u user:pass`, and `-p<password>` on database clients such as mysql.

What is kept: a bare git SHA or UUID in running text (no rule matches one), and a value that is
plainly not a secret, such as `${DB_PASS}`, `<your key>`, `changeme` or a number after a key that
is not a password. A value after a secret-named key, a bearer or a URL's user is redacted whatever
it looks like: a 40 or 64 character hex string there is a secret, not a commit.

Extra rules: a file of `kind<TAB>regex` lines (`#` starts a comment), at GNOMONKIT_SCRUB_RULES or
~/.gnomonkit/history/scrub-rules.txt. A rule that does not compile makes Scrubber.load() raise
ScrubConfigError, and the indexer then stores nothing: a rule the user wrote for a secret must never
be silently skipped.

The caller treats any exception from scrub() as "drop this record": a record is stored scrubbed or
not at all.
"""
from __future__ import annotations

import os
import re

# A quote, bare or escaped: `"`, `'`, `\"` (JSON inside a shell string), `\\\"`, or PowerShell's `` `" ``.
_Q = r"(?:\\{1,3}|`)?[\"']"
# The value after a key or flag. Quoted: everything up to the matching quote, spaces included, at least
# one character. Unquoted (or a quote that never closes on the line): a run of value characters, where
# a backslash belongs to the value unless it escapes a quote.
_VAL_QUOTED = r"(?P<q>" + _Q + r")(?P<v>(?:(?!(?P=q))(?:\\.|[^\n\\])){1,256}?)(?P=q)"
_VAL_BARE = r"(?:" + _Q + r")?(?P<v2>(?:[^\s\"'`,;&<>)\\]|\\(?!\\*[\"'`])){%d,256})"
_CRED_WORDS = (r"api[_-]?key|apikey|secret|access[_-]?key|account[_-]?key|auth[_-]?token|access[_-]?token|"
               r"refresh[_-]?token|client[_-]?secret|private[_-]?token|token|password|passwd|pwd|encryption[_-]?key")
_FLAG_WORDS = (r"password|passwd|pass|passin|passout|pwd|secret|token|api-?key|access-key|secret-key|"
               r"client-secret|auth-token|access-token|refresh-token|storepass|keypass")
_DB_CLIENTS = (r"mysql|mariadb|mysqldump|mysqladmin|mysqlimport|mysqlshow|mysqlcheck|mysqlpump|mysqlbinlog|"
               r"mysqlsh|mariadb-dump|mariadb-admin|mariadb-import|mariadb-check")
# A command's own arguments: the rest of the line up to the next pipe or command separator.
_SAME_COMMAND = r"[^\n|;&]{0,300}?"

# (kind, regex, group). group 0 replaces the whole match; an int replaces that group; "v" replaces the
# first of the groups v, v2 that took part in the match.
_RULES = (
    ("private-key", re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ ){0,3}PRIVATE KEY(?: BLOCK)?-----[\s\S]*?"
        r"(?:-----END (?:[A-Z0-9]+ ){0,3}PRIVATE KEY(?: BLOCK)?-----|\Z)"), 0),
    ("aws-access-key-id", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b"), 0),
    # 30 rather than the full 36 characters, so a token cut short in a log line is still caught
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,255}\b"), 0),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"), 0),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), 0),
    ("slack-token", re.compile(r"\b(?:xox[baprse]|xapp)-[A-Za-z0-9-]{10,}\b"), 0),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/+_-]{20,}"), 0),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), 0),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"), 0),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 0),
    ("stripe-key", re.compile(r"\b[rsp]k_(?:live|test)_[0-9A-Za-z]{16,}\b"), 0),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), 0),
    ("pypi-token", re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}\b"), 0),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), 0),
    # signed URLs: the signature is the credential
    ("url-signature", re.compile(
        r"(?i)[?&](?:sig|signature|x-amz-signature|x-goog-signature|x-amz-security-token)=([^&\s\"'#]{16,})"), 1),
    ("authorization-header", re.compile(
        r"(?i)\bauthorization(?:" + _Q + r")?\s*[:=]\s*(?:" + _Q + r")?(?:bearer|basic|token)\s+"
        r"(?P<v>[A-Za-z0-9._~+/=-]{8,})"), "v"),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{20,})"), 1),
    # user:password@ in any URL; the scheme and host stay so the text still says where it pointed. A
    # slash may be in the password, but host:port/path@ (a port, then a path holding an @) is not one.
    ("url-credentials", re.compile(
        r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@'\"]{1,128}:(?!\d{1,5}(?:/|$))([^\s@'\"]{1,256})@", re.I), 1),
    # curl -u user:pass, wget/httpie/xh likewise; the user stays, the password goes
    ("basic-auth-flag", re.compile(
        r"\b(?:curl|wget|https?|xh|xhs)(?:\.exe)?\b" + _SAME_COMMAND +
        r"\s(?:-[uU]\s*|--(?:proxy-)?user(?:\s+|=)|-a\s*|--auth(?:\s+|=))"
        r"(?:(?P<q>" + _Q + r")[^\s:\"'`]{0,128}:(?P<v>(?:(?!(?P=q))[^\n]){1,256}?)(?P=q)"
        r"|(?:" + _Q + r")?[^\s:\"'`]{0,128}:(?P<v2>[^\s\"'`]{1,256}))"), "v"),
    # mysql -pSECRET, mysqldump -p'SECRET' ...; a bare -p (prompt for it) and -P 3306 are left alone
    ("db-client-password", re.compile(
        r"\b(?:" + _DB_CLIENTS + r")(?:\.exe)?\b" + _SAME_COMMAND +
        r"\s-p(?:" + _Q + r")?(?P<v>[^\s\"'`]{1,256})"), "v"),
    ("sshpass-password", re.compile(
        r"\bsshpass\b" + _SAME_COMMAND + r"\s-p\s*(?:" + _Q + r")?(?P<v>[^\s\"'`]{1,256})"), "v"),
    # --password X, --db-password=X, -storepass X, --token X; not --password-stdin or --token-file
    ("password-flag", re.compile(
        r"(?i)(?<![\w-])(?P<k>--?(?!no-)(?:[a-z0-9]+-)*(?:" + _FLAG_WORDS + r"))(?:=|\s+)(?!-)"
        r"(?:" + _VAL_QUOTED + r"|" + (_VAL_BARE % 3) + r")"), "v"),
    ("credential-assignment", re.compile(
        r"(?i)(?P<k>\b[A-Z0-9_]*(?:" + _CRED_WORDS + r")[A-Z0-9_]*)\b(?:" + _Q + r")?\s*[:=]\s*"
        r"(?:" + _VAL_QUOTED + r"|" + (_VAL_BARE % 6) + r")"), "v"),
)

# Values that are plainly not secrets: placeholders, booleans, environment and command references.
_ALLOW = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$|^\$env:[A-Za-z_][A-Za-z0-9_]*$|^\$\([^()]*\)$|"
                    r"^<[^>]*>$|^\[REDACTED:[a-z0-9-]+\]$|^%[A-Za-z_]+%$|"
                    r"^(?:true|false|null|none|changeme|example|redacted|placeholder|x{3,}|\*+|\.{3,})$", re.I)
# A count (max_tokens=100000, token_limit: 8k) is kept, except where the key or flag names a password.
_NUMBER = re.compile(r"^\d{1,12}[km]?$", re.I)
_PASSWORD_KEY = re.compile(r"(?i)pass|pwd|pin")
_PASSWORD_KINDS = frozenset(("db-client-password", "sshpass-password", "basic-auth-flag", "url-credentials"))
_KIND = re.compile(r"[a-z0-9][a-z0-9-]{0,40}$")


class ScrubConfigError(ValueError):
    """The extra rules file can't be used. Nothing is indexed until it is fixed."""


def rules_path() -> str:
    raw = (os.environ.get("GNOMONKIT_SCRUB_RULES") or "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    from . import _common as C
    return os.path.join(C.gnomonkit_home(), "history", "scrub-rules.txt")


def load_extra(path: str | None = None) -> list:
    """[(kind, compiled regex)] from the extra rules file; [] when there is none. Raises ScrubConfigError."""
    path = path or rules_path()
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as e:
        raise ScrubConfigError("can't read the extra scrub rules in %s (%s)" % (path, e)) from None
    out = []
    for n, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        kind, sep, rx = line.partition("\t")
        kind = kind.strip().lower()
        if not sep or not rx.strip() or not _KIND.match(kind):
            raise ScrubConfigError("%s line %d: expected kind<TAB>regex" % (path, n))
        try:
            out.append((kind, re.compile(rx.strip())))
        except re.error as e:
            raise ScrubConfigError("%s line %d: the regex does not compile (%s)" % (path, n, e)) from None
    return out


class Scrubber:
    """Callable: scrubber(text) -> text with every match replaced. Count of replacements in .count."""

    def __init__(self, extra=()):
        self.rules = list(_RULES) + [(k, rx, 0) for k, rx in extra]
        self.count = 0

    @classmethod
    def load(cls, path: str | None = None) -> "Scrubber":
        return cls(load_extra(path))

    def __call__(self, text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("scrub needs text")
        for kind, rx, group in self.rules:
            text = rx.sub(lambda m, kind=kind, group=group: self._repl(m, kind, group), text)
        return text

    def _repl(self, m, kind, group):
        if isinstance(group, str):
            group = next((n for n in sorted(m.re.groupindex) if n.startswith(group) and m.group(n) is not None),
                         None)
            if group is None:
                return m.group(0)
        elif group > (m.re.groups or 0):
            group = 0
        value = m.group(group)
        if value is None:
            return m.group(0)
        if group and _kept(value.strip("\"'"), kind, m):
            return m.group(0)
        self.count += 1
        tag = "[REDACTED:%s]" % kind
        if group == 0:
            return tag
        s, e = m.span(group)
        base = m.start(0)
        whole = m.group(0)
        return whole[:s - base] + tag + whole[e - base:]


def _kept(value: str, kind: str, m) -> bool:
    """True when a captured value is plainly not a secret and the match should stay as it was."""
    if not value or _ALLOW.match(value):
        return True
    if _NUMBER.match(value):
        key = m.group("k") if "k" in m.re.groupindex else ""
        return kind not in _PASSWORD_KINDS and not (key and _PASSWORD_KEY.search(key))
    return False


def scrub(text: str) -> str:
    """Built-in rules plus the extra rules file. Raises ScrubConfigError when that file is broken."""
    return Scrubber.load()(text)
