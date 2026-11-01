"""gnomonkit overlay: init, convert, check and link.

Synthetic data only: every folder is made under pytest's tmp_path, GNOMONKIT_HOME points there, git
reads a throwaway global config, and nothing asks GitHub anything (github_visibility is replaced).
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "plugins", "core")
sys.path.insert(0, CORE)

import gnomonkit  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import overlay  # noqa: E402

HAVE_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAVE_GIT, reason="git is not installed")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A fresh GNOMONKIT_HOME, no developer options, a git config of our own, and no GitHub lookups."""
    for k in list(os.environ):
        if k.startswith("CLAUDE_PLUGIN_OPTION_") or k.startswith("GNOMONKIT_") or k.startswith("GIT_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    asked = []
    monkeypatch.setattr(overlay, "github_visibility", lambda slug: asked.append(slug))
    C._CACHE.clear()
    yield {"gitconfig": gitconfig, "asked": asked}
    C._CACHE.clear()


def cli(capsys, *argv):
    rc = gnomonkit.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return rc, out, err


def set_identity(isolated):
    isolated["gitconfig"].write_text("[user]\n\tname = Example User\n\temail = user@example.com\n", encoding="utf-8")


def git(*args, cwd):
    return subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True)


def tree_hash(root):
    h = hashlib.sha256()
    for folder, dirs, files in os.walk(str(root)):
        dirs.sort()
        for d in dirs:
            h.update(("d:" + os.path.relpath(os.path.join(folder, d), str(root))).encode())
        for fn in sorted(files):
            p = os.path.join(folder, fn)
            h.update(("f:" + os.path.relpath(p, str(root))).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def files_under(root):
    out = {}
    for folder, dirs, files in os.walk(str(root)):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fn in files:
            p = os.path.join(folder, fn)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, str(root)).replace(os.sep, "/")] = fh.read()
    return out


class Recorder:
    """Stands in for subprocess.run inside the overlay module: records every argv, then runs it."""

    def __init__(self, real):
        self.real, self.calls = real, []

    def __call__(self, argv, *a, **kw):
        self.calls.append([str(x) for x in argv])
        return self.real(argv, *a, **kw)


def outward(calls):
    """Calls that would create a remote or publish."""
    bad = []
    for argv in calls:
        words = [os.path.splitext(os.path.basename(argv[0]))[0].lower()] + argv[1:]
        joined = " ".join(words)
        if (words[0] == "gh" and "create" in words) or (words[0] == "git" and re.search(r"\b(push|remote add|remote set-url)\b", joined)):
            bad.append(argv)
    return bad


# ---- init ----------------------------------------------------------------------------------------
def test_init_builds_the_layout_and_check_passes(tmp_path, capsys):
    target = tmp_path / "projects" / "my-overlay"
    rc, out, _ = cli(capsys, "overlay", "init", target)
    assert rc == 0, out
    for rel in ("gnomonkit-overlay.toml", "README.md", "CLAUDE.md", ".gitignore", ".claude-plugin/marketplace.json",
                "plugins/overlay/.claude-plugin/plugin.json", "plugins/overlay/skills/example-house-rule/SKILL.md",
                "profile/personal.toml", "private/gnomonkit-denylist.txt", "evidence/README.md"):
        assert (target / rel).is_file(), rel
        assert rel in out
    marker = C.load_toml(str(target / "gnomonkit-overlay.toml"))
    assert marker["format"] == 1 and marker["name"] == "my-overlay"
    assert marker["parts"] == ["plugin", "profile", "denylist", "evidence"]
    assert "{{" not in (target / "README.md").read_text(encoding="utf-8")
    rc, out, _ = cli(capsys, "overlay", "check", target)
    assert rc == 0, out
    assert "Check passed." in out
    assert "the denylist has no rules yet" in out   # a note, not a problem: init leaves it for the user


def test_init_refuses_a_folder_that_is_not_empty(tmp_path, capsys):
    target = tmp_path / "notes"
    target.mkdir()
    (target / "todo.md").write_text("synthetic\n", encoding="utf-8")
    before = tree_hash(target)
    rc, _, err = cli(capsys, "overlay", "init", target)
    assert rc == 2
    assert "not empty" in err and "overlay convert" in err
    assert tree_hash(target) == before


