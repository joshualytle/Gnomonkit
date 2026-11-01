"""Folder trust: the levels per root, owners read from .git/config, the one-key edit of Claude Code's file, the
`gnomonkit trust` CLI and its terminal rule, `setup security`'s folder-trust section, and the `never` check
in session-dispatch.

Synthetic data only: a made-up home, Gnomonkit folder and Claude config folder under pytest's tmp_path
(CLAUDE_CONFIG_DIR, HOME and USERPROFILE point there, so the real Claude Code file is never read or written),
repositories that are a .git folder with a hand-written config, placeholder owners, and a fake `claude`.
Anything that looks like a credential is built from fragments at run time.
"""
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "plugins" / "core"
IJ = ROOT / "plugins" / "interjector"
DISPATCH = IJ / "skills" / "session-dispatch" / "dispatch.py"
for p in (str(CORE), str(CORE / "hooks")):
    if p not in sys.path:
        sys.path.insert(0, p)

import gk_grants as G  # noqa: E402
import gk_trust as T  # noqa: E402
import gnomonkit  # noqa: E402
import trust_autodiscover as AD  # noqa: E402
import trust_write as TW  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import grants as GC  # noqa: E402
from gnomonkit_cmds import trust as TC  # noqa: E402

WORD = "trust-0042"
GRANT_WORD = "grant-0042"
SCRUB = ("GNOMONKIT_", "CLAUDE_PLUGIN_OPTION_")
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_CONFIG_DIR")
ME = "5e551011-aaaa-4bbb-8ccc-000000000002"
GH = "https://github.com/"
# The user part of an SSH remote, built at runtime so URLs don't read as e-mail addresses to the leak gate.
SSH_USER = "git" + "@"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A made-up home, Gnomonkit folder and Claude config folder; no Claude Code session."""
    for k in list(os.environ):
        if k.startswith(SCRUB) or k in SESSION_VARS:
            monkeypatch.delenv(k, raising=False)
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    gk = h / ".gnomonkit"
    monkeypatch.setenv("GNOMONKIT_HOME", str(gk))
    cfg = tmp_path / "claude-config"
    cfg.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(TC, "confirm_word", lambda: WORD)
    monkeypatch.setattr(GC, "confirm_word", lambda kind="grant": GRANT_WORD)
    assert T.claude_json_path() == str(cfg / ".claude.json")
    return gk


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "projects"
    r.mkdir()
    return r


def F(p) -> str:
    return G.resolve(p)


def make_repo(path, url=None, extra=""):
    path = Path(path)
    (path / ".git").mkdir(parents=True, exist_ok=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    conf = "[core]\n\tbare = false\n"
    if url:
        conf += '[remote "origin"]\n\turl = %s\n' % url
    (path / ".git" / "config").write_text(conf + extra, encoding="utf-8")
    return path


def levels_file(*tables, version=1):
    lines = ["version = %d" % version]
    for t in tables:
        lines.append("\n[[trust]]")
        lines += ["%s = %s" % (k, json.dumps(v)) for k, v in t.items()]
    path = Path(G.grants_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def writable(root):
    """Levels under which every repository made in `root` qualifies (no remote, local_only on): the writer
    checks each repository against the levels right before its edit."""
    levels_file(trusted_root(root, local_only=True, auto=True))
    return root


def trusted_root(r, owners=("example-user",), **more):
    t = {"root": F(r), "level": "trusted", "owners": list(owners)}
    t.update(more)
    return t


def claude_file(projects=None, text=None, **top):
    path = Path(T.claude_json_path())
    if text is None:
        data = dict(top or {"numStartups": 3})
        data["projects"] = projects if projects is not None else {}
        text = json.dumps(data, indent=2)
    path.write_bytes(text.encode("utf-8"))
    return path


def trust_audit():
    p = Path(G.audit_path("trust"))
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def changed(old: str, new: str) -> tuple:
    """(the old text, the new text) between the longest common prefix and suffix."""
    i = 0
    while i < min(len(old), len(new)) and old[i] == new[i]:
        i += 1
    j = 0
    while j < min(len(old), len(new)) - i and old[-1 - j] == new[-1 - j]:
        j += 1
    return old[i:len(old) - j], new[i:len(new) - j]


def link_folder(link, target):
    """A directory link: a junction on Windows (no extra rights needed), a symlink elsewhere. Skips the test
    where neither can be made."""
    try:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, NotImplementedError, subprocess.CalledProcessError):
        pytest.skip("this machine can't make a directory link")


def state_of(*repos, root=""):
    """A folder-trust state that remembers `repos` (paths as trusted) as Gnomonkit's own."""
    st = {"version": TW.STATE_VERSION, "roots": {}, "trusted": {}}
    for repo in repos:
        st["trusted"][T.key_norm(repo)] = {"repo": repo, "root": root, "by": "sync", "at": "2026-01-01T00:00:00Z"}
    return st


# ---- owners, from the remote URL ------------------------------------------------------------------
@pytest.mark.parametrize("url,want", [
    ("https://github.com/example-user/widget.git", ("github.com", "example-user")),
    ("https://github.com/Example-Org/widget", ("github.com", "Example-Org")),
    ("http://www.github.com/example-user/widget/", ("github.com", "example-user")),
    ("ssh://" + SSH_USER + "github.com/example-user/widget.git", ("github.com", "example-user")),
    ("ssh://" + SSH_USER + "ssh.github.com:443/example-org/widget.git", ("github.com", "example-org")),
    ("git://github.com/example-user/widget.git", ("github.com", "example-user")),
    (SSH_USER + "github.com:example-user/widget.git", ("github.com", "example-user")),
    ("github.com:example-org/widget", ("github.com", "example-org")),
    ("https://git.example.com:8443/team-a/widget.git", ("git.example.com", "team-a")),
    (SSH_USER + "git.example.com:team-a/sub/widget.git", ("git.example.com", "team-a")),
    ("https://[2001:db8::1]/team-a/widget.git", ("2001:db8::1", "team-a")),
])
def test_owner_is_read_from_https_ssh_and_scp_like_remotes(url, want):
    assert T.parse_remote(url) == want


def test_credentials_in_a_remote_are_dropped():
    secret = "".join(["gh", "p_", "Zz9" * 12])
    url = "https://x-access-%s:%s%sgithub.com/example-user/widget.git" % ("to" + "ken", secret, "@")
    assert T.parse_remote(url) == ("github.com", "example-user")
    assert secret not in repr(T.parse_remote(url))


@pytest.mark.parametrize("url", ["", "file:///srv/git/widget.git", "/srv/git/widget.git", "../widget",
                                 "C:\\repos\\widget", "C:/repos/widget", "https://github.com/", "https://github.com/x"])
def test_local_paths_and_urls_without_an_owner_give_none(url):
    assert T.parse_remote(url) is None


def test_bare_owner_names_mean_github_and_host_names_mean_that_host():
    assert T.owner_listed("github.com", "Example-User", ["example-user"])
    assert not T.owner_listed("git.example.com", "example-user", ["example-user"])
    assert T.owner_listed("git.example.com", "team-a", ["git.example.com/Team-A"])
    assert T.owner_listed("github.com", "team-a", ["www.github.com/team-a"])
    assert not T.owner_listed("github.com", "team-a", ["git.example.com/team-a"])
    assert not T.owner_listed("github.com", "example-user-2", ["example-user"])


