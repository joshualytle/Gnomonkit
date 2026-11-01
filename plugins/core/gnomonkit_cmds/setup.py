"""gnomonkit setup: where first-run setup stands, recording how it ended, and the terminal-only part.

    gnomonkit setup [status]   what is set up and what is not
    gnomonkit setup done       the user went through setup: the first-run offer stops
    gnomonkit setup skipped    the user declined it: the offer stops ("Gnomonkit setup" restarts it any time)
    gnomonkit setup security   terminal only: the settings that are permissions, which an agent may not set
    gnomonkit setup remote on|off|status [--project DIR ...]
                               for someone who works remotely or from the phone a lot: turns Claude Code's
                               remoteControlAtStartup on (user settings, CLAUDE_CONFIG_DIR honoured, the
                               old file kept as .bak) and records working.remote in the personal profile,
                               then prints what they must do themselves, per project: the grants that let
                               a project's sessions start, hand off to and stop their own, and folder trust

The state lives in ~/.gnomonkit/onboarding.json. The conversation itself is the core `onboarding` skill.
`status` also gives this machine's kind and its memory backend: on a personal machine where MemPalace
isn't set up it recommends it (what it is, licence, link, what can leave the machine; Gnomonkit never
installs it). On a work machine, or one whose kind is unknown, MemPalace isn't mentioned.

`setup security` refuses to run unless stdin and stdout are a terminal (exit 2). Its sections come from
other modules in this package: each one that defines `security_setup(args) -> int` runs, in
`SECURITY_ORDER` order, and one that defines `security_arguments(parser)` may add its own flags. With
no such module installed it says what is coming and changes nothing.
"""
from __future__ import annotations

import json
import os
import types

from . import _common as C

ACTIONS = ("status", "done", "skipped", "security", "remote")
REMOTE_VALUES = ("on", "off", "status")
REMOTE_SETTING = "remoteControlAtStartup"
# What a person who dispatches sessions from a project must set themselves, in a terminal, for each
# project: an agent may not grant itself the right to start, hand off to or stop sessions, or trust a folder.
PROJECT_CHECKLIST = """For {project}, in a terminal (an agent may not run these for you):
  1. Let its sessions start, hand off to and stop sessions of this same project:
       {prog} grants add --from "{project}" --to "{project}" --actions spawn,handoff,stop
  2. Trust the folder, so `claude --bg` accepts it: run `claude` once in {project} and accept the
     trust prompt; or, when it sits under a root you set to `trusted` in `{prog} setup security`:
       {prog} trust sync
"""
REMOTE_EXPLAINED = (
    "Remote Control connects a session to your phone and to claude.ai/code. Claude Code's "
    "remoteControlAtStartup setting turns it on for every interactive session you open; the sessions "
    "Gnomonkit starts for you in the background read the same setting and start with it too, and a "
    "primary started that way is left running, since the phone lists only running sessions."
)
COMING = ("Nothing to set here yet. The permissions an agent may not choose for you, which projects a session "
          "may start or steer sessions in (orchestration grants) and how far each folder is trusted, arrive in "
          "a coming release, and this command will ask about them then. Nothing was changed.")


# What first-run setup says about MemPalace, on a personal machine only. Facts from the public project's
# README and LICENSE; Gnomonkit never installs it.
MEMPALACE_ABOUT = (
    "What it is: an open-source memory for AI agents. It keeps your past conversations as verbatim text "
    "and finds them again with semantic search; Claude Code reaches it through an MCP server.",
    "Licence: MIT.",
    "Project: %s. Its README has the install and verify steps. Install it only from there or PyPI: the "
    "README warns of impostor sites." % C.MEMPALACE_URL,
    "What can leave this machine: its README says nothing does unless you opt in. The opt-ins it lists are "
    "a remote embedding endpoint, a storage backend on a server (Qdrant, pgvector or a Milvus server), a "
    "shared hub other machines connect to, and release checks that contact PyPI. It also downloads its "
    "embedding model the first time it needs one.",
    "Gnomonkit never installs it. It is optional: without it, Gnomonkit's own local history index and "
    "lessons are used. Once it is set up they turn off, unless you turn them on (memory.history, "
    "memory.lessons).",
)
OWN_MEMORY = (("memory.history", "GNOMONKIT_HISTORY"), ("memory.lessons", "GNOMONKIT_LESSONS"))


def own_memory_line() -> str | None:
    """With MemPalace set up here: which of Gnomonkit's own history index and lessons auto turned off,
    and how to have them as well. None when auto turned neither off."""
    off = [key for key, env in OWN_MEMORY if C.memory_feature(key, env) == (False, "auto, and MemPalace is set up here")]
    if not off:
        return None
    return ("Gnomonkit's own %s: off while MemPalace is set up (auto). To run %s alongside it: %s"
            % (", ".join(off), "them" if len(off) > 1 else "it",
               C.command_line("profile", "set", *("%s=on" % k for k in off))))


