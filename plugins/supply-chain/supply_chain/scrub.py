"""Remove credentials from text before it reaches a report or the optional local model.

Package contents are public, but a release can still ship a test key or a token someone pasted
into a fixture, and a report should not repeat it. Values are replaced, never recorded.
"""
from __future__ import annotations

import re

_RULES = (
    ("private-key", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"
        r"[A-Za-z0-9+/=\r\n\\ ]{40,}?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|$)"), 0),
    ("aws-access-key-id", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b"), 0),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"), 0),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"), 0),
    ("slack-token", re.compile(r"\bxox[baprse]-[A-Za-z0-9-]{10,}\b"), 0),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/+_-]{20,}"), 0),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), 0),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"), 0),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), 0),
    ("pypi-token", re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}\b"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), 0),
    ("authorization-header", re.compile(
        r"(?i)\bauthorization\s*[:=]\s*[\"']?(?:bearer|basic|token)\s+([A-Za-z0-9._~+/=-]{16,})"), 1),
    ("connection-string", re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|s3)://[^\s:'\"/@]{1,128}:([^\s'\"@]{1,256})@"), 1),
    ("credential-assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret|"
        r"private[_-]?token|password|passwd|encryption[_-]?key)\b\s*[:=]\s*[\"']?([A-Za-z0-9._~+/=-]{12,256})"), 1),
)

# Public identifiers that look like secrets. Keeping them is what makes a digest useful.
_ALLOW = re.compile(r"^(?:sha256:)?(?:[a-f0-9]{7,8}|[a-f0-9]{40}|[a-f0-9]{64})$|^\$\{[^}]+\}$|^<[^>]+>$|"
                    r"^(?:true|false|null|none|changeme|example|redacted|x{3,}|\*+)$", re.I)


def scrub(text: str):
    """Return (clean text, number of values removed)."""
    n = 0
    for kind, rx, group in _RULES:
        def repl(m, kind=kind, group=group):
            nonlocal n
            value = m.group(group)
            if _ALLOW.match(value.strip("\"'")):
                return m.group(0)
            n += 1
            if group == 0:
                return "[REDACTED:%s]" % kind
            s, e = m.span(group)
            base = m.start(0)
            whole = m.group(0)
            return whole[:s - base] + "[REDACTED:%s]" % kind + whole[e - base:]
        text = rx.sub(repl, text)
    return text, n


def clean(text: str) -> str:
    return scrub(text)[0]