# ---- .git/config, read straight from the file -----------------------------------------------------
def test_git_config_parsing_handles_quotes_comments_and_old_spellings():
    conf = T.parse_git_config(
        '# a comment\n[core]\n\tbare = false\n'
        '[remote "origin"]\n\turl = "' + SSH_USER + 'github.com:example-user/widget.git" ; trailing comment\n'
        '\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
        '[remote.upstream]\n\turl = https://github.com/example-org/widget\n'
        '[remote "with \\"quote\\""]\n\turl = https://git.example.com/team-a/w # c\n'
        '[branch "main"] remote = origin\n')
    assert conf[("remote", "origin")]["url"] == [SSH_USER + "github.com:example-user/widget.git"]
    assert conf[("remote", "upstream")]["url"] == ["https://github.com/example-org/widget"]
    assert conf[("remote", 'with "quote"')]["url"] == ["https://git.example.com/team-a/w"]
    assert conf[("branch", "main")]["remote"] == ["origin"]


def test_remotes_of_a_worktree_come_from_the_main_repository(tmp_path):
    main = make_repo(tmp_path / "main", GH + "example-user/widget.git")
    wt_git = main / ".git" / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: %s\n" % wt_git, encoding="utf-8")
    assert T.is_repo(wt) and T.repo_root_of(wt / "src") == F(wt)
    assert T.remotes(wt) == {"origin": [GH + "example-user/widget.git"]}


def test_a_folder_is_a_repository_only_with_a_real_git_folder(tmp_path):
    (tmp_path / "empty-git" / ".git").mkdir(parents=True)                # no HEAD: not a repository
    (tmp_path / "bad-file").mkdir()
    (tmp_path / "bad-file" / ".git").write_text("not a gitdir line\n", encoding="utf-8")
    assert not T.is_repo(tmp_path / "empty-git") and not T.is_repo(tmp_path / "bad-file")
    assert T.is_repo(make_repo(tmp_path / "real"))


def test_qualifies_names_the_reason_and_never_the_url(root):
    r = T.TrustRoot(1, {}, str(root), "trusted", True, ["example-user"], False)
    local = T.TrustRoot(2, {}, str(root), "trusted", True, [], True)
    secret = "".join(["s3", "cr3t", "Q" * 20])
    cases = [
        (make_repo(root / "a", GH + "example-user/a"), r, True, "origin's owner example-user is listed"),
        (make_repo(root / "b", GH + "someone-else/b"), r, False, "origin's owner someone-else is not in owners"),
        (make_repo(root / "c"), r, False, "no remote"),
        (make_repo(root / "c2"), local, True, "no remote, and local_only is on"),
        (make_repo(root / "d", None, '[remote "fork"]\n\turl = %sexample-user/d\n' % GH), r, False, "no origin"),
        (make_repo(root / "e", "/srv/git/e.git"), r, False, "not a URL Gnomonkit reads an owner from"),
        (make_repo(root / "f", "https://user:%s%sgithub.com/example-user/f" % (secret, "@")), r, True, "is listed"),
        (make_repo(root / "g", GH + "example-user/g", '[remote "origin"]\n\turl = %ssomeone-else/g\n' % GH), r,
         False, "someone-else is not in owners"),
        (make_repo(root / "h", "https://git.example.com/example-user/h"), r, False,
         "git.example.com/example-user is not in owners"),
    ]
    for repo, tr, ok, why in cases:
        got_ok, got_why = T.qualifies(repo, tr)
        assert (got_ok, why in got_why) == (ok, True), (repo.name, got_ok, got_why)
        assert "://" not in got_why and secret not in got_why


# ---- the levels ---------------------------------------------------------------------------------
def test_levels_are_checked_and_any_problem_gives_no_roots(root):
    good = {"version": 1, "trust": [trusted_root(root, auto=True, local_only=False)]}
    assert T.check_trust(good)[0] == "ok"
    bad = [
        ({"trust": []}, "malformed", "version"),
        ({"version": 2, "trust": []}, "version", "version 2"),
        ({"version": 1, "trust": {"root": "x"}}, "malformed", "[[trust]] tables"),
        ({"version": 1, "trust": [{"root": "relative/path", "level": "trusted"}]}, "malformed", "absolute"),
        ({"version": 1, "trust": [{"root": F(root) + "/*", "level": "trusted"}]}, "malformed", "wildcards"),
        ({"version": 1, "trust": [{"root": F(root), "level": "always"}]}, "malformed", "level must be"),
        ({"version": 1, "trust": [{"root": F(root), "level": "trusted", "auto": "yes"}]}, "malformed", "true or false"),
        ({"version": 1, "trust": [{"root": F(root), "level": "trusted", "owners": "example-user"}]}, "malformed",
         "list of account names"),
        ({"version": 1, "trust": [{"root": F(root), "level": "trusted", "owners": ["not an owner!"]}]},
         "malformed", "not an account name"),
        ({"version": 1, "trust": [{"root": F(root), "level": "trusted", "colour": "red"}]}, "malformed",
         "unknown key colour"),
        ({"version": 1, "trust": [{"root": F(root), "level": "never"}, {"root": F(root) + "/", "level": "trusted"}]},
         "malformed", "same folder"),
    ]
    for data, status, words in bad:
        got, problems, roots = T.check_trust(data)
        assert (got, roots) == (status, []), data
        assert words in " ".join(problems), (data, problems)


def test_a_grants_file_that_is_not_toml_has_no_levels(home):
    path = Path(G.grants_path())
    path.parent.mkdir(parents=True)
    path.write_text("version = 1\n[[trust]\n", encoding="utf-8")
    lv = T.load_levels()
    assert lv.status == "malformed" and lv.roots == []
    assert T.load_levels(str(path) + ".missing").status == "missing"


def test_the_most_specific_root_wins(root):
    (root / "keep" / "deep").mkdir(parents=True)
    (root / "keep-sibling").mkdir()
    levels_file(trusted_root(root), {"root": F(root / "keep"), "level": "per-project"},
                {"root": F(root / "keep" / "deep"), "level": "never"})
    lv = T.load_levels()
    assert lv.ok
    assert T.governing(root / "a", lv).level == "trusted"
    assert T.governing(root / "keep", lv).level == "per-project"
    assert T.governing(root / "keep" / "x", lv).level == "per-project"
    assert T.governing(root / "keep" / "deep" / "x", lv).level == "never"
    assert T.governing(root / "keep-sibling", lv).level == "trusted"        # a name prefix is not a parent
    assert T.governing(root.parent, lv) is None
    if os.name == "nt":
        assert T.governing(str(root / "KEEP" / "x").upper(), lv).level == "per-project"


