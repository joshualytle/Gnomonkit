"""gnomonkit profile: show and change the personal profile, every value checked against one schema.

    gnomonkit profile show [--json] [--file PATH]
    gnomonkit profile set KEY=VALUE [KEY=VALUE ...] [--file PATH]
    gnomonkit profile unset KEY [KEY ...] [--file PATH]

`set` checks every value before writing anything: one bad value and nothing is written (exit 2, with
the valid choices). It keeps the previous file as <file>.bak, writes the new one atomically in a fixed
layout and prints what changed. Comments are not kept; keys the schema doesn't know are.

Most keys live in the personal profile (see `profile show` for where that is). Some go elsewhere:
machine.shell (checked against the shells found here) and machine.kind (personal, work or unknown) to
~/.gnomonkit/machine.toml, because one person can have both kinds of machine, and plan.label /
plan.monthly_usd to the subscription schedule the effectiveness collector reads, as this month's entry.
This is the only way an agent should change these files: it never writes the TOML itself.
"""
from __future__ import annotations

import copy
import difflib
import json
import os
import sys
import time

from . import _common as C


def register(sub) -> None:
    p = sub.add_parser("profile", help="show or change your personal profile (checked against the schema)",
                       description=__doc__.split("\n\n")[0])
    ps = p.add_subparsers(dest="profile_cmd", metavar="<show|set|unset>")
    for name, fn, hlp in (("show", cmd_show, "every setting: its value, where it comes from, and the choices"),
                          ("set", cmd_set, "set KEY=VALUE pairs; nothing is written if any value is invalid"),
                          ("unset", cmd_unset, "remove keys from the profile so their defaults apply")):
        q = ps.add_parser(name, help=hlp)
        q.add_argument("--file", help="the profile file to use (default: the one the session hook reads)")
        if name == "show":
            q.add_argument("--json", action="store_true", help="machine-readable output")
        elif name == "set":
            q.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
        else:
            q.add_argument("keys", nargs="+", metavar="KEY")
        q.set_defaults(func=fn)
    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


# ---- files ---------------------------------------------------------------------------------------
def _target(args) -> tuple:
    if getattr(args, "file", None):
        return os.path.abspath(os.path.expanduser(args.file)), "--file"
    return C.personal_profile_path()


def _load_strict(path: str) -> dict:
    """The file's tables; {} when it doesn't exist. A file that exists but can't be read is never
    overwritten: that would lose whatever is in it."""
    if not os.path.exists(path):
        return {}
    try:
        return C.load_toml(path)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise C.CommandError("can't read %s (%s). Fix or move it first; nothing was written." % (path, e)) from None


def _machine_path() -> str:
    return os.path.join(C.gnomonkit_home(), "machine.toml")


def _subscription_path() -> str:
    raw = (os.environ.get("CLAUDE_PLUGIN_OPTION_SUBSCRIPTION_FILE") or os.environ.get("GNOMONKIT_SUBSCRIPTION_FILE")
           or "").strip()
    return os.path.expanduser(raw) if raw else os.path.join(C.gnomonkit_home(), "subscription.json")


