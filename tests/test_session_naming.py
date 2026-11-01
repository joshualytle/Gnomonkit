"""The session naming scheme (ij_naming): templates of fields, where they are set, how they are checked, and
how titles are read back. Dispatch's use of it is in test_session_roles; the hook's in test_ij_naming.

Synthetic data only: made-up repo, task and prefix names, a profile written to a temp folder.
"""
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IJ = ROOT / "plugins" / "interjector"
for p in (str(IJ), str(IJ / "hooks")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ij_naming as N  # noqa: E402
from interjector import roles as R  # noqa: E402

GK = {"prefix": "gk", "primary": "{prefix}-{repo}:{task}", "sub": "[{prefix} {role}] {parent} / {task} ({date})"}


@pytest.fixture(autouse=True)
def profile(tmp_path, monkeypatch):
    """No naming setting or profile of the user's: the profile is a file in a temp GNOMONKIT_HOME."""
    for k in list(os.environ):
        if k.startswith("GNOMONKIT_NAMING_") or k in ("GNOMONKIT_PERSONAL_PROFILE",
                                                      "CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path))
    return tmp_path / "personal.toml"


def write(path, table, extra=""):
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(extra + "[naming]\n" + "".join('%s = "%s"\n' % kv for kv in table.items()), encoding="utf-8")
    st = path.stat()                         # a stamp later than the last one, whatever the clock tick did
    os.utime(path, ns=(st.st_atime_ns, max(st.st_mtime_ns, before) + 1_000_000))


# ---- the defaults are the names in use before the scheme ----------------------------------------
def test_the_defaults_are_the_names_in_use_before():
    s = N.scheme()
    assert (s.primary, s.sub, s.prefix, s.source, s.problems) == ("{repo}:{task}", "sub:{parent}/{task}", "",
                                                                  "default", [])
    assert s.render(N.PRIMARY, "fix-login", "demo-repo") == "demo-repo:fix-login"
    assert s.render(N.PRIMARY, "fix-login") == "session:fix-login"
    assert s.render(N.SUB, "review", "demo-repo", parent="release") == "sub:release/review"
    assert N.read("demo-repo:fix-login") == {"role": "primary", "repo": "demo-repo", "task": "fix-login"}
    assert N.read("demo-repo@feat/x:fix-login")["repo"] == "demo-repo@feat/x"
    assert N.read("sub:release/review") == {"role": "sub", "parent": "release", "task": "review"}
    assert N.read("sub:a/b/review")["parent"] == "a/b"                  # the parent ends at the last /
    for title in ("my chosen title", "", None, "Release prep"):
        assert N.read(title) is None
    assert N.check(N.DEFAULT_PRIMARY, N.DEFAULT_SUB) == []


def test_roles_read_titles_as_before():
    assert R.task_part("demo-repo:release") == "release" and R.task_part("sub:release/review") == "review"
    assert R.task_part("my chosen title") == "my chosen title"
    assert R.sub_parent_label("sub:release/review") == "release" and R.sub_parent_label("demo-repo:x") == ""
    assert R.sub_name("release", "review") == "sub:release/review"
    assert R.role_of({"name": "sub:release/review"}, None) == R.SUB
    assert R.role_of({"name": "demo-repo:release"}, None) == ""
    assert R.role_of({"name": "sub:release/review"}, {"role": "primary"}) == "primary"   # the record wins


def test_parent_label():
    assert N.parent_label("my release / plan") == "my-release-plan"
    assert N.parent_label("") == "session" and N.parent_label(None) == "session"
    assert len(N.parent_label("p" * 60)) == N.PARENT_MAX


def test_placeholder():
    assert N.is_placeholder("demo-repo:new-session") and not N.is_placeholder("demo-repo:new-session-2")
    assert not N.is_placeholder("sub:release/new-session")


# ---- a scheme of the user's own -----------------------------------------------------------------
def test_a_scheme_from_the_profile_renders_and_reads_back(profile):
    write(profile, GK)
    s = N.scheme()
    assert (s.primary, s.sub, s.prefix, s.problems) == (GK["primary"], GK["sub"], "gk", [])
    assert s.source == "[naming] primary, sub, prefix in " + str(profile)
    p = s.render(N.PRIMARY, "fix-login", "demo-repo@feat/x")
    assert p == "gk-demo-repo@feat/x:fix-login"
    assert N.read(p) == {"role": "primary", "repo": "demo-repo@feat/x", "task": "fix-login"}
    sub = s.render(N.SUB, "review", "demo-repo", parent="release plan", date="2026-01-02")
    assert sub == "[gk sub] release-plan / review (2026-01-02)"
    assert N.read(sub) == {"role": "sub", "parent": "release-plan", "task": "review", "date": "2026-01-02"}
    assert R.sub_name("release", "review", repo="demo-repo").startswith("[gk sub] release / review (")
    assert R.task_part(sub) == "review" and R.sub_parent_label(sub) == "release-plan"
    assert R.role_of({"name": sub}, None) == R.SUB and R.role_of({"name": p}, None) == ""
    assert N.is_placeholder(s.render(N.PRIMARY, N.PLACEHOLDER, "demo-repo"))


def test_the_date_field_is_today(profile):
    write(profile, {"primary": "{date} {repo}:{task}"})
    before = time.strftime("%Y-%m-%d")
    name = N.scheme().render(N.PRIMARY, "fix-login", "demo-repo")
    assert name in {d + " demo-repo:fix-login" for d in (before, time.strftime("%Y-%m-%d"))}
    assert N.read(name)["date"] in (before, time.strftime("%Y-%m-%d"))


def test_titles_made_before_a_change_still_read_as_what_they_are(profile):
    write(profile, GK)
    assert N.read("sub:release/review") == {"role": "sub", "parent": "release", "task": "review"}
    assert N.read("demo-repo:fix-login")["role"] == "primary"
    assert R.role_of({"name": "sub:release/review"}, None) == R.SUB
    assert R.task_part("demo-repo:fix-login") == "fix-login"
    assert N.is_placeholder("demo-repo:new-session")


def test_the_environment_wins_over_the_profile(profile, monkeypatch):
    write(profile, GK)
    monkeypatch.setenv("GNOMONKIT_NAMING_PRIMARY", "{repo} = {task}")
    s = N.scheme()
    assert s.primary == "{repo} = {task}" and s.sub == GK["sub"] and s.prefix == "gk"
    assert s.source.startswith("GNOMONKIT_NAMING_PRIMARY; [naming] sub, prefix in ")
    monkeypatch.setenv("GNOMONKIT_NAMING_PREFIX", "ops")
    assert N.scheme().render(N.SUB, "x", parent="y", date="2026-01-02") == "[ops sub] y / x (2026-01-02)"


def test_a_profile_edit_is_seen_without_a_restart(profile):
    write(profile, {"primary": "{repo} = {task}"})
    assert N.scheme().primary == "{repo} = {task}"
    write(profile, {"primary": "{repo} -> {task}"})
    assert N.scheme().primary == "{repo} -> {task}"
    profile.unlink()
    assert N.scheme().source == "default"


def test_a_profile_without_naming_or_that_cannot_be_read_gives_the_defaults(profile):
    profile.write_text("[dispatch]\nmax = 2\n", encoding="utf-8")
    assert N.scheme().source == "default"
    profile.write_text("[naming\nprimary = ", encoding="utf-8")
    st = profile.stat()
    os.utime(profile, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    s = N.scheme()
    assert s.source == "default" and s.problems == []


# ---- validation ----------------------------------------------------------------------------------
D_P, D_S = N.DEFAULT_PRIMARY, N.DEFAULT_SUB


@pytest.mark.parametrize("primary,sub,prefix,problem", [
    ("{repo}{task}", D_S, "", "primary: {repo}{task} need text between them"),
    (D_P, "sub:{parent}{task}", "", "sub: {parent}{task} need text between them"),
    ("{repo}:{tsk}", D_S, "", "primary: unknown field {tsk}"),
    ("{repo}:{parent}/{task}", D_S, "", "primary: unknown field {parent}"),
    ("{task}-{task}", D_S, "", "primary: {task} more than once"),
    ("{repo}", D_S, "", "primary: needs {task}"),
    (D_P, "sub:{parent}", "", "sub: needs {task}"),
    ("{prefix}:{repo}:{task}", D_S, "", "primary: uses {prefix}, but no prefix is set"),
    (D_P, D_S, "has space", "prefix: up to 24 of"),
    (D_P, D_S, "p" * 25, "prefix: up to 24 of"),
    ("x" * 80 + "{task}", D_S, "", "primary: longer than 80 characters"),
    ("{repo}:{task}\t", D_S, "", "primary: holds a control character"),
    ("{repo}:{task}}", D_S, "", "primary: a { or } that is not part of a {field}"),
    ("{repo:{task}", D_S, "", "primary: a { or } that is not part of a {field}"),
    ("", D_S, "", "primary: empty or not text"),
    (5, D_S, "", "primary: empty or not text"),
    (D_P, ["sub"], "", "sub: empty or not text"),
    ("sub:{repo}/{task}", D_S, "", "primary: a name like sub:demo-repo@feat/x/fix-login reads as a sub"),
    ("{repo}/{task}", D_S, "", "primary: a name like demo-repo@feat/x/fix-login does not read back"),
])
def test_each_rule_refuses(primary, sub, prefix, problem):
    got = N.check(primary, sub, prefix)
    assert any(p.startswith(problem) for p in got), got


def test_role_tells_two_templates_of_the_same_text_apart():
    assert N.check("{role} {repo}:{task}", "{role} {parent}:{task}") == []
    assert N.check("{repo}:{task}", "{parent}:{task}")                   # without it a sub reads as a primary


def test_an_invalid_scheme_falls_back_to_the_defaults_whole(profile):
    write(profile, {"prefix": "gk", "primary": "{prefix}-{repo}:{task}", "sub": "{prefix}-sub:{parent}"})
    s = N.scheme()
    assert (s.primary, s.sub, s.prefix) == (D_P, D_S, "")               # the valid primary is not used either
    assert s.source.startswith("default, because the scheme set in [naming] primary, sub, prefix in ")
    assert s.problems == ["sub: needs {task}, so each task gets a name of its own"]
    assert s.render(N.SUB, "review", parent="release") == "sub:release/review"


def test_a_bad_value_type_in_the_profile_falls_back(profile):
    profile.write_text("[naming]\nprimary = 5\n", encoding="utf-8")
    s = N.scheme()
    assert s.primary == D_P and s.problems == ["primary: empty or not text"]