# ---- what Claude Code trusts: keyed on the repository root -----------------------------------------
def test_a_trusted_parent_does_not_cover_a_nested_repository(root):
    outer = make_repo(root / "outer", GH + "example-user/outer")
    inner = make_repo(outer / "libs" / "inner", GH + "example-user/inner")
    (outer / "docs").mkdir()
    (root / "plain" / "sub").mkdir(parents=True)
    claude_file({F(outer): {"hasTrustDialogAccepted": True}, F(root / "plain"): {"hasTrustDialogAccepted": True}})
    assert T.trust_state(outer) is True
    assert T.trust_state(outer / "docs") is True               # a plain folder inside the trusted repository
    assert T.trust_state(inner) is False                       # its own repository: its own entry
    assert T.trust_state(inner / "src") is False
    assert T.trust_state(root / "plain" / "sub") is True       # outside any repository the parent counts
    claude_file({F(root): {"hasTrustDialogAccepted": True}})
    assert T.trust_state(outer) is False                       # a trusted folder above a repository: not it
    claude_file(text="{not json")
    assert T.trust_state(outer) is None


def test_sync_finds_nested_repositories_only_when_asked_to_look_deeper(root):
    outer = make_repo(root / "outer", GH + "example-user/outer")
    inner = make_repo(outer / "inner", GH + "example-user/inner")
    levels_file(trusted_root(root))
    claude_file({F(outer): {"hasTrustDialogAccepted": True}})
    lv, cfg = T.load_levels(), T.read_claude_config()
    one, _ = T.candidates(lv, cfg.projects, depth=1)
    two, _ = T.candidates(lv, cfg.projects, depth=2)
    assert [c.repo for c in one] == [F(outer)]
    assert {c.repo: c.pending for c in two} == {F(outer): False, F(inner): True}


def test_candidates_skip_per_project_and_never_roots_and_wrong_owners(root):
    make_repo(root / "mine", GH + "example-user/mine")
    make_repo(root / "theirs", GH + "someone-else/theirs")
    make_repo(root / "keep" / "kept", GH + "example-user/kept")
    make_repo(root / "shut", GH + "example-user/shut")
    levels_file(trusted_root(root), {"root": F(root / "keep"), "level": "per-project"},
                {"root": F(root / "shut"), "level": "never"})
    found, complete = T.candidates(T.load_levels(), {}, depth=2)
    assert complete
    assert {Path(c.repo).name: c.pending for c in found} == {"mine": True, "theirs": False}


@pytest.mark.parametrize("how", ["symlink", "junction"])
def test_a_scan_does_not_follow_a_link_out_of_the_root(root, tmp_path, how):
    outside = make_repo(tmp_path / "outside" / "elsewhere", GH + "example-user/elsewhere")
    if how == "symlink":
        try:
            os.symlink(outside, root / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("this machine can't make a directory link")
    else:
        link_folder(root / "link", outside)        # a junction on Windows (a symlink elsewhere)
    assert T.find_repos(root, 1) == ([], True)


def test_a_repository_reached_through_a_link_inside_the_root_is_found_at_its_real_path(root):
    real = root / "real"
    make_repo(real / "inner", GH + "example-user/inner")
    link_folder(root / "alias", real)
    found, complete = T.find_repos(root, 2)
    assert complete and found == [F(real / "inner"), F(real / "inner")]       # by the link, then directly


# ---- the edit: one key, every other byte as it was ----------------------------------------------------
LAYOUT = """{
  "numStartups": 12,
  "big": 1.50,
  "neg": -0,
  "name": "caf\\u00e9 \u00e9",
  "nested": {"a": [1, 2, {"b": null}]},
  "projects": {
    "%(other)s": {
      "allowedTools": [],
      "hasTrustDialogAccepted": false,
      "history": [{"display": "x"}]
    },
    "%(repo)s": {
      "allowedTools": ["Read"],
      "hasTrustDialogAccepted": false,
      "mcpServers": {}
    }
  },
  "tail": true
}
"""


def test_setting_an_existing_false_changes_only_that_word(root):
    repo, other = make_repo(root / "repo"), make_repo(root / "other")
    old = LAYOUT % {"repo": F(repo), "other": F(other)}
    new, changes = TW.edit_text(old, [F(repo)], json.loads(old)["projects"])
    p = old.index('"hasTrustDialogAccepted": false', old.index(json.dumps(F(repo))))
    p += len('"hasTrustDialogAccepted": ')
    assert new == old[:p] + "true" + old[p + len("false"):]
    assert changes == [(F(repo), F(repo), "set key")]


@pytest.mark.parametrize("style", ["pretty", "compact", "crlf", "tabs", "empty-pretty", "empty-compact"])
def test_a_new_entry_is_one_insertion_laid_out_like_its_neighbours(root, style):
    repo, other = make_repo(root / "repo"), make_repo(root / "other")
    base = {"numStartups": 1, "big": 1.5, "projects": {F(other): {"hasTrustDialogAccepted": True, "x": [2]}}}
    if style.startswith("empty"):
        base["projects"] = {}
    text = {"pretty": lambda: json.dumps(base, indent=2), "compact": lambda: json.dumps(base, separators=(",", ":")),
            "crlf": lambda: json.dumps(base, indent=2).replace("\n", "\r\n") + "\r\n",
            "tabs": lambda: json.dumps(base, indent="\t"),
            "empty-pretty": lambda: json.dumps(base, indent=2) + "\n",
            "empty-compact": lambda: json.dumps(base, separators=(",", ":"))}[style]()
    text = text.replace("1.5", "1.50")          # a number re-serialising would rewrite
    new, changes = TW.edit_text(text, [F(repo)], json.loads(text)["projects"])
    want = json.loads(text)
    want["projects"][F(repo)] = {"hasTrustDialogAccepted": True}
    assert json.loads(new) == want and "1.50" in new
    old_mid, new_mid = changed(text, new)
    if style.startswith("empty"):
        assert old_mid in ("", "}")
    else:
        assert old_mid == ""
        assert re.fullmatch(r',\s*%s:\s*\{\s*"hasTrustDialogAccepted":\s*true\s*\}' % re.escape(json.dumps(F(repo))),
                            new_mid), new_mid
    if style == "crlf":
        assert "\n" not in new.replace("\r\n", "")
    if style == "compact":
        assert " " not in new_mid and "\n" not in new
    if style == "tabs":
        assert "\n\t\t" + json.dumps(F(repo)) in new


def test_an_entry_spelled_another_way_is_edited_in_place(root):
    repo = make_repo(root / "repo")
    key = F(repo) + "/"
    if os.name == "nt":
        key = key.replace("/", "\\").upper()
    text = json.dumps({"projects": {key: {"allowedTools": []}}}, indent=2)
    new, changes = TW.edit_text(text, [F(repo)], json.loads(text)["projects"])
    assert changes == [(F(repo), key, "added key")]
    assert json.loads(new) == {"projects": {key: {"allowedTools": [], "hasTrustDialogAccepted": True}}}


@pytest.mark.skipif(os.name != "nt", reason="drive-letter keys exist only on Windows")
def test_a_new_key_follows_the_files_own_spelling_on_windows(root):
    repo = make_repo(root / "repo")
    back = {"C:\\one": {}, "C:\\two": {}, "d:/three": {}}
    assert TW.spelling(F(repo), back) == F(repo).replace("/", "\\")
    fwd = {"c:/one": {}, "c:/two": {}}
    assert TW.spelling(F(repo), fwd) == F(repo)[0].lower() + F(repo)[1:]


def test_layouts_it_cannot_edit_safely_are_refused(root):
    repo = make_repo(root / "repo")
    key = json.dumps(F(repo))
    for text in ('{"projects": {%s: {}, %s: {}}}' % (key, key), '{"projects": {}, "projects": {}}', '{"projects": {}} x'):
        with pytest.raises(ValueError):
            TW.edit_text(text, [F(repo)], {})


# ---- the write: format check, backup, re-read, atomic swap, read back ---------------------------------
def test_set_trusted_backs_up_first_and_writes_only_the_key(root, writable):
    repo, other = make_repo(root / "repo"), make_repo(root / "other")
    path = claude_file(text=LAYOUT % {"repo": F(repo), "other": F(other)})
    old = path.read_bytes()
    seen = {}

    def before_swap():
        seen["file"] = path.read_bytes()
        seen["backups"] = [p.read_bytes() for p in Path(TW.backup_dir()).iterdir()]

    res = TW.set_trusted([TW.Item(F(repo), F(root), "test")], by="sync", path=str(path), before_swap=before_swap)
    assert seen == {"file": old, "backups": [old]}                    # the backup exists before the swap
    new = path.read_bytes()
    assert changed(old.decode(), new.decode()) == ("fals", "tru")    # "false" -> "true", nothing else
    assert [r["change"] for r in res.changed] == ["set key"] and res.failed == []
    assert [e["event"] for e in trust_audit()] == ["trusted"]
    assert not [p for p in os.listdir(path.parent) if p.endswith(".tmp")]
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(res.backups[0]).st_mode) == 0o600