def memory_lines(kind: str | None, status: dict | None = None) -> list:
    """The memory backend line of `setup status` (and what the onboarding skill passes on). MemPalace is
    mentioned only on a personal machine; on a work machine, or one whose kind is unknown or not set, the
    built-in local history index is the default. Where MemPalace is set up, a line says which of
    Gnomonkit's own history index and lessons are off because of it (memory.history / memory.lessons auto)."""
    if kind != "personal":
        return ["the built-in local history index"]
    status = status if status is not None else C.mempalace_status(refresh=True)
    found = [w for w in status.get("where", []) if not w.startswith("on PATH")]
    own = own_memory_line() if status.get("mcp") or status.get("installed") else None
    if status.get("mcp"):
        return ["MemPalace, set up here (%s%s)" % (found[0] if found else "MCP server registered",
                                                    ", and %d more" % (len(found) - 1) if len(found) > 1 else "")
                ] + ([own] if own else [])
    if status.get("installed"):
        head = ("MemPalace is installed (%s), but Claude Code has no MCP server for it yet; its README covers "
                "connecting it." % (status.get("where") or ["on PATH"])[0])
        return [head] + ([own] if own else []) + list(MEMPALACE_ABOUT)
    return ["recommended on a personal machine: MemPalace (not set up here)."] + list(MEMPALACE_ABOUT)


def _kind_line() -> str:
    kind = C.machine_kind()
    if kind is None:
        return ("not set: the next session start asks, or run %s"
                % C.command_line("profile", "set", "machine.kind=<personal|work|unknown>"))
    return "unknown (treated as a work machine)" if kind == "unknown" else kind


def _security_modules() -> list:
    out = []
    for name, mod, _err in C.command_modules():
        if mod is not None and callable(getattr(mod, "security_setup", None)):
            out.append((getattr(mod, "SECURITY_ORDER", 50), name, mod))
    return sorted(out, key=lambda t: (t[0], t[1]))


def register(sub) -> None:
    p = sub.add_parser("setup", help="first-run setup: status, done, skipped, security (terminal only)",
                       description=__doc__.split("\n\n")[0])
    p.add_argument("action", nargs="?", default="status", choices=ACTIONS,
                   help="status (default), done, skipped, security (terminal only), or remote on|off|status")
    p.add_argument("value", nargs="?", choices=REMOTE_VALUES, help="for remote: on, off or status (default)")
    p.add_argument("--project", action="append", default=[], metavar="DIR",
                   help="for remote: a project you dispatch sessions from; prints its checklist (repeat for more)")
    for _order, _name, mod in _security_modules():
        add = getattr(mod, "security_arguments", None)
        if callable(add):
            add(p)
    p.set_defaults(func=run)


def _launcher_line() -> str:
    try:
        from . import launcher
        d = launcher.launcher_dir()
        if not launcher.installed(d):
            return "not installed (optional): %s" % C.command_line("install-launcher")
        return "installed in %s (%s)" % (d, "on PATH" if launcher.on_path(d) else "not on PATH yet")
    except Exception:  # noqa: BLE001 - status is informational
        return "unknown"


def status() -> int:
    st = C.read_onboarding()
    path, source = C.personal_profile_path()
    exists = os.path.isfile(path)
    state = st.get("state")
    if state in ("done", "skipped"):
        line = "%s (%s)" % (state, str(st.get("updated") or "")[:10])
    else:
        line = "not done (offered at %d of %d session starts)" % (min(int(st.get("offers") or 0), C.MAX_OFFERS),
                                                                  C.MAX_OFFERS)
    ack = C.lookup(C.read_toml(path) if exists else None, "acknowledged.disclaimer")
    print("First-run setup:   %s" % line)
    print("Personal profile:  %s (%s; %s)" % (path, source, "exists" if exists else "not created yet"))
    print("Disclaimer:        %s" % ("acknowledged" if str(ack) == C.DISCLAIMER_VERSION
                                     else "not acknowledged yet: " + C.DISCLAIMER_URL))
    print("Machine kind:      %s" % _kind_line())
    try:
        memory = memory_lines(C.machine_kind())
    except Exception:  # noqa: BLE001 - status is informational
        memory = ["unknown"]
    print("Memory backend:    %s" % memory[0])
    for extra in memory[1:]:
        print("                   %s" % extra)
    print("Launcher:          %s" % _launcher_line())
    print("Remote Control:    %s" % remote_line())
    print("Security settings: run `%s` in a terminal yourself" % C.command_line("setup", "security"))
    if os.environ.get("GNOMONKIT_NO_ONBOARDING"):
        print("(GNOMONKIT_NO_ONBOARDING is set, so sessions here never offer setup.)")
    return 0