def test_init_refuses_names_that_cannot_be_a_marketplace(tmp_path, capsys):
    for bad in ("gnomonkit", "My Overlay", "claude-rules", "-x"):
        rc, _, err = cli(capsys, "overlay", "init", tmp_path / "o", "--name=" + bad)
        assert rc == 2, bad
    assert not (tmp_path / "o").exists()
    assert overlay.suggest_name(str(tmp_path / "My Private Rules")) == "my-private-rules"


@needs_git
def test_init_commits_only_when_the_git_identity_is_set(tmp_path, capsys, isolated):
    rc, out, _ = cli(capsys, "overlay", "init", tmp_path / "a")
    assert rc == 0
    assert "No commit: your git identity isn't set" in out
    assert (tmp_path / "a" / ".git").is_dir()
    assert git("rev-parse", "--verify", "-q", "HEAD", cwd=tmp_path / "a").returncode != 0

    set_identity(isolated)
    rc, out, _ = cli(capsys, "overlay", "init", tmp_path / "b")
    assert rc == 0
    assert "Made the first commit." in out
    tracked = git("ls-files", cwd=tmp_path / "b").stdout.split()
    assert "private/gnomonkit-denylist.txt" in tracked and "profile/personal.toml" in tracked


@needs_git
def test_init_prints_a_private_remote_and_never_makes_one(tmp_path, capsys, monkeypatch, isolated):
    set_identity(isolated)
    rec = Recorder(subprocess.run)
    monkeypatch.setattr(overlay.subprocess, "run", rec)
    rc, out, _ = cli(capsys, "overlay", "init", tmp_path / "o", "--github", "example-org/my-overlay")
    assert rc == 0
    assert "gh repo create example-org/my-overlay --private" in out
    assert "--public" not in out
    assert rec.calls and not outward(rec.calls)
    assert not git("remote", cwd=tmp_path / "o").stdout.strip()
    assert C.load_toml(str(tmp_path / "o" / "gnomonkit-overlay.toml"))["github"] == "example-org/my-overlay"


@needs_git
def test_init_accepts_an_empty_clone_but_not_a_public_one(tmp_path, capsys, monkeypatch):
    target = tmp_path / "clone"
    target.mkdir()
    git("init", "-q", cwd=target)
    git("remote", "add", "origin", "https://github.com/example-org/rules.git", cwd=target)
    monkeypatch.setattr(overlay, "github_visibility", lambda slug: "public")
    rc, _, err = cli(capsys, "overlay", "init", target)
    assert rc == 2 and "PUBLIC" in err
    assert os.listdir(str(target)) == [".git"]
    monkeypatch.setattr(overlay, "github_visibility", lambda slug: "private")
    rc, out, _ = cli(capsys, "overlay", "init", target)
    assert rc == 0
    assert "Push it yourself" in out and "gh repo create" not in out


def test_the_profile_template_lists_the_schema_and_passes_it_when_uncommented():
    text = overlay.profile_template()
    assert C.validate_profile(C.parse_toml(text)) == []          # all commented out: defaults apply
    live = "\n".join(line[2:] if re.match(r"# (\[|[a-z_]+ = )", line) else line for line in text.splitlines())
    data = C.parse_toml(live)
    assert C.validate_profile(data) == []
    for s in C.SCHEMA:
        if s.store == "personal" and s.group != "record":
            assert C.lookup(data, s.key) is not None, s.key


# ---- convert -------------------------------------------------------------------------------------
def make_rules_repo(root, with_git=True):
    """A synthetic repository of the kind people keep their rules in."""
    (root / ".claude" / "skills" / "status-style").mkdir(parents=True)
    (root / ".claude" / "skills" / "status-style" / "SKILL.md").write_text(
        "---\nname: status-style\ndescription: Activates when writing a status update.\n---\n# Status\n", encoding="utf-8")
    (root / ".claude" / "skills" / "status-style" / "example.txt").write_text("synthetic\n", encoding="utf-8")
    (root / ".claude" / "settings.json").write_text(
        '{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]}}',
        encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Rules\n\n- be brief\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "naming-conventions.md").write_text("# Naming\n", encoding="utf-8")
    (root / "keep" / "profile").mkdir(parents=True)
    (root / "keep" / "profile" / "personal.toml").write_text('[communication]\ndecisions = "prose"\n',
                                                             encoding="utf-8")
    (root / "keep" / "my-denylist.txt").write_text("name\t\\bexample-person\\b\n", encoding="utf-8")
    if with_git and HAVE_GIT:
        git("init", "-q", cwd=root)