def test_a_change_between_the_read_and_the_write_aborts(root, writable):
    repo = make_repo(root / "repo")
    path = claude_file({})
    theirs = json.dumps({"projects": {}, "numStartups": 99}, indent=2).encode()

    def claude_code_writes():
        path.write_bytes(theirs)

    with pytest.raises(TW.WriteError) as e:
        TW.set_trusted([TW.Item(F(repo))], by="auto", path=str(path), before_swap=claude_code_writes)
    assert e.value.kind == "changed"
    assert path.read_bytes() == theirs                                # their write stands, ours never happened
    assert not [p for p in os.listdir(path.parent) if p.endswith(".tmp")]
    assert trust_audit()[-1]["event"] == "aborted"


def test_a_replace_that_has_to_wait_reads_the_file_again_before_each_try(root, writable, monkeypatch):
    """Windows refuses os.replace while another process has the file open; that process may be writing it.
    Each try reads the file again first, so a write made while the replace waited is never overwritten."""
    repo = make_repo(root / "repo")
    path = claude_file({})
    theirs = json.dumps({"projects": {}, "concurrentWrite": 1}, indent=2).encode()
    real_replace = os.replace
    tries = []

    def held_open_then_written(src, dst):
        tries.append(dst)
        if len(tries) == 1:
            path.write_bytes(theirs)          # the other process writes, then lets go of the file
            raise PermissionError(13, "the file is open in another process")
        real_replace(src, dst)

    monkeypatch.setattr(TW, "REPLACE_RETRY", True)
    monkeypatch.setattr(TW.os, "replace", held_open_then_written)
    with pytest.raises(TW.WriteError) as e:
        TW.set_trusted([TW.Item(F(repo))], by="sync", path=str(path))
    assert e.value.kind == "changed" and len(tries) == 1
    assert path.read_bytes() == theirs                                # their write stands
    assert not [p for p in os.listdir(path.parent) if p.endswith(".tmp")]
    assert [e["event"] for e in trust_audit()] == ["aborted"]


def test_a_replace_that_has_to_wait_goes_ahead_when_nothing_changed(root, writable, monkeypatch):
    repo = make_repo(root / "repo")
    path = claude_file({})
    real_replace = os.replace
    tries = []

    def held_open_briefly(src, dst):
        tries.append(dst)
        if len(tries) == 1:
            raise PermissionError(13, "the file is open in another process")
        real_replace(src, dst)

    monkeypatch.setattr(TW, "REPLACE_RETRY", True)
    monkeypatch.setattr(TW.os, "replace", held_open_briefly)
    res = TW.set_trusted([TW.Item(F(repo))], by="sync", path=str(path))
    assert len(tries) == 2 and res.changed and T.is_trusted(T.read_claude_config().projects, repo)


def test_a_key_that_does_not_stick_is_tried_once_more(root, writable):
    repo = make_repo(root / "repo")
    path = claude_file({})
    old = path.read_bytes()

    def claude_code_writes_back_once(attempt):
        if attempt == 1:
            path.write_bytes(old)

    res = TW.set_trusted([TW.Item(F(repo))], by="auto", path=str(path), after_swap=claude_code_writes_back_once)
    assert res.attempts == 2 and [r["attempt"] for r in res.changed] == [2] and res.failed == []
    assert T.is_trusted(T.read_claude_config().projects, repo)
    assert [e["event"] for e in trust_audit()] == ["lost", "trusted"]


def test_a_key_that_never_sticks_is_reported_not_retried_forever(root, writable):
    repo = make_repo(root / "repo")
    path = claude_file({})
    old = path.read_bytes()
    res = TW.set_trusted([TW.Item(F(repo))], by="auto", path=str(path), after_swap=lambda _a: path.write_bytes(old))
    assert res.attempts == 2 and res.changed == [] and [r for r, _ in res.failed] == [F(repo)]
    assert [e["event"] for e in trust_audit()] == ["lost", "lost", "not-stuck"]


@pytest.mark.parametrize("text", ['{"projects": [', '{"projects": []}', "[]", '{"numStartups": 1}', "\ufeff{\"projects\": {}}",
                                  '{"projects": {"x": 1}}'])
def test_a_file_it_does_not_recognise_is_left_alone(root, writable, text):
    repo = make_repo(root / "repo")
    path = claude_file(text=text)
    if text == '{"projects": {"x": 1}}':          # a recognised file whose entry for this repository is no object
        path = claude_file(text='{"projects": {%s: 1}}' % json.dumps(F(repo)))
    old = path.read_bytes()
    with pytest.raises(TW.WriteError) as e:
        TW.set_trusted([TW.Item(F(repo))], by="sync", path=str(path))
    assert e.value.kind == "format" and path.read_bytes() == old
    assert not os.path.exists(TW.backup_dir()) or os.listdir(TW.backup_dir()) == []
    assert trust_audit()[-1]["event"] == "refused"


def test_no_file_means_nothing_is_created(root, writable):
    with pytest.raises(TW.WriteError):
        TW.set_trusted([TW.Item(F(make_repo(root / "repo")))], by="sync")
    assert not os.path.exists(T.claude_json_path())


def test_only_the_last_ten_backups_are_kept(root, writable):
    path = claude_file({})
    for n in range(12):
        TW.set_trusted([TW.Item(F(make_repo(root / ("r%02d" % n))))], by="sync", path=str(path))
    assert len(os.listdir(TW.backup_dir())) == TW.BACKUPS
    assert len(T.read_claude_config().projects) == 12


def test_one_writer_at_a_time_and_a_stale_lock_is_replaced(root, writable):
    repo = make_repo(root / "repo")
    path = claude_file({})
    lock = Path(T.trust_home()) / "write.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("1", encoding="utf-8")
    with pytest.raises(TW.WriteError) as e:
        TW.set_trusted([TW.Item(F(repo))], by="auto", path=str(path))
    assert e.value.kind == "busy" and json.loads(path.read_text()) == {"numStartups": 3, "projects": {}}
    old = time.time() - TW.LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    assert TW.set_trusted([TW.Item(F(repo))], by="auto", path=str(path)).changed
    assert not lock.exists() and not [f for f in os.listdir(lock.parent) if "lock" in f]


