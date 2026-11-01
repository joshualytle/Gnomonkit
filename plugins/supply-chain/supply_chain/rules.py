"""Deterministic risk rules over the ADDED lines of a change.

These decide; nothing else (and never the optional model) can raise or clear a rule hit. Each
rule is high-precision on purpose: a proxy, an HTTP client or a storage driver adds ordinary
network and environment code in most releases, so "calls the network" is not a rule here (it is
a *kind* of change, reported by kinds.py, which describes but does not flag). What is a rule:
piping a download into a shell, IP-literal and exfiltration endpoints, decode-then-execute,
large encoded blobs, reading credential stores, dumping the whole environment, install and build
hooks, and committed binaries.

Calibrated by tests/test_supply_chain_rules.py against planted-bad and clean-but-suspicious-looking
synthetic diffs; a rule that fires on a clean shape is a bug in the rule.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import scrub, text

MAX_LINE = 20000   # longer lines (minified bundles) are scanned on their first MAX_LINE characters


@dataclass(frozen=True)
class Rule:
    id: str
    sign: str            # network_calls | obfuscation | credential_access | env_access | new_binaries | install_hooks
    severity: str        # high | medium
    rx: "re.Pattern[str]"
    why: str
    code_only: bool = True      # skip docs, tests, fixtures, vendored code and lockfiles


@dataclass
class Hit:
    rule: str
    sign: str
    severity: str
    path: str
    line: "int | None"
    excerpt: str
    why: str

    def as_dict(self) -> dict:
        return dict(self.__dict__)


_EXFIL = (r"pastebin\.com|transfer\.sh|webhook\.site|requestbin|pipedream\.net|ngrok(?:-free)?\.(?:io|app)|"
          r"interact\.sh|oast\.(?:fun|live|me|online|pro|site)|burpcollaborator|discord(?:app)?\.com/api/webhooks|"
          r"api\.telegram\.org/bot|hookbin|beeceptor|canarytokens")

_CRED_FILES = (r"\.ssh[/\\]|\.aws[/\\]credentials|\.npmrc|\.pypirc|\.docker[/\\]config\.json|\.kube[/\\]config|"
               r"\.git-credentials|\.config[/\\]gh[/\\]hosts\.yml|\.netrc")

RULES = (
    Rule("pipe-to-shell", "network_calls", "high",
         re.compile(r"\b(?:curl|wget|iwr|invoke-webrequest)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b|"
                    r"\b(?:curl|wget)\b[^\n]{0,200}\|\s*python3?\b|"
                    r"\biex\s*\(\s*(?:new-object\s+net\.webclient|iwr|invoke-webrequest)", re.I),
         "downloads code and runs it in one step"),
    Rule("exfil-endpoint", "network_calls", "high", re.compile(r"\b(?:%s)" % _EXFIL, re.I),
         "talks to a paste, webhook or tunnelling service often used for exfiltration"),
    Rule("ip-literal-url", "network_calls", "medium",
         re.compile(r"\b(?:https?|wss?|ftp)://(?!(?:127\.|10\.|0\.0\.0\.0|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|"
                    r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|localhost|\[::1\]))\d{1,3}(?:\.\d{1,3}){3}", re.I),
         "hard-coded public IP address endpoint"),
    Rule("eval-decoded", "obfuscation", "high",
         re.compile(r"\b(?:eval|exec|Function|execSync|spawnSync)\s*\(\s*[^)\n]{0,80}"
                    r"(?:atob|b64decode|base64|fromCharCode|Buffer\.from\([^)]*['\"](?:base64|hex)['\"]|"
                    r"decompress|unhexlify|codecs\.decode|bytes\.fromhex)"),
         "decodes data and executes it"),
    Rule("marshal-loads", "obfuscation", "high",
         re.compile(r"\b(?:marshal\.loads|pickle\.loads)\s*\(\s*(?:base64|zlib|codecs|bytes\.fromhex)"),
         "deserialises encoded code"),
    Rule("encoded-blob", "obfuscation", "medium",
         re.compile(r"['\"`][A-Za-z0-9+/]{400,}={0,2}['\"`]|(?:\\x[0-9a-fA-F]{2}){60,}"),
         "large encoded string in source"),
    Rule("credential-store", "credential_access", "high",
         re.compile(r"(?:~|\$HOME|%USERPROFILE%|homedir\(\)|expanduser\([^)]*)[/\\'\"+, ]{0,6}(?:" + _CRED_FILES + r")|"
                    # A home directory joined with a credential path, e.g. filepath.Join(home, ".docker/config.json").
                    r"\b(?:home|homedir|home_dir|userhome|user_home|HOME)\b[^\n]{0,40}['\"][/\\]?(?:" + _CRED_FILES + r")|"
                    r"\.ssh[/\\](?:id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?\b|identity\b)|"
                    r"/var/run/secrets/kubernetes\.io/serviceaccount/token[^\n]{0,80}(?:https?://(?!kubernetes)|fetch|requests\.post|axios)|"
                    r"Local State[^\n]{0,40}(?:Chrome|Edge|Brave)|(?:metamask|exodus|electrum)[^\n]{0,40}(?:wallet|vault)", re.I),
         "reads a credential store or wallet"),
    Rule("env-dump", "env_access", "high",
         re.compile(r"JSON\.stringify\(\s*process\.env\s*\)|\bdict\(\s*os\.environ\s*\)|"
                    r"\bos\.environ\.copy\(\)[^\n]{0,80}(?:post|send|urlopen|requests)|"
                    r"\bprintenv\b[^\n]{0,60}\|\s*(?:curl|wget|nc)\b|\benv\s*\|\s*(?:curl|wget|nc)\b"),
         "collects the whole environment"),
    Rule("install-hook", "install_hooks", "high",
         re.compile(r"^\s*\"(?:preinstall|install|postinstall|prepare)\"\s*:\s*\"(?!husky|is-ci|npm run build|tsc|node-gyp rebuild\")"),
         "adds an npm install-time script", code_only=False),
    Rule("setup-cmdclass", "install_hooks", "medium",
         re.compile(r"\bcmdclass\s*=\s*\{[^}\n]*['\"](?:install|develop|egg_info|build_py)['\"]"),
         "overrides a setup.py install command"),
    # Medium, not high: a few packaging tools ship a legitimate .pth hook (setuptools' distutils shim).
    Rule("pth-exec", "install_hooks", "medium",
         re.compile(r"^\s*import\s+\S"),
         "a .pth file line that runs code every time Python starts", code_only=False),
    Rule("workflow-prt", "install_hooks", "medium",
         re.compile(r"^\s*pull_request_target\s*:?"),
         "adds a pull_request_target workflow trigger (runs fork code with secrets)", code_only=False),
)

BINARY_EXT = re.compile(r"(?i)\.(?:exe|dll|so(?:\.\d+)*|dylib|node|wasm|bin|jar|class|pyc|o|a|elf|msi|dmg|scr)$")
_NON_CODE = re.compile(
    r"(?i)(?:^|/)(?:tests?|testdata|test_data|__tests__|fixtures?|examples?|docs?|documentation|vendor|third_party|node_modules|"
    r"mocks?|deps|licenses?|\.changeset)/|\.(?:md|rst|txt|adoc|lock|sum|svg|snap|golden|po|pot|license|map)$|"
    r"(?:_test\.go|_test\.py|\.test\.[jt]sx?|\.spec\.[jt]sx?|Tests?\.cs|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|"
    r"go\.sum|Cargo\.lock|(?:^|/)(?:CHANGELOG|CHANGES|HISTORY|NEWS|AUTHORS|CONTRIBUTORS|README|COPYING|LICENSE|NOTICE)"
    # No extension, or a -MIT / _APACHE style suffix: history.js or news.py is code, not a document.
    r"(?:[-_][A-Za-z0-9.]+)?)$")
_BUILD_HOOK_PATH = re.compile(r"(?i)(?:^|/)(?:build\.rs|binding\.gyp|setup\.py|hook/build\.dart)$|\.pth$")


def non_code(path: str) -> bool:
    return bool(_NON_CODE.search(path))


def scan_lines(path: str, added, status: str = "modified") -> list:
    """Rules over one file's added lines. `added` is a list of (line number or None, text)."""
    hits = []
    if status == "added" and BINARY_EXT.search(path) and not non_code(path):
        hits.append(Hit("new-binary", "new_binaries", "medium", path, None, "", "adds a compiled binary"))
    if status == "added" and _BUILD_HOOK_PATH.search(path) and not non_code(path):
        hits.append(Hit("new-build-hook", "install_hooks", "medium", path, None, "",
                        "adds a file that runs at install or build time"))
    skip = non_code(path)
    is_pkg_json = path.endswith("package.json")
    is_pth = path.endswith(".pth")
    is_workflow = "/.github/workflows/" in "/" + path
    for lineno, line in added:
        line = line[:MAX_LINE]
        for r in RULES:
            if r.code_only and skip:
                continue
            if r.id == "install-hook" and not is_pkg_json:
                continue
            if r.id == "pth-exec" and not is_pth:
                continue
            if r.id == "workflow-prt" and not is_workflow:
                continue
            if r.rx.search(line):
                hits.append(Hit(r.id, r.sign, r.severity, path, lineno,
                                text.plain(scrub.clean(line.strip()), 160), r.why))
    return hits


def added_from_patch(patch: str) -> list:
    """(line number, text) for every '+' line of a unified-diff hunk set."""
    out = []
    lineno = 0
    for raw in (patch or "").splitlines():
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) - 1 if m else 0
            continue
        if raw.startswith("-"):
            continue
        lineno += 1
        if raw.startswith("+") and not raw.startswith("+++"):
            out.append((lineno, raw[1:]))
    return out


def scan_patch(path: str, patch, status: str = "modified") -> list:
    return scan_lines(path, added_from_patch(patch or ""), status)