def record(state: str) -> int:
    st = C.read_onboarding()
    st["state"] = state
    C.write_onboarding(st)
    if state == "done":
        print("Recorded: setup done. The first-run offer stops.")
        print("Last step, for the user in a terminal (an agent can't do it): %s" % C.command_line("setup", "security"))
    else:
        print('Recorded: setup skipped. The first-run offer stops; ask for "Gnomonkit setup" any time to run it.')
    return 0


def security(args) -> int:
    if not C.is_terminal():
        raise C.CommandError(
            "refused: `setup security` sets permissions an agent may not choose, so it runs only in a terminal "
            "where you type the answers yourself (stdin and stdout must both be a terminal). Open a terminal and "
            "run: %s" % C.command_line("setup", "security"))
    sections = _security_modules()
    if not sections:
        print(COMING)
        return 0
    for _order, _name, mod in sections:
        rc = int(mod.security_setup(args) or 0)
        if rc:
            return rc
    return 0


def _settings_path() -> str:
    return os.path.join(C.claude_home(), "settings.json")


def _read_settings(path: str) -> dict:
    """The user-level Claude Code settings, or {} when there is no file. A file that isn't a JSON object is
    an error: nothing is written over it."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise C.CommandError("%s could not be read as JSON (%s); nothing was changed" % (path, e))
    if not isinstance(data, dict):
        raise C.CommandError("%s does not hold a JSON object; nothing was changed" % path)
    return data


def remote_state() -> tuple:
    """(Claude's remoteControlAtStartup: True/False/None, profile working.remote: "yes"/"no"/None)."""
    try:
        wanted = _read_settings(_settings_path()).get(REMOTE_SETTING)
    except C.CommandError:
        wanted = None
    path, _source = C.personal_profile_path()
    answer = C.lookup(C.read_toml(path) if os.path.isfile(path) else None, "working.remote")
    return (wanted if isinstance(wanted, bool) else None), (answer if answer in ("yes", "no") else None)


def remote_line() -> str:
    wanted, answer = remote_state()
    setting = {True: "on", False: "off", None: "not set"}[wanted]
    how = C.command_line("setup", "remote", "on")
    if wanted is True:
        return "on (%s in Claude Code's user settings)" % REMOTE_SETTING
    return "%s; run `%s` if you work remotely or from the phone a lot%s" % (
        setting, how, " (profile says yes, but the Claude setting is %s)" % setting if answer == "yes" else "")


def remote_checklists(projects: list) -> str:
    prog = "gnomonkit"     # typed by a person: the launcher's name (install-launcher), not the python form
    if not projects:
        return PROJECT_CHECKLIST.format(project="<project folder>", prog=prog)
    return "\n".join(PROJECT_CHECKLIST.format(project=os.path.abspath(os.path.expanduser(p)), prog=prog)
                     for p in projects)


def remote(args) -> int:
    value = args.value or "status"
    wanted, answer = remote_state()
    if value == "status":
        print("Remote Control:    %s" % remote_line())
        print("Profile:           working.remote = %s" % (answer or "(not set)"))
        print()
        print(REMOTE_EXPLAINED)
        print()
        print("Still yours to do, per project you dispatch sessions from:")
        print(remote_checklists(args.project))
        return 0
    want = value == "on"
    path = _settings_path()
    data = _read_settings(path)
    if data.get(REMOTE_SETTING) is want:
        print("%s already %s in %s" % (REMOTE_SETTING, value, path))
    else:
        backup = None
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                old = fh.read()
            backup = path + ".bak"
            C.atomic_write(backup, old)
        new = dict(data)
        new[REMOTE_SETTING] = want
        C.atomic_write(path, json.dumps(new, indent=2) + "\n")
        print("saved %s: %s = %s%s" % (path, REMOTE_SETTING, "true" if want else "false",
                                       " (previous version: %s)" % backup if backup else ""))
    from . import profile
    rc = profile.cmd_set(types.SimpleNamespace(pairs=["working.remote=%s" % ("yes" if want else "no")], file=None))
    if rc:
        return rc
    print()
    if want:
        print(REMOTE_EXPLAINED)
        print("It takes effect in sessions started from now on (a session that is already open keeps what it had).")
        print()
        print("Still yours to do, per project you dispatch sessions from:")
        print(remote_checklists(args.project))
    else:
        print("Sessions started from now on connect no Remote Control unless started with --remote-control. "
              "Grants and folder trust are untouched: `%s` lists them." % C.command_line("grants", "list"))
    return 0


def run(args) -> int:
    if args.action == "status":
        return status()
    if args.action in ("done", "skipped"):
        return record(args.action)
    if args.action == "remote":
        return remote(args)
    return security(args)