def test_a_stale_lock_taken_over_by_another_process_first_is_left_to_it(root, writable, monkeypatch):
    lock = Path(T.trust_home()) / "write.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("stale", encoding="utf-8")
    old = time.time() - TW.LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    real_rename = os.rename

    def another_process_wins(src, dst):
        # Between this process judging the lock stale and moving it, another one took it over.
        lock.unlink()
        lock.write_text("someone else", encoding="utf-8")
        real_rename(src, dst)

    monkeypatch.setattr(TW.os, "rename", another_process_wins)
    with pytest.raises(TW.WriteError) as e:
        with TW._Lock():
            pass
    assert e.value.kind == "busy"
    assert lock.read_text(encoding="utf-8") == "someone else"           # their lock is put back, untouched
    assert [f for f in os.listdir(lock.parent) if "lock" in f] == ["write.lock"]


def test_a_writer_never_removes_a_lock_that_is_not_its_own(root, writable):
    lock = Path(T.trust_home()) / "write.lock"
    with TW._Lock():
        lock.write_text("someone else", encoding="utf-8")                # it was taken over meanwhile
    assert lock.read_text(encoding="utf-8") == "someone else"


def test_a_write_past_its_deadline_defers_everything_and_writes_nothing(root, writable):
    repo = make_repo(root / "repo")
    path = claude_file({})
    before = path.read_bytes()
    res = TW.set_trusted([TW.Item(F(repo))], by="auto", path=str(path), deadline=time.monotonic() - 1)
    assert res.deferred == [F(repo)] and not res.changed and not res.refused and not res.failed
    assert path.read_bytes() == before and not os.path.exists(TW.backup_dir()) and trust_audit() == []


