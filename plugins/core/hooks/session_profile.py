#!/usr/bin/env python3
"""Inject the user's profile at session start (personal + machine), and offer first-run setup.

See docs/PROFILES.md and docs/ONBOARDING.md. Two layers:
  personal  -- how the user likes to work. A TOML file found by, in order: the plugin option
               `personal_profile` (env CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE), GNOMONKIT_PERSONAL_PROFILE,
               then ~/.gnomonkit/personal.toml. No overlay is needed. `gnomonkit profile set` writes it.
  machine   -- what THIS machine is. ~/.gnomonkit/machine.toml (GNOMONKIT_HOME overrides the folder).
               Never synced. Created once per machine.

Precedence: what the user says in-session > machine > personal > defaults.

First session on a machine (no machine file): detect OS, shells and GPU, and tell the agent to ask
the user only what could not be detected (which shell, when there are several, and whether this is a
personal or a work machine), then save it with:
    python <this file> --write-machine --shell powershell --kind work
The kind is kept in the machine profile only, because one person can have both kinds of machine; a
machine profile without it gets the one question once. "unknown" (rather not say) is saved like an
answer, so it isn't asked again, and is treated as work. Sessions nobody is watching
(GNOMONKIT_NO_ONBOARDING) get no machine questions.
Later sessions: re-detect the cheap facts; if they no longer match the saved file, tell the agent to
ask before updating. Cloud/web sessions (CLAUDE_CODE_REMOTE set) skip the machine file.

First-run setup: on a `startup` session start (never resume, clear or compact), when no personal
profile exists and ~/.gnomonkit/onboarding.json says neither done nor skipped, the block carries a
notice telling the agent to offer setup (the core `onboarding` skill). That happens at most
MAX_OFFERS times; after that the block carries one line saying setup can be run any time. Never in
sessions nobody is watching: GNOMONKIT_NO_ONBOARDING (session-dispatch sets it) or CLAUDE_CODE_REMOTE.
Until the user acknowledges the disclaimer (`[acknowledged] disclaimer` in the personal profile),
the block carries one line pointing at it.

The plugin option `personal_profile` reaches this hook but not the agent's shell, so the hook records
it in onboarding.json, where `gnomonkit profile set` finds it.

SessionStart hook contract: prints JSON with hookSpecificOutput.additionalContext, exits 0, never
blocks. Any failure prints nothing and exits 0: a profile hook that breaks session start has done
more harm than any profile is worth.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# The shared helpers live beside hooks/ in the core plugin. If they can't be imported the profile
# block still appears, without first-run setup (fail open).
CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    if CORE not in sys.path:
        sys.path.append(CORE)
    from gnomonkit_cmds import _common as C
except Exception:  # noqa: BLE001
    C = None

# What kind of machine this is, kept in the machine profile only. "unknown" (rather not say) counts as work.
KINDS = C.MACHINE_KINDS if C is not None else ("personal", "work", "unknown")

# A hook's children must never open a console window on Windows (a detached parent has none to share).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SHELLS_BY_OS = {
    "windows": [("powershell", ("pwsh", "powershell")), ("bash", ("bash",)), ("cmd", ("cmd",))],
    "macos": [("zsh", ("zsh",)), ("bash", ("bash",)), ("fish", ("fish",)), ("powershell", ("pwsh",))],
    "linux": [("bash", ("bash",)), ("zsh", ("zsh",)), ("fish", ("fish",)), ("powershell", ("pwsh",))],
}


def gnomonkit_home() -> str:
    if C is not None:
        return C.gnomonkit_home()
    return os.environ.get("GNOMONKIT_HOME") or os.path.join(os.path.expanduser("~"), ".gnomonkit")


def machine_path() -> str:
    return os.path.join(gnomonkit_home(), "machine.toml")


def personal_path() -> str:
    """The personal profile this session uses: the plugin option, GNOMONKIT_PERSONAL_PROFILE, then
    ~/.gnomonkit/personal.toml."""
    for name in ("CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE", "GNOMONKIT_PERSONAL_PROFILE"):
        p = (os.environ.get(name) or "").strip()
        if p:
            return os.path.expanduser(p)
    return os.path.join(gnomonkit_home(), "personal.toml")


# ---- TOML ------------------------------------------------------------------------------------
def parse_toml(text: str) -> dict:
    """The shared reader (tomllib, or its fallback parser). Without the shared helpers, a minimal
    reader for sections, key = "str" | number | true/false | ["str", ...]."""
    if C is not None:
        return C.parse_toml(text)
    try:
        import tomllib  # Python 3.11+
        return tomllib.loads(text)
    except ModuleNotFoundError:
        pass
    data, cur = {}, None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("[") else raw.strip()
        if not line:
            continue
        m = re.match(r"^\[([A-Za-z0-9_.-]+)\]$", line)
        if m:
            cur = data.setdefault(m.group(1), {})
            continue
        m = re.match(r'^([A-Za-z0-9_-]+)\s*=\s*(.+)$', line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v.startswith("["):
            val = re.findall(r'"([^"]*)"', v)
        elif v.startswith('"'):
            val = v.strip('"')
        elif v in ("true", "false"):
            val = v == "true"
        else:
            try:
                val = int(v)
            except ValueError:
                val = v
        (cur if cur is not None else data)[k] = val
    return data


def read_toml(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return parse_toml(fh.read())
    except (OSError, ValueError, UnicodeDecodeError):
        return None


# ---- detection -------------------------------------------------------------------------------
def detect_os() -> str:
    # sys.platform, not platform.system(): on Windows the latter asks WMI and costs ~0.1 s per
    # session start, for an answer the interpreter already knows.
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def detect_shells(os_name: str) -> list[str]:
    found = []
    for name, exes in SHELLS_BY_OS.get(os_name, []):
        if any(shutil.which(e) for e in exes):
            found.append(name)
    return found


def detect_gpu() -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return "none"
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3,
                             creationflags=NO_WINDOW).stdout.strip().splitlines()
        if out:
            name, mem = [x.strip() for x in out[0].split(",")[:2]]
            return "%s (%d GB)" % (name, round(int(mem) / 1024))
    except Exception:
        pass
    return "nvidia (unknown)"


# ---- output ----------------------------------------------------------------------------------
# The newer keys, as one line each when set. Values outside these choices are left out.
WORDING = {
    ("working", "ask_before_acting"): ("Ask before acting", {
        "always": "always, before anything that changes files or state",
        "risky": "before risky or irreversible steps; go ahead with the rest",
        "rarely": "rarely: go ahead, except for the always-confirm list"}),
    ("delivery", "prs"): ("Pull requests", {
        "always": "always (never push straight to the default branch)",
        "direct-ok": "optional (pushing straight to the default branch is fine)"}),
    ("delivery", "test_style"): ("Tests", {
        "test-first": "write the failing test first", "tests-after": "after the code",
        "match-repo": "follow the repo's own practice"}),
    ("communication", "explanation_level"): ("Explanations", {
        "brief": "brief", "standard": "standard", "teach": "teach (explain the why and the terms)"}),
    ("working", "subagents"): ("Subagents", {
        "sparing": "sparing (only for clearly parallel or very large work)", "normal": "normal",
        "liberal": "liberal (delegate searches and parallel work)"}),
    ("memory", "lessons"): ("Lessons from past sessions", {
        "auto": "auto (on unless MemPalace is set up here)", "on": "on", "off": "off"}),
    ("memory", "history"): ("Gnomonkit's own history index", {
        "auto": "auto (on unless MemPalace is set up here)", "on": "on", "off": "off"}),
}


def _table(personal: dict, name: str) -> dict:
    t = personal.get(name)
    return t if isinstance(t, dict) else {}


def merge_line(delivery: dict) -> str | None:
    """The Merging row from [delivery] merge and merge_ask_for; None when merge is unset or not a choice (the
    skills then take the default, agent). ship-through-prs and merge-guidance read this row."""
    how = delivery.get("merge")
    if how == "agent":
        return "merge your own pull request once it is green; don't ask"
    if how == "ask":
        return "ask the user before merging any pull request, even a green one"
    if how != "ask-for":
        return None
    raw = delivery.get("merge_ask_for")
    items = [x.strip() for x in raw if isinstance(x, str) and x.strip() and not re.search(r"[\x00-\x1f\x7f]", x)
             ][:20] if isinstance(raw, list) else []
    if not items:
        return "merge your own pull request once it is green; don't ask (ask-for lists nothing)"
    return ("merge your own pull request once it is green, but ask the user first when it changes a file "
            "matching, or carries a label in: %s" % ", ".join(x[:120] for x in items))


def describe(personal: dict, machine: dict) -> list[str]:
    rows = []
    if machine.get("os"):
        rows.append("OS: %s" % machine["os"])
    if machine.get("shell"):
        rows.append("Write shell commands for: %s" % machine["shell"])
    if machine.get("gpu"):
        rows.append("GPU: %s" % machine["gpu"])
    if machine.get("kind") in KINDS:
        rows.append("Machine kind: %s" % ("unknown (treat it as a work machine)" if machine["kind"] == "unknown"
                                          else machine["kind"]))
    c = _table(personal, "communication")
    if c.get("decisions"):
        rows.append("Decisions: %s" % ("numbered list" if c["decisions"] == "numbered" else c["decisions"]))
    if c.get("reply_length"):
        rows.append("Reply length: %s" % c["reply_length"])
    if _table(personal, "working").get("remote") == "yes":
        rows.append("Remote work: yes (often on the phone: sessions start with Remote Control, a dispatched "
                    "primary stays running, and every step they must take gets a command, not a tab to open)")
    for (table, key), (label, words) in WORDING.items():
        v = _table(personal, table).get(key)
        if isinstance(v, str) and v in words:
            rows.append("%s: %s" % (label, words[v]))
    style = _table(personal, "code").get("style")
    if isinstance(style, str) and style.strip() and len(style) <= 200 and not re.search(r"[\x00-\x1f\x7f]", style):
        rows.append("Code style: %s" % style.strip())
    d = _table(personal, "delivery")
    if d.get("ci"):
        ci = d["ci"]
        if ci == "self-hosted" and d.get("ci_runner_label"):
            ci += " (runner label %s)" % d["ci_runner_label"]
        if ci == "hosted" and d.get("hosted_minutes_budget"):
            ci += " (budget %s minutes/month: conserve)" % d["hosted_minutes_budget"]
        rows.append("CI: %s" % ci)
    merging = merge_line(d)
    if merging:
        rows.append("Merging: %s" % merging)
    if d.get("commit_email") == "noreply":
        rows.append("Commits: use the platform's noreply address, never a personal e-mail")
    a = _table(personal, "alerts")
    if a.get("destination"):
        rows.append("Alerts go to: %s%s" % (a["destination"], " (issues only)" if a.get("issues_only") else ""))
    s = _table(personal, "safety")
    if s.get("always_confirm") and isinstance(s["always_confirm"], list):
        rows.append("Always confirm before: %s" % ", ".join(str(x) for x in s["always_confirm"]))
    return rows


def machine_questions(machine: dict, shells: list) -> tuple:
    """(what to ask the user, the --write-machine flags for the answers) for what the machine profile
    lacks: the shell when several are installed, and the machine's kind. An answer of "unknown" (they
    would rather not say) is saved too, so the kind is asked only once per machine."""
    asks, flags = [], []
    if not machine.get("shell") and len(shells) > 1:
        asks.append("which shell commands should be written for (found: %s)" % ", ".join(shells))
        flags.append("--shell <their answer>")
    if machine.get("kind") not in KINDS:
        asks.append('whether this is a personal or a work machine (if they would rather not say, save "unknown", '
                    "which is treated as work)")
        flags.append("--kind <%s>" % "|".join(KINDS))
    return asks, " ".join(flags)


def build_context(personal: dict | None, machine: dict | None, detected: dict, remote: bool,
                  hook_path: str, extra_lines=(), extra_notes=(), ask_user: bool = True) -> str:
    lines = ["USER PROFILE (Gnomonkit). Apply these unless the user says otherwise in this session."]
    notes = []
    # Name the interpreter that is running this hook: a bare `python` is missing on python3-only systems.
    py = os.path.splitext(os.path.basename(sys.executable or ""))[0] or "python"
    if remote:
        machine = {"os": detected["os"], "shell": (detected["shells"] or [""])[0], "gpu": detected["gpu"]}
        notes.append("Cloud/web session: machine facts are detected here, not read from the user's machine.")
    elif machine is None:
        shells = detected["shells"]
        machine = {"os": detected["os"], "gpu": detected["gpu"]}
        if len(shells) == 1:
            machine["shell"] = shells[0]
        asks, flags = machine_questions({}, shells)
        if ask_user:   # never in a session nobody is watching
            notes.append(
                "NO MACHINE PROFILE YET on this machine. Detected: OS %s, shells %s, GPU %s. Ask the user, in one "
                "message, %s. Then save it with:\n"
                "  %s \"%s\" --write-machine %s\n"
                "This file stays on this machine and is never synced."
                % (detected["os"], ", ".join(shells) or "none found", detected["gpu"], " and ".join(asks),
                   py, hook_path, flags))
    else:
        drift = []
        if machine.get("os") and machine["os"] != detected["os"]:
            drift.append("saved OS %s, detected %s" % (machine["os"], detected["os"]))
        if machine.get("shell") and machine["shell"] not in detected["shells"]:
            drift.append("saved shell %s is not installed here" % machine["shell"])
        if drift:
            notes.append("MACHINE PROFILE OUT OF DATE (%s). Ask the user before updating it with:\n"
                         "  %s \"%s\" --write-machine --shell <shell>" % ("; ".join(drift), py, hook_path))
        asks, flags = machine_questions(machine, detected["shells"])
        if asks and ask_user:
            notes.append(("MACHINE PROFILE INCOMPLETE on this machine. Ask the user, once, %s. Then save it with:\n"
                          "  %s \"%s\" --write-machine %s"
                          % (" and ".join(asks), py, hook_path, flags))
                         + ("\nThe machine's kind is kept only in its machine profile: one person can have both kinds."
                            if "--kind" in flags else ""))
    rows = describe(personal or {}, machine or {})
    lines += ["  - " + r for r in rows]
    if personal is None:
        lines.append("  (no personal profile configured; using defaults)")
    lines += ["  " + x for x in extra_lines]
    notes += list(extra_notes)
    return ("\n".join(lines) + "\n\n" + "\n\n".join(notes)).rstrip()


# ---- first-run setup and the disclaimer ------------------------------------------------------
def first_run(source, profile_exists: bool, remote: bool) -> tuple:
    """(notice for the agent or None, one profile-block line or None). Also records the plugin option
    personal_profile for `gnomonkit profile set`. Writes onboarding.json only when something changed."""
    if C is None:
        return None, None
    st = C.read_onboarding()
    changed = False
    option = (os.environ.get("CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE") or "").strip()
    if option and st.get("profile_option") != option:
        st["profile_option"], changed = option, True
    elif not option and "profile_option" in st:
        del st["profile_option"]
        changed = True
    notice = line = None
    quiet = (remote or os.environ.get("GNOMONKIT_NO_ONBOARDING") or profile_exists
             or st.get("state") in ("done", "skipped"))
    if not quiet:
        try:
            offers = max(0, int(st.get("offers") or 0))
        except (TypeError, ValueError):
            offers = 0
        if offers >= C.MAX_OFFERS:
            line = 'Gnomonkit setup: run setup any time: ask for "Gnomonkit setup".'
        elif source == "startup":
            st["offers"], changed = offers + 1, True
            notice = (
                "FIRST-RUN SETUP (Gnomonkit): this user has no personal profile yet. In your first reply, after "
                "answering whatever they asked, offer the one-time setup in a sentence or two: about two "
                "minutes, every question has a default, and it can be skipped. If they agree, follow the core "
                "`onboarding` skill. If they decline, run this so it isn't offered again:\n"
                "  %s\n"
                "(Offer %d of %d. Do not run the setup or save anything without their answer.)"
                % (C.command_line("setup", "skipped"), offers + 1, C.MAX_OFFERS))
    if changed:
        try:
            C.write_onboarding(st)
        except OSError:
            pass
    return notice, line


def disclaimer_line(personal: dict | None) -> str | None:
    if C is None:
        return None
    if str(C.lookup(personal, "acknowledged.disclaimer")) == C.DISCLAIMER_VERSION:
        return None
    return "Gnomonkit is provided as is: see DISCLAIMER.md (%s)." % C.DISCLAIMER_URL


def write_machine(shell: str | None, kind: str | None = None) -> int:
    """Save the machine profile. A value not given keeps the saved one; with no saved shell, the only
    shell found is used, and with several, --shell is needed unless only the kind is being saved."""
    os_name = detect_os()
    shells = detect_shells(os_name)
    data = (C.read_toml(machine_path()) if C is not None else read_toml(machine_path())) or {}
    if kind is not None and kind not in KINDS:
        print("--kind must be one of: %s" % ", ".join(KINDS), file=sys.stderr)
        return 2
    if shell is None:
        if data.get("shell"):
            shell = data["shell"]
        elif len(shells) == 1:
            shell = shells[0]
        elif kind is None:
            print("Pass --shell (one of: %s)" % ", ".join(shells), file=sys.stderr)
            return 2
    elif shells and shell not in shells:
        print("Shell %r not found on this machine (found: %s)" % (shell, ", ".join(shells)), file=sys.stderr)
        return 2
    header = "Gnomonkit machine profile. Local to this machine; never synced. See docs/PROFILES.md."
    data.update(os=os_name, gpu=detect_gpu())
    if shell:
        data["shell"] = shell
    if kind:
        data["kind"] = kind
    if C is not None:   # keys this file doesn't set are kept
        C.atomic_write(machine_path(), C.dumps(data, [("", ["os", "shell", "kind", "gpu"])], header))
    else:
        os.makedirs(gnomonkit_home(), exist_ok=True)
        with open(machine_path(), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# %s\n" % header + "".join('%s = "%s"\n' % (k, data[k]) for k in ("os", "shell", "kind", "gpu")
                                                    if isinstance(data.get(k), str)))
    print("saved %s (os=%s, shell=%s, kind=%s)" % (machine_path(), os_name, data.get("shell") or "not set",
                                                   data.get("kind") or "not set"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--write-machine", action="store_true")
    ap.add_argument("--shell")
    ap.add_argument("--kind", help="personal, work or unknown (rather not say; treated as work)")
    a, _ = ap.parse_known_args()
    if a.write_machine:
        return write_machine(a.shell, kind=a.kind)

    try:
        try:
            event = json.load(sys.stdin)
        except Exception:
            event = {}
        source = event.get("source") if isinstance(event, dict) else None
        os_name = detect_os()
        remote = bool(os.environ.get("CLAUDE_CODE_REMOTE"))
        machine = None if remote else read_toml(machine_path())
        # The GPU is probed (an nvidia-smi spawn, a few hundred ms) only when no saved machine
        # file records it: --write-machine caches it there, and a GPU swap is rare enough that
        # re-saving the profile is how it gets picked up.
        gpu = machine["gpu"] if machine and machine.get("gpu") else detect_gpu()
        detected = {"os": os_name, "shells": detect_shells(os_name), "gpu": gpu}
        ppath = personal_path()
        exists = os.path.isfile(ppath)
        personal = read_toml(ppath) if exists else None
        extra_lines, extra_notes = [], []
        if exists and personal is None:
            extra_lines.append("(the personal profile %s can't be read, so defaults apply; `%s` says what is "
                               "wrong)" % (ppath, C.command_line("profile", "show") if C else "gnomonkit profile show"))
        try:
            notice, line = first_run(source, exists, remote)
            if line:
                extra_lines.append(line)
            if notice:
                extra_notes.append(notice)
            ack = disclaimer_line(personal)
            if ack:
                extra_lines.append(ack)
        except Exception:  # noqa: BLE001 - setup is an extra; the profile block still goes out
            pass
        ctx = build_context(personal, machine, detected, remote, os.path.abspath(__file__),
                            extra_lines, extra_notes, ask_user=not os.environ.get("GNOMONKIT_NO_ONBOARDING"))
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