def _read_subscription(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError:
        return []
    except ValueError:
        raise C.CommandError("can't read %s (not JSON). Fix or move it first; nothing was written." % path) from None
    if not isinstance(data, list):
        raise C.CommandError("%s is not a JSON list. Fix or move it first; nothing was written." % path)
    return data


def _session_profile():
    hooks = os.path.join(C.core_dir(), "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import session_profile
    return session_profile


# ---- show ----------------------------------------------------------------------------------------
def _fmt(v) -> str:
    if v is None:
        return "(not set)"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "; ".join(v) if v else "(empty list)"
    return str(v)


def _shells_here() -> str:
    """The shells found on this machine, which are machine.shell's choices (setup asks only when there are several)."""
    try:
        sp = _session_profile()
        return " | ".join(sp.detect_shells(sp.detect_os())) or "(none found)"
    except Exception:
        return "a shell found here"


def _choices(s: C.Setting) -> str:
    if s.choices:
        return " | ".join(s.choices)
    if s.store == "machine":   # machine.shell; machine.kind has fixed choices
        return _shells_here()
    return {"int": "a whole number, 0 = off" if "0 = off" in s.label else "a whole number",
            "number": "a number", "bool": "true | false", "list": "items separated by commas",
            "text": "one line of text", "path": "a folder path", "repo": "owner/name",
            "choice": "a shell found here"}.get(s.kind, s.kind)


def collect(path: str, data: dict | None) -> list:
    """[{key, value, source, default, choices, group}] with the precedence the hooks use."""
    rows = []
    machine = C.read_toml(_machine_path()) or {}
    try:
        plans = sorted((r for r in _read_subscription(_subscription_path()) if isinstance(r, dict)),
                       key=lambda r: str(r.get("from") or ""))
    except C.CommandError:
        plans = []
    for s in C.SCHEMA:
        value, source = None, "default"
        if s.store == "machine":
            value = machine.get(s.name)
            source = "machine profile" if value else "not set"
        elif s.store == "subscription":
            value = plans[-1].get("plan" if s.name == "label" else "monthly_usd") if plans else None
            source = "subscription file (from %s)" % plans[-1].get("from") if plans else "not set"
        else:
            if s.option:
                for name in ("CLAUDE_PLUGIN_OPTION_" + s.option, "GNOMONKIT_" + s.option):
                    raw = (os.environ.get(name) or "").strip()
                    if raw:
                        value, source = raw, "plugin option" if name.startswith("CLAUDE_") else name
                        break
            if source == "default":
                v = C.lookup(data, s.key)
                if v is not None:
                    value, source = v, "profile"
                else:
                    value = s.default
        rows.append({"key": s.key, "value": value, "source": source, "default": s.default,
                     "choices": _choices(s), "group": s.group, "label": s.label})
    return rows


def _unknown(data: dict | None) -> list:
    out = []
    if not isinstance(data, dict):
        return out
    known_tables = {s.table for s in C.SCHEMA if s.store == "personal"}
    for t, v in data.items():
        if t not in known_tables:
            out.append(t)
        elif isinstance(v, dict):
            out += ["%s.%s" % (t, k) for k in v if "%s.%s" % (t, k) not in C.SCHEMA_BY_KEY]
    return out


def cmd_show(args) -> int:
    path, source = _target(args)
    exists = os.path.isfile(path)
    data, error = None, None
    if exists:
        try:
            data = C.load_toml(path)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            error = str(e)
    rows = collect(path, data)
    problems = C.validate_profile(data) if data is not None else []
    if args.json:
        print(json.dumps({"personal": {"path": path, "source": source, "exists": exists, "error": error},
                          "machine": _machine_path(), "subscription": _subscription_path(),
                          "settings": rows, "unknown_keys": _unknown(data), "problems": problems},
                         indent=1, default=str))
        return 0
    print("Personal profile: %s" % path)
    print("  from: %s; %s" % (source, "exists" if exists else "not created yet (`profile set` creates it)"))
    if error:
        print("  CAN'T READ IT: %s" % error)
    print("  Written by `gnomonkit profile set`, which doesn't keep comments: the previous version is kept as "
          "%s.bak." % os.path.basename(path))
    print("Machine profile:  %s" % _machine_path())
    print("Subscription:     %s" % _subscription_path())
    width = max(len(r["key"]) for r in rows) + 1
    swidth = max(len(r["source"]) for r in rows) + 1
    print("\n  %-*s %-28s %-*s %s" % (width, "key", "value", swidth, "from", "choices"))
    for group, title in (("default", "Default set"), ("advanced", "Advanced"), ("record", "Recorded by setup")):
        print("\n" + title)
        for r in (r for r in rows if r["group"] == group):
            print("  %-*s %-28s %-*s %s" % (width, r["key"], _fmt(r["value"])[:28], swidth, r["source"],
                                           r["choices"]))
    unknown = _unknown(data)
    if unknown:
        print("\nKept, not in the schema: %s" % ", ".join(unknown))
    if problems:
        print("\nProblems (the hooks ignore these values):")
        for p in problems:
            print("  - " + p)
    print("\nPlugin options (context_budget, max_compactions, manual_compactions, scratch_root, corrections_repo) win over the profile. "
          "They reach hooks but not this shell, so one set with `claude plugin configure core@gnomonkit` may "
          "override a value shown here as coming from the profile.")
    return 0


# ---- set / unset ---------------------------------------------------------------------------------
def _unknown_key(key: str) -> str:
    near = difflib.get_close_matches(key, list(C.SCHEMA_BY_KEY), n=1, cutoff=0.6)
    return "unknown key %s%s (`gnomonkit profile show` lists every key)" % (
        key, "; did you mean %s?" % near[0] if near else "")


def _write_personal(path: str, current: dict, new: dict) -> str | None:
    """Back up and replace the file. Returns the backup path, or None when there was no old file."""
    backup = None
    if os.path.exists(path):
        with open(path, "rb") as fh:
            old = fh.read()
        backup = path + ".bak"
        C.atomic_write(backup, old)
    C.atomic_write(path, C.dumps(new, C.personal_layout(), C.PERSONAL_HEADER))
    return backup


def _set_nested(d: dict, key: str, value) -> None:
    table, name = key.split(".", 1)
    if not isinstance(d.get(table), dict):
        d[table] = {}
    d[table][name] = value


def cmd_set(args) -> int:
    path, _source = _target(args)
    errors, personal, plan, machine = [], {}, {}, {}
    sp = None
    for pair in args.pairs:
        if "=" not in pair:
            errors.append("%r: write KEY=VALUE" % pair)
            continue
        key, raw = pair.split("=", 1)
        key = key.strip()
        s = C.SCHEMA_BY_KEY.get(key)
        if s is None:
            errors.append(_unknown_key(key))
            continue
        try:
            if s.store == "machine":
                sp = sp or _session_profile()
                machine[s.name] = C.coerce(s, raw, choices=None if s.choices else sp.detect_shells(sp.detect_os()))
            elif s.store == "subscription":
                plan[s.name] = C.coerce(s, raw)
            else:
                personal[key] = C.coerce(s, raw)
        except ValueError as e:
            errors.append(str(e))
    current = _load_strict(path) if personal else {}
    sub_path, schedule = _subscription_path(), []
    if plan:
        schedule = _read_subscription(sub_path)
        last = sorted((r for r in schedule if isinstance(r, dict)), key=lambda r: str(r.get("from") or ""))
        last = last[-1] if last else {}
        plan.setdefault("label", last.get("plan"))
        plan.setdefault("monthly_usd", last.get("monthly_usd"))
        if plan["monthly_usd"] is None:
            errors.append("plan.monthly_usd is needed with plan.label (there is no earlier entry to take it from)")
        if not plan["label"]:
            plan["label"] = ""
    if errors:
        raise C.CommandError("\n  ".join(["nothing was written:"] + errors))

    changes = []
    if personal:
        new = copy.deepcopy(current)
        for key, v in personal.items():
            old = C.lookup(current, key)
            if old != v:
                changes.append("%s: %s -> %s" % (key, _fmt(old), _fmt(v)))
            _set_nested(new, key, v)
        if new != current:
            backup = _write_personal(path, current, new)
            print("saved %s%s" % (path, " (previous version: %s)" % backup if backup else ""))
    if plan:
        month = time.strftime("%Y-%m")
        entry = {"from": month, "plan": plan["label"], "monthly_usd": plan["monthly_usd"]}
        schedule = [r for r in schedule if not (isinstance(r, dict) and r.get("from") == month)] + [entry]
        schedule.sort(key=lambda r: str(r.get("from") or "") if isinstance(r, dict) else "")
        C.atomic_write(sub_path, json.dumps(schedule, indent=1) + "\n")
        changes.append("plan from %s: %s, %s USD a month" % (month, plan["label"] or "(no name)", plan["monthly_usd"]))
        print("saved %s" % sub_path)
    if machine:
        rc = sp.write_machine(machine.get("shell"), kind=machine.get("kind"))
        if rc:
            return rc
        changes += ["machine.%s: %s" % (k, machine[k]) for k in ("shell", "kind") if k in machine]
    for c in changes:
        print("  " + c)
    if not changes:
        print("no change: %s already holds these values" % path)
    return 0


def cmd_unset(args) -> int:
    path, _source = _target(args)
    errors = []
    for key in args.keys:
        s = C.SCHEMA_BY_KEY.get(key)
        if s is None:
            errors.append(_unknown_key(key))
        elif s.store != "personal":
            errors.append("%s is not kept in the personal profile; edit %s instead" % (
                key, _machine_path() if s.store == "machine" else _subscription_path()))
    if errors:
        raise C.CommandError("\n  ".join(["nothing was written:"] + errors))
    current = _load_strict(path)
    new = copy.deepcopy(current)
    removed = []
    for key in args.keys:
        table, name = key.split(".", 1)
        if isinstance(new.get(table), dict) and name in new[table]:
            del new[table][name]
            removed.append(key)
            if not new[table]:
                del new[table]
    if not removed:
        print("no change: none of those keys is set in %s" % path)
        return 0
    backup = _write_personal(path, current, new)
    print("saved %s%s" % (path, " (previous version: %s)" % backup if backup else ""))
    for key in removed:
        print("  %s: removed (default: %s)" % (key, _fmt(C.SCHEMA_BY_KEY[key].default)))
    return 0