def test_convert_without_apply_changes_nothing(tmp_path, capsys):
    repo = tmp_path / "rules"
    repo.mkdir()
    make_rules_repo(repo)
    before = tree_hash(repo)
    rc, out, _ = cli(capsys, "overlay", "convert", repo)
    assert rc == 0, out
    assert tree_hash(repo) == before
    assert "Dry run: nothing was changed." in out
    assert ".claude/skills/status-style/ -> plugins/overlay/skills/status-style/" in out
    assert "keep/profile/personal.toml -> profile/personal.toml" in out
    assert "keep/my-denylist.txt -> private/gnomonkit-denylist.txt" in out
    assert "AGENTS.md holds instructions for agents" in out
    assert ".claude/settings.json has 1 hook (PreToolUse)" in out
    assert "docs/naming-conventions.md reads like rules" in out


def test_convert_dry_run_on_a_plain_folder_does_not_run_git_init(tmp_path, capsys):
    repo = tmp_path / "plain"
    repo.mkdir()
    make_rules_repo(repo, with_git=False)
    before = tree_hash(repo)
    rc, out, _ = cli(capsys, "overlay", "convert", repo)
    assert rc == 0
    assert tree_hash(repo) == before and not (repo / ".git").exists()
    assert "--apply runs `git init` here" in out


def test_convert_apply_copies_and_never_deletes_or_overwrites(tmp_path, capsys):
    repo = tmp_path / "rules"
    repo.mkdir()
    make_rules_repo(repo)
    (repo / "README.md").write_text("# My rules\n", encoding="utf-8")
    # A skill already in the overlay's place with different text: it must be left as it is.
    clash = repo / "plugins" / "overlay" / "skills" / "status-style"
    clash.mkdir(parents=True)
    (clash / "SKILL.md").write_text("---\ndescription: Activates when an older copy is kept.\n---\n", encoding="utf-8")
    before = files_under(repo)
    rc, out, _ = cli(capsys, "overlay", "convert", repo, "--apply")
    assert rc == 0, out
    after = files_under(repo)
    for rel, data in before.items():
        assert after.get(rel) == data, rel        # every original is still there, unchanged
    new = set(after) - set(before)
    assert {"gnomonkit-overlay.toml", ".claude-plugin/marketplace.json", "plugins/overlay/.claude-plugin/plugin.json",
            "plugins/overlay/skills/status-style/example.txt", "profile/personal.toml",
            "private/gnomonkit-denylist.txt", "evidence/README.md", ".gitignore"} <= new
    assert "README.md" not in new and "CLAUDE.md" not in new
    assert after["profile/personal.toml"] == before["keep/profile/personal.toml"]
    for rel in new:
        assert rel in out                         # every file written is reported
    assert "differs from .claude/skills/status-style/SKILL.md" in out
    assert "The originals are still in place" in out
    rc, out, _ = cli(capsys, "overlay", "check", repo)
    assert rc == 0, out


def test_convert_refuses_a_repository_that_is_public_on_github(tmp_path, capsys, monkeypatch):
    if not HAVE_GIT:
        pytest.skip("git is not installed")
    repo = tmp_path / "rules"
    repo.mkdir()
    make_rules_repo(repo)
    git("remote", "add", "origin", "git" + "@" + "github.com:example-org/shared-rules.git", cwd=repo)
    seen = []
    monkeypatch.setattr(overlay, "github_visibility", lambda slug: seen.append(slug) or "public")
    before = tree_hash(repo)
    for extra in ((), ("--apply",)):
        rc, _, err = cli(capsys, "overlay", "convert", repo, *extra)
        assert rc == 2 and "PUBLIC" in err
    assert seen == ["example-org/shared-rules"] * 2
    assert tree_hash(repo) == before