def test_levels_that_break_before_the_write_stop_it(root, writable):
    repo = make_repo(root / "repo")
    path = claude_file({})
    before = path.read_bytes()
    Path(G.grants_path()).write_text('version = 1\n\n[[trust]]\nroot = "relative"\nlevel = "trusted"\n',
                                     encoding="utf-8")
    with pytest.raises(TW.WriteError) as e:
        TW.set_trusted([TW.Item(F(repo))], by="sync", path=str(path))
    assert e.value.kind == "levels" and path.read_bytes() == before
    assert [x["event"] for x in trust_audit()] == ["refused"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_the_files_mode_is_kept(root, writable):
    path = claude_file({})
    os.chmod(path, 0o640)
    TW.set_trusted([TW.Item(F(make_repo(root / "repo")))], by="sync", path=str(path))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


# ---- the CLI ------------------------------------------------------------------------------------
class Term(io.StringIO):
    def isatty(self):
        return True


class Pipe(io.StringIO):
    def isatty(self):
        return False


def at_terminal(monkeypatch, answers=""):
    out = Term()
    monkeypatch.setattr(C, "_is_console", lambda stream: True)   # stands in for a console on Windows
    monkeypatch.setattr(sys, "stdin", Term(answers))
    monkeypatch.setattr(sys, "stdout", out)
    return out


@pytest.fixture
def tree(root):
    """A trusted root with one of each kind of repository under it."""
    t = {"mine": make_repo(root / "mine", GH + "example-user/mine.git"),
         "org": make_repo(root / "org", SSH_USER + "github.com:example-org/org.git"),
         "theirs": make_repo(root / "theirs", GH + "someone-else/theirs"),
         "local": make_repo(root / "local"),
         "kept": make_repo(root / "keep" / "kept", GH + "example-user/kept"),
         "shut": make_repo(root / "shut", GH + "example-user/shut")}
    (root / "plain").mkdir()
    levels_file(trusted_root(root, ["example-user", "example-org"], auto=True),
                {"root": F(root / "keep"), "level": "per-project"}, {"root": F(root / "shut"), "level": "never"})
    claude_file({F(t["theirs"]): {"hasTrustDialogAccepted": False, "allowedTools": []}}, numStartups=5, theme="x")
    return t


def test_sync_refuses_without_a_terminal_and_changes_nothing(tree, home):
    before = Path(T.claude_json_path()).read_bytes()
    r = subprocess.run([sys.executable, str(CORE / "gnomonkit.py"), "trust", "sync"], input=WORD + "\n",
                       capture_output=True, text=True, env=dict(os.environ),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 2 and "refused" in r.stderr and "terminal" in r.stderr
    assert Path(T.claude_json_path()).read_bytes() == before
    assert not os.path.exists(T.trust_home())


def test_sync_refuses_a_fake_non_terminal_in_process(tree, monkeypatch):
    before = Path(T.claude_json_path()).read_bytes()
    monkeypatch.setattr(sys, "stdin", Term(WORD + "\n"))
    monkeypatch.setattr(sys, "stdout", Pipe())
    assert gnomonkit.main(["trust", "sync"]) == 2
    assert Path(T.claude_json_path()).read_bytes() == before


def test_sync_at_a_terminal_trusts_what_qualifies_and_nothing_else(tree, monkeypatch):
    path = Path(T.claude_json_path())
    before = json.loads(path.read_text(encoding="utf-8"))
    out = at_terminal(monkeypatch, WORD + "\n")
    assert gnomonkit.main(["trust", "sync"]) == 0, out.getvalue()
    text = out.getvalue()
    assert "origin's owner example-user is listed" in text and "someone-else is not in owners" in text
    after = json.loads(path.read_text(encoding="utf-8"))
    want = json.loads(json.dumps(before))
    want["projects"][F(tree["mine"])] = {"hasTrustDialogAccepted": True}
    want["projects"][F(tree["org"])] = {"hasTrustDialogAccepted": True}
    assert after == want
    assert [e["by"] for e in trust_audit()] == ["sync", "sync"]
    assert set(TW.read_state()["trusted"]) == {G.norm(tree["mine"]), G.norm(tree["org"])}
    out = at_terminal(monkeypatch, "")
    assert gnomonkit.main(["trust", "sync"]) == 0 and "Nothing to trust" in out.getvalue()


def test_sync_needs_the_word(tree, monkeypatch):
    before = Path(T.claude_json_path()).read_bytes()
    at_terminal(monkeypatch, "yes\n")
    assert gnomonkit.main(["trust", "sync"]) == 2
    assert Path(T.claude_json_path()).read_bytes() == before


def during_the_prompt(monkeypatch, change):
    """Run `change` while the person reads the list and types the word: after the scan, before the write."""
    real = C.require_tty

    def typed(word, *a, **k):
        change()
        return real(word, *a, **k)

    monkeypatch.setattr(C, "require_tty", typed)


@pytest.mark.parametrize("target", ["shut", "theirs"])
def test_a_folder_swapped_for_a_link_while_the_word_is_typed_is_not_trusted(tree, monkeypatch, tmp_path, target):
    """The scan lists mine. Before the word is typed, mine is swapped for a junction (a symlink off Windows)
    to a repository under the `never` root, or to one whose owner is not listed. The writer checks each
    repository again inside its lock: mine is refused, and neither path is trusted."""
    link_folder(tmp_path / "probe", tree[target])          # skip now, not halfway, where links can't be made
    mine, target_path = F(tree["mine"]), F(tree[target])

    def swap():
        shutil.rmtree(tree["mine"])
        link_folder(tree["mine"], tree[target])

    during_the_prompt(monkeypatch, swap)
    out = at_terminal(monkeypatch, WORD + "\n")
    assert gnomonkit.main(["trust", "sync"]) == 1
    text = out.getvalue()
    assert "Not trusted: %s (the folder no longer leads to itself" % mine in text, text
    projects = T.read_claude_config().projects
    assert T.entry_keys(projects, mine, resolved=True) == []
    assert not T.is_trusted(projects, target_path, resolved=True)
    assert T.is_trusted(projects, F(tree["org"]), resolved=True)       # the rest of the list goes ahead
    assert [(e["event"], e["repo"]) for e in trust_audit() if e["event"] == "refused"] == [("refused", mine)]
    assert T.key_norm(mine) not in TW.read_state()["trusted"]


def _owner_changed(t):
    make_repo(t["mine"], GH + "someone-else/mine")


def _level_changed(t):
    levels_file(trusted_root(t["mine"].parent, ["example-user", "example-org"], auto=True),
                {"root": F(t["mine"]), "level": "never"})


def _not_a_repository(t):
    shutil.rmtree(t["mine"] / ".git")


@pytest.mark.parametrize("change,why", [
    (_owner_changed, "someone-else is not in owners"),
    (_level_changed, "it is not under a root at the trusted level now (level never)"),
    (_not_a_repository, "it is no longer a git repository"),
], ids=["owner", "level", "not-a-repository"])
def test_a_repository_that_stops_qualifying_while_the_word_is_typed_is_not_trusted(tree, monkeypatch, change, why):
    mine = F(tree["mine"])
    during_the_prompt(monkeypatch, lambda: change(tree))
    out = at_terminal(monkeypatch, WORD + "\n")
    assert gnomonkit.main(["trust", "sync"]) == 1
    assert "Not trusted: %s (" % mine in out.getvalue() and why in out.getvalue(), out.getvalue()
    projects = T.read_claude_config().projects
    assert T.entry_keys(projects, mine, resolved=True) == []
    assert T.is_trusted(projects, F(tree["org"]), resolved=True)


def test_sync_without_levels_or_with_a_bad_file_changes_nothing(root, monkeypatch):
    make_repo(root / "mine", GH + "example-user/mine")
    claude_file({})
    at_terminal(monkeypatch, WORD + "\n")
    assert gnomonkit.main(["trust", "sync"]) == 2                            # no levels yet
    levels_file(trusted_root(root))
    path = claude_file(text='{"projects": []}')
    at_terminal(monkeypatch, WORD + "\n")
    assert gnomonkit.main(["trust", "sync"]) == 2 and path.read_text() == '{"projects": []}'


def test_status_only_reads(tree, monkeypatch, capsys):
    before = Path(T.claude_json_path()).read_bytes()
    assert gnomonkit.main(["trust", "status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    rows = {Path(x["repo"]).name: (x["trusted"], x["qualifies"]) for r in data["roots"] for x in r["repos"]}
    assert rows == {"mine": (False, True), "org": (False, True), "theirs": (False, False), "local": (False, False),
                    "kept": (False, None), "shut": (False, None)}
    assert gnomonkit.main(["trust"]) == 0
    text = capsys.readouterr().out
    assert "pending" in text and "level never" in text and "level per-project" in text and "trust sync" in text
    assert Path(T.claude_json_path()).read_bytes() == before and not os.path.exists(T.trust_home())


def test_status_json_names_trusted_folders_no_root_covers_and_missing_roots(root, tmp_path, capsys):
    make_repo(root / "mine", GH + "example-user/mine")
    gone = tmp_path / "removed-root"
    levels_file(trusted_root(root), {"root": str(gone), "level": "per-project"})
    loose, other = tmp_path / "elsewhere" / "loose", tmp_path / "elsewhere" / "other"
    claude_file({F(root / "mine"): {"hasTrustDialogAccepted": True},       # under a root: covered
                 F(loose): {"hasTrustDialogAccepted": True},
                 F(other): {"hasTrustDialogAccepted": False},              # not trusted: not listed
                 str(tmp_path / "odd"): "not an entry"})
    assert gnomonkit.main(["trust", "status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["uncovered"] == [F(loose)]
    assert [(r["level"], r["exists"]) for r in data["roots"]] == [("trusted", True), ("per-project", False)]
    assert gnomonkit.main(["trust", "status"]) == 0
    out = capsys.readouterr().out
    assert "under no root, so no level applies (1):\n  " + F(loose) + "\n" in out and F(other) not in out
    claude_file({F(tmp_path / "many" / ("f%02d" % i)): {"hasTrustDialogAccepted": True} for i in range(12)})
    assert gnomonkit.main(["trust", "status"]) == 0
    out = capsys.readouterr().out
    assert "(12):" in out and "f09" in out and "f10" not in out and "and 2 more (--json lists them all)" in out
    claude_file(text="{not json")
    assert gnomonkit.main(["trust", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["uncovered"] == []


# ---- the session-start hook: its deadline, where it puts keys back, how much it says -----------------
class Clock:
    """time.monotonic for the hook: one second passes with every look at the clock."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        self.t += 1
        return self.t


def test_the_hook_stops_at_its_deadline_and_leaves_the_rest_for_the_next_start(root, monkeypatch):
    make_repo(root / "mine", GH + "example-user/mine")
    levels_file(trusted_root(root, auto=True))
    path = claude_file({})
    before = path.read_bytes()
    assert AD.run({"source": "startup"}, time.monotonic() - 1) == []            # no time to look
    assert path.read_bytes() == before and TW.read_state()["roots"] == {}
    reserve = AD.WRITE_RESERVE_S
    monkeypatch.setattr(AD, "WRITE_RESERVE_S", -60.0)                            # time to look, none to write
    assert AD.run({"source": "startup"}, time.monotonic() - 1) == []
    assert path.read_bytes() == before
    assert TW.read_state()["roots"] == {}                 # not recorded as scanned, so it is scanned again
    monkeypatch.setattr(AD, "WRITE_RESERVE_S", reserve)     # (not undo(): that would drop the made-up home)
    assert AD.run({"source": "startup"})
    assert T.is_trusted(T.read_claude_config().projects, F(root / "mine"), resolved=True)


def test_putting_keys_back_carries_on_where_the_last_start_stopped(root, monkeypatch):
    levels_file(trusted_root(root, auto=True))
    repos = [F(make_repo(root / ("r%d" % n), GH + "example-user/r%d" % n)) for n in range(1, 7)]
    claude_file({})                                        # Claude Code lost every key
    st = state_of(*repos, root=F(root))
    lv, cfg = T.load_levels(), T.read_claude_config()
    # The clock: the pass starts at 1 with the deadline at 10, so it may look until 5.5: four repositories.
    monkeypatch.setattr(AD, "time", Clock())
    items, _, _ = AD.plan(lv, cfg, st, 10.0)
    assert [it.repo for it in items if it.by == "reapply"] == repos[:4]
    assert st["cursor"] == T.key_norm(repos[3])
    monkeypatch.setattr(AD, "time", Clock())
    items, _, _ = AD.plan(lv, cfg, st, 10.0)
    assert [it.repo for it in items if it.by == "reapply"] == [repos[4], repos[5], repos[0], repos[1]]
    assert st["cursor"] == T.key_norm(repos[1])


def test_keys_are_put_back_only_directly_inside_an_auto_root(root):
    """trust sync --depth 2 can trust a nested repository; the hook looks one level down, so it doesn't put
    that one's key back (trust sync does) and stops remembering it."""
    levels_file(trusted_root(root, auto=True))
    direct = F(make_repo(root / "direct", GH + "example-user/direct"))
    inner = F(make_repo(root / "outer" / "inner", GH + "example-user/inner"))
    claude_file({})
    items, _, forget = AD.plan(T.load_levels(), T.read_claude_config(), state_of(direct, inner, root=F(root)),
                               time.monotonic() + 60)
    assert [it.repo for it in items if it.by == "reapply"] == [direct]
    assert forget == [T.key_norm(inner)]


def test_a_key_is_not_put_back_through_a_link_made_since(root, tmp_path):
    levels_file(trusted_root(root, auto=True))
    elsewhere = make_repo(tmp_path / "elsewhere" / "real", GH + "example-user/real")
    was = F(root) + "/alias"                      # trusted when it was a repository of its own
    link_folder(root / "alias", elsewhere)
    claude_file({})
    items, _, forget = AD.plan(T.load_levels(), T.read_claude_config(), state_of(was, root=F(root)),
                               time.monotonic() + 60)
    assert not [it for it in items if it.by == "reapply"] and forget == [T.key_norm(was)]
    assert not [it for it in items if T.key_norm(it.repo) == T.key_norm(F(elsewhere))]


def test_the_first_start_shows_at_most_ten_lines_and_how_many_more():
    recs = [{"repo": "/projects/r%02d" % n, "root": "/projects", "reason": "it qualifies", "by": "auto"}
            for n in range(15)]
    lines = AD.lines_for(recs)
    assert len(lines) == AD.MAX_LINES + 1 == 11
    assert lines[-1].startswith("...and 5 more;") and G.audit_path("trust") in lines[-1]
    assert lines[:10] == AD.lines_for(recs[:10])
    assert len(AD.lines_for(recs[:10])) == 10                 # exactly ten: no "more" line


def hook(env=None):
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(CORE / "hooks" / "trust_autodiscover.py")], input='{"source": "startup"}',
                       capture_output=True, text=True, encoding="utf-8", env=env or dict(os.environ),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 0, r.stderr
    msg = json.loads(r.stdout)["systemMessage"] if r.stdout.strip() else ""
    return msg, time.monotonic() - t0


def test_thousands_of_repositories_and_entries_stay_inside_the_hooks_time(root, tmp_path):
    """2000 new repositories under one root and 3000 other entries in Claude Code's file. Each start stays
    well inside the 10 s hook timeout and shows at most eleven lines; the repositories are all trusted within
    a few starts, the other entries are as they were, and a start with nothing to do is quick."""
    n_repos, n_other = 2000, 3000
    levels_file(trusted_root(root, auto=True))
    for n in range(n_repos):
        make_repo(root / ("repo-%04d" % n), GH + "example-user/repo-%04d" % n)
    others = {F(tmp_path) + "/other/p%04d" % n: {"allowedTools": [], "hasTrustDialogAccepted": n % 2 == 0,
                                                 "lastCost": n} for n in range(n_other)}
    path = claude_file(dict(others))
    limit = AD.BUDGET_S + 3.0              # the budget, plus starting Python and reading the files
    runs = []
    for _ in range(8):
        msg, took = hook()
        runs.append(took)
        assert took < limit, runs
        assert msg.count("\n") <= AD.MAX_LINES, msg[-300:]
        if not msg:
            break
    projects = json.loads(path.read_text(encoding="utf-8"))["projects"]
    assert all(projects.get(F(root / ("repo-%04d" % n)), {}).get("hasTrustDialogAccepted") is True
               for n in range(n_repos)), runs
    assert {k: v for k, v in projects.items() if k in others} == others
    assert len(projects) == n_repos + n_other
    msg, took = hook()                                          # nothing left to do
    assert msg == "" and took < 3.0, took


# ---- the owner's real-session check (tests/manual): it does nothing unless asked ---------------------------
MANUAL = ROOT / "tests" / "manual" / "trust_real_session.py"


def manual(*args):
    return subprocess.run([sys.executable, str(MANUAL), *args], input="", capture_output=True, text=True,
                          encoding="utf-8", env=dict(os.environ),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


@pytest.mark.skipif(not MANUAL.exists(), reason="the manual checks are not part of the public export")
def test_the_real_session_check_does_nothing_unless_asked(home, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("GNOMONKIT_SCRATCH_ROOT", str(scratch))
    path = claude_file({})
    before = path.read_bytes()
    r = manual()
    assert r.returncode == 2 and "refused" in r.stderr and "--real" in r.stderr, r.stderr
    r = manual("--real")                                        # no terminal here, as for an agent
    assert r.returncode == 2 and "terminal" in r.stderr, r.stderr
    r = manual("--dry-run")
    assert r.returncode == 0, r.stderr
    assert [n for n in range(1, 9) if ("  %d. " % n) in r.stdout] == list(range(1, 8)), r.stdout
    assert str(path) in r.stdout and "--bg" in r.stdout
    assert path.read_bytes() == before and not scratch.exists() and not home.exists()


# ---- gnomonkit setup security: the folder-trust section ----------------------------------------------
def test_setup_security_writes_levels_beside_the_grants(root, monkeypatch, home):
    (root / "vault").mkdir()
    gpath = Path(G.grants_path())
    gpath.parent.mkdir(parents=True)
    gpath.write_text('version = 1\n\n[[grant]]\nfrom = "%s/*"\nto = ["same-project"]\nactions = ["spawn"]\n'
                     'agents = ["claude"]\n' % F(root), encoding="utf-8")
    monkeypatch.setenv("GNOMONKIT_PROJECTS_ROOT", str(root))
    answers = ["", "trusted", "", "example-user, example-org", "", str(root / "vault"), "never", "", WORD]
    out = at_terminal(monkeypatch, "\n".join(answers) + "\n")
    assert TC.security_setup(None) == 0, out.getvalue()
    lv = T.load_levels()
    assert lv.ok and [(r.root, r.level, r.auto, r.owners, r.local_only) for r in lv.roots] == [
        (F(root), "trusted", True, ["example-user", "example-org"], False), (F(root / "vault"), "never", False, [],
                                                                             False)]
    assert len(G.load().grants) == 1                                       # the grants are kept
    assert Path(str(gpath) + ".bak").exists()
    assert trust_audit()[-1]["event"] == "levels-setup"
    assert "trust sync" in out.getvalue()
    # Run again: the levels are shown and kept unless the user says to replace them.
    before = gpath.read_text(encoding="utf-8")
    at_terminal(monkeypatch, "n\n")
    assert TC.security_setup(None) == 0 and gpath.read_text(encoding="utf-8") == before


def test_grants_changes_keep_the_levels(root, home):
    levels_file(trusted_root(root, auto=True))
    loaded, data = GC._writable()
    data["grant"] = [GC.build_grant(F(root) + "/*", ["same-project"], actions=["spawn"])]
    GC.save(data, {"event": "test"})
    assert T.load_levels().ok and len(T.load_levels().roots) == 1 and len(G.load().grants) == 1


def test_setup_security_trust_section_refuses_without_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", Pipe("\n"))
    monkeypatch.setattr(sys, "stdout", Pipe())
    with pytest.raises(C.CommandError):
        TC.security_setup(None)
    assert not os.path.exists(G.grants_path())


def test_setup_security_runs_the_trust_section_after_the_grants():
    from gnomonkit_cmds import setup
    names = [name for _order, name, _mod in setup._security_modules()]
    assert names.index("grants") < names.index("trust")


# ---- session-dispatch: `never` beats any grant ------------------------------------------------------
FAKE = r'''import json, os, sys
state = os.environ["FAKE_CLAUDE_STATE"]
args = sys.argv[1:]
with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"args": args}) + "\n")
if args[:1] == ["agents"]:
    p = os.path.join(state, "agents.json")
    sys.stdout.write(open(p, encoding="utf-8").read() if os.path.exists(p) else "[]")
elif "--bg" in args:
    sys.stdout.write("bg42\n")
else:
    sys.exit(3)
'''


@pytest.fixture
def dispatch(tmp_path, home, root):
    state = tmp_path / "state"
    state.mkdir()
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE, encoding="utf-8")
    for name in ("alpha", "beta"):
        (root / name).mkdir()
    env = dict(os.environ)
    env.update(FAKE_CLAUDE_STATE=str(state), GNOMONKIT_CLAUDE_CMD=json.dumps([sys.executable, str(fake)]),
               GNOMONKIT_PROJECTS_ROOT=str(root), CLAUDECODE="1", CLAUDE_CODE_SESSION_ID=ME)
    me = {"pid": 1, "cwd": str(root / "alpha"), "kind": "interactive", "id": "me000001", "sessionId": ME,
          "name": "alpha:work", "status": "busy", "state": "working"}
    (state / "agents.json").write_text(json.dumps([me]), encoding="utf-8")
    gpath = Path(G.grants_path())
    gpath.parent.mkdir(parents=True, exist_ok=True)

    def grants_and_levels(*tables):
        text = ('version = 1\n\n[[grant]]\nfrom = "%s/**"\nto = ["%s/**"]\nactions = ["spawn", "handoff"]\n'
                'agents = ["claude"]\n' % (F(root), F(root)))
        for t in tables:
            text += "\n[[trust]]\n" + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in t.items())
        gpath.write_text(text, encoding="utf-8")

    def run(*args, plain=False, stdin=""):
        e = {k: v for k, v in env.items() if not (plain and k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID"))}
        r = subprocess.run([sys.executable, str(DISPATCH), *args], capture_output=True, text=True, env=e,
                           input=stdin, encoding="utf-8", cwd=str(root / "alpha"),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, r.stdout, r.stderr

    def bg_calls():
        p = state / "calls.jsonl"
        rows = [json.loads(x)["args"] for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return [a for a in rows if "--bg" in a]

    return {"run": run, "levels": grants_and_levels, "bg": bg_calls}


def dispatch_audit():
    p = Path(G.audit_path())
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_never_beats_a_grant_that_allows(dispatch, root):
    dispatch["levels"](trusted_root(root), {"root": F(root / "beta"), "level": "never"})
    rc, out, err = dispatch["run"]("spawn", "alpha", "--task", "t", "--prompt", "synthetic")
    assert rc == 0, err                                                   # the grant allows alpha
    rc, out, err = dispatch["run"]("spawn", "beta", "--task", "t", "--prompt", "synthetic")
    assert rc == 2 and "never" in err and "whatever the grants say" in err
    rc, _, err = dispatch["run"]("handoff", "beta", "--task", "t", stdin="## Next\nShip.\n")
    assert rc == 2 and "never" in err
    assert len(dispatch["bg"]()) == 1                                     # only alpha's session started
    refusals = [e for e in dispatch_audit() if e.get("rule") == "folder-trust level"]
    assert [(e["action"], e["decision"]) for e in refusals] == [("spawn", "refused"), ("handoff", "refused")]


def test_never_holds_at_a_plain_terminal_and_in_a_dry_run(dispatch, root):
    dispatch["levels"]({"root": F(root / "beta"), "level": "never"})
    rc, _, err = dispatch["run"]("spawn", "beta", "--task", "t", "--prompt", "synthetic", plain=True)
    assert rc == 2 and "never" in err
    logged = len(dispatch_audit())
    rc, _, err = dispatch["run"]("spawn", "beta", "--task", "t", "--prompt", "synthetic", "--dry-run")
    assert rc == 2 and err.startswith("dry run: refused") and len(dispatch_audit()) == logged
    assert dispatch["bg"]() == []


def test_levels_that_cannot_be_read_refuse_dispatch(dispatch, root):
    dispatch["levels"]({"root": "relative/folder", "level": "trusted"})
    rc, _, err = dispatch["run"]("spawn", "alpha", "--task", "t", "--prompt", "synthetic")
    assert rc == 2 and "malformed" in err and dispatch["bg"]() == []


def test_dispatch_points_at_trust_sync_for_a_repository_that_qualifies(dispatch, root):
    make_repo(root / "alpha", GH + "example-user/alpha")
    make_repo(root / "beta", GH + "someone-else/beta")
    dispatch["levels"](trusted_root(root))
    claude_file({})
    rc, out, err = dispatch["run"]("spawn", "alpha", "--task", "t", "--prompt", "synthetic", "--dry-run")
    assert rc == 0, err
    assert "NOT accepted" in out and "trust sync" in out and "folder-trust level: trusted" in out
    rc, out, _ = dispatch["run"]("spawn", "beta", "--task", "t", "--prompt", "synthetic", "--dry-run")
    assert "accept the trust prompt" in out and "would not trust it" in out


def test_dispatch_sees_a_nested_repository_as_untrusted(dispatch, root):
    make_repo(root / "alpha", GH + "example-user/alpha")
    inner = make_repo(root / "alpha" / "inner", GH + "example-user/inner")
    dispatch["levels"](trusted_root(root))
    claude_file({F(root / "alpha"): {"hasTrustDialogAccepted": True}})
    rc, out, err = dispatch["run"]("spawn", str(inner), "--task", "t", "--prompt", "synthetic", "--dry-run")
    assert rc == 0, err
    assert "NOT accepted" in out
    rc, out, _ = dispatch["run"]("spawn", "alpha", "--task", "t", "--prompt", "synthetic", "--dry-run")
    assert "folder trust: accepted" in out


# ---- the shared module ships twice, identically ----------------------------------------------------
def test_core_and_interjector_ship_the_same_trust_module():
    assert (CORE / "hooks" / "gk_trust.py").read_bytes() == (IJ / "hooks" / "gk_trust.py").read_bytes()


def test_the_writer_ships_only_in_core():
    assert not (IJ / "hooks" / "trust_write.py").exists()
    assert "trust_write" not in DISPATCH.read_text(encoding="utf-8")