def test_convert_leaves_an_existing_marketplace_alone_and_says_what_to_add(tmp_path, capsys):
    repo = tmp_path / "rules"
    (repo / ".claude-plugin").mkdir(parents=True)
    manifest = '{"name": "my-rules", "owner": {"name": "x"}, "plugins": []}\n'
    (repo / ".claude-plugin" / "marketplace.json").write_text(manifest, encoding="utf-8")
    rc, out, _ = cli(capsys, "overlay", "convert", repo, "--apply")
    assert rc == 0
    assert (repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8") == manifest
    assert "add this entry to the plugins list" in out
    assert C.load_toml(str(repo / "gnomonkit-overlay.toml"))["name"] == "my-rules"
    rc, out, _ = cli(capsys, "overlay", "check", repo)
    assert rc == 1 and "doesn't list plugins/overlay" in out


def test_convert_refuses_an_overlay_and_gnomonkit_itself(tmp_path, capsys):
    cli(capsys, "overlay", "init", tmp_path / "o")
    rc, _, err = cli(capsys, "overlay", "convert", tmp_path / "o")
    assert rc == 2 and "already an overlay" in err
    shared = tmp_path / "shared"
    (shared / ".claude-plugin").mkdir(parents=True)
    (shared / ".claude-plugin" / "marketplace.json").write_text('{"name": "gnomonkit", "plugins": []}',
                                                                 encoding="utf-8")
    rc, _, err = cli(capsys, "overlay", "convert", shared)
    assert rc == 2 and "Gnomonkit's own repository" in err


# ---- check ---------------------------------------------------------------------------------------
@pytest.fixture
def made(tmp_path, capsys):
    target = tmp_path / "o"
    assert cli(capsys, "overlay", "init", target)[0] == 0
    return target


def test_check_says_when_a_folder_is_not_an_overlay(tmp_path, capsys):
    (tmp_path / "plain").mkdir()
    rc, out, _ = cli(capsys, "overlay", "check", tmp_path / "plain")
    assert rc == 2 and "not a Gnomonkit overlay" in out


def test_check_reports_a_skill_without_a_description(made, capsys):
    skill = made / "plugins" / "overlay" / "skills" / "bare"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: bare\n---\n# Bare\n", encoding="utf-8")
    (made / "plugins" / "overlay" / "skills" / "empty").mkdir()
    rc, out, _ = cli(capsys, "overlay", "check", made)
    assert rc == 1
    assert "plugins/overlay/skills/bare/SKILL.md has no description:" in out
    assert "plugins/overlay/skills/empty/SKILL.md has no SKILL.md" in out


def test_check_reports_bad_denylist_lines_without_printing_them(made, capsys):
    ident = "zq" + "x-" + "intra" + "corp"           # built here so this file holds no identifier
    lines = ["# comment", "name\t\\b%s\\b" % ident, "%s without a tab" % ident, "host\t(%s" % ident, "any\t.*"]
    (made / "private" / "gnomonkit-denylist.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc, out, _ = cli(capsys, "overlay", "check", made)
    assert rc == 1
    assert "denylist line 3: expected category<TAB>regex" in out
    assert "denylist line 4: the regular expression doesn't compile" in out
    assert "denylist line 5: the regular expression matches empty text" in out
    assert ident not in out
    assert overlay.parse_denylist("\n".join(lines[:2]))[0] == 1


def test_check_reports_a_malformed_profile(made, capsys):
    prof = made / "profile" / "personal.toml"
    prof.write_text('[communication]\ndecisions = "sometimes"\n', encoding="utf-8")
    rc, out, _ = cli(capsys, "overlay", "check", made)
    assert rc == 1 and "communication.decisions must be one of: numbered | prose" in out
    prof.write_text("[communication\n", encoding="utf-8")
    rc, out, _ = cli(capsys, "overlay", "check", made)
    assert rc == 1 and "profile/personal.toml doesn't parse as TOML" in out


def test_check_reports_marker_and_manifest_problems(made, capsys):
    (made / "gnomonkit-overlay.toml").write_text('format = 2\nname = "o"\nparts = ["plugin", "lessons", "x"]\n',
                                                encoding="utf-8")
    (made / "plugins" / "overlay" / ".claude-plugin" / "plugin.json").write_text("{not json", encoding="utf-8")
    rc, out, _ = cli(capsys, "overlay", "check", made)
    assert rc == 1
    assert "format 2" in out
    assert "lists lessons, but lessons/register.json is missing" in out
    assert "unknown part 'x'" in out
    assert "plugins/overlay/.claude-plugin/plugin.json is not valid JSON" in out


@needs_git
def test_check_reports_private_files_an_ignore_rule_would_leave_out(made, capsys):
    with open(str(made / ".gitignore"), "a", encoding="utf-8") as fh:
        fh.write("private/\n")
    rc, out, _ = cli(capsys, "overlay", "check", made)
    assert rc == 1
    assert "private/gnomonkit-denylist.txt is ignored (.gitignore line" in out


@needs_git
def test_check_reports_a_public_github_repository(made, capsys, monkeypatch):
    git("remote", "add", "origin", "https://github.com/example-org/o.git", cwd=made)
    monkeypatch.setattr(overlay, "github_visibility", lambda slug: "public")
    rc, out, _ = cli(capsys, "overlay", "check", made)
    assert rc == 1 and "example-org/o is PUBLIC on GitHub" in out
    monkeypatch.setattr(overlay, "github_visibility", lambda slug: "private")
    rc, out, _ = cli(capsys, "overlay", "check", made)
    assert rc == 0 and "example-org/o is private on GitHub" in out


def test_check_and_link_find_the_overlay_the_profile_is_in(made, capsys, monkeypatch):
    rc, out, _ = cli(capsys, "overlay", "check")
    assert rc == 2 and "no overlay is configured" in out
    monkeypatch.setenv("GNOMONKIT_PERSONAL_PROFILE", str(made / "profile" / "personal.toml"))
    C._CACHE.clear()
    assert overlay.configured_overlay() == str(made)
    assert cli(capsys, "overlay", "check")[0] == 0
    rc, out, _ = cli(capsys, "overlay", "link")
    assert rc == 0 and "overlay@o" in out


# ---- link ----------------------------------------------------------------------------------------
def test_link_prints_the_commands_and_runs_nothing(made, capsys, monkeypatch, tmp_path):
    def refuse(*a, **kw):
        raise AssertionError("link ran a program: %r" % (a,))
    monkeypatch.setattr(overlay.subprocess, "run", refuse)
    monkeypatch.setattr(overlay.subprocess, "Popen", refuse)
    (tmp_path / "gk").mkdir(exist_ok=True)
    (tmp_path / "gk" / "personal.toml").write_text('[communication]\ndecisions = "prose"\n', encoding="utf-8")
    before = tree_hash(made)
    rc, out, _ = cli(capsys, "overlay", "link", made)
    assert rc == 0
    assert "claude plugin marketplace add" in out and str(made) in out
    assert "claude plugin install overlay@o" in out
    assert '"personal_profile"' in out and "claude plugin configure core@gnomonkit --values-stdin" in out
    assert "GNOMONKIT_DENYLIST" in out and "gnomonkit-denylist.txt" in out
    assert "You already have a profile at" in out
    assert "Nothing was run" in out
    assert tree_hash(made) == before


def test_link_refuses_a_folder_that_is_not_an_overlay(tmp_path, capsys):
    (tmp_path / "plain").mkdir()
    rc, _, err = cli(capsys, "overlay", "link", tmp_path / "plain")
    assert rc == 2 and "not a Gnomonkit overlay" in err


# ---- no public remote, ever ----------------------------------------------------------------------
def test_no_code_path_creates_a_remote_or_pushes():
    for argv in (["gh", "repo", "create", "example-org/x", "--private"], ["gh.exe", "repo", "create", "x/y"],
                 ["git", "-C", "/tmp/x", "push", "-u", "origin", "main"], ["git", "remote", "add", "origin", "u"],
                 ["git", "-c", "a=b", "remote", "set-url", "origin", "u"]):
        assert overlay._forbidden(argv), argv
        with pytest.raises(RuntimeError):
            overlay._run(argv)
    for argv in (["git", "-C", "push", "status"], ["git", "remote", "-v"], ["gh", "repo", "view", "x/y"]):
        assert not overlay._forbidden(argv), argv
    src = open(overlay.__file__, encoding="utf-8").read()
    assert "--public" not in src
    commands = [line for line in src.splitlines() if re.search(r"gh repo create (%s|<)", line)]
    assert commands
    for line in commands:                         # every printed creation command makes a private one
        assert "--private" in line, line


def test_there_is_no_option_for_a_public_remote(tmp_path, capsys):
    with pytest.raises(SystemExit):
        gnomonkit.main(["overlay", "init", str(tmp_path / "o"), "--public"])
    capsys.readouterr()
    assert not (tmp_path / "o").exists()


def test_the_dispatcher_lists_overlay(capsys):
    with pytest.raises(SystemExit):
        gnomonkit.main(["--help"])
    assert "overlay" in capsys.readouterr().out


def test_the_template_holds_only_synthetic_examples():
    files = overlay.template_files("my-overlay")
    text = "\n".join(files.values())
    assert "{{" not in text
    assert re.search(r"[A-Za-z]:\\\\Users\\\\|/home/|/Users/", text) is None
    rules, problems = overlay.parse_denylist(files["private/gnomonkit-denylist.txt"])
    assert (rules, problems) == (0, [])
    assert overlay.skill_problem(os.path.join(overlay.TEMPLATE_DIR, "plugins", "overlay", "skills",
                                              "example-house-rule", "SKILL.md")) is None
