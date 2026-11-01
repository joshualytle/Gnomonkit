"""The sync gate (`gnomonkit sync`): adopt, the quarantine fetch, the decision, held updates, approvals.

Synthetic data only: repositories, keys and people are made under pytest's tmp_path at test time. The
developer's git config is switched off (GIT_CONFIG_GLOBAL points at a file made here, GIT_CONFIG_NOSYSTEM=1)
so nothing from it can leak in or change a result. Skipped where git or ssh-keygen is missing.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "plugins", "core")
sys.path.insert(0, CORE)

import gnomonkit  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import _syncgate as G  # noqa: E402
from gnomonkit_cmds import sync  # noqa: E402

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("ssh-keygen")),
                                reason="needs git and ssh-keygen")

MKT = "examplemkt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def mail(user):
    return "@".join([user, "example." + "org"])


MAINTAINER = ("Maintainer", mail("maintainer"))
NEWCOMER = ("Newcomer", mail("newcomer"))


def sh(*args, cwd=None, env=None):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(list(args), cwd=cwd, env=e, capture_output=True, text=True, creationflags=NO_WINDOW)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


@pytest.fixture(scope="module")
def keyring(tmp_path_factory):
    """Key pairs made once per module (ssh-keygen is slow on some machines): name -> (private path, public)."""
    folder, made = tmp_path_factory.mktemp("keys"), {}

    def get(name):
        if name not in made:
            made[name] = (str(folder / name), keygen(folder / name))
        return made[name]
    return get


@pytest.fixture
def world(tmp_path, monkeypatch, keyring):
    for k in list(os.environ):
        if k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_", "GIT_")) or k == "CLAUDE_CONFIG_DIR":
            monkeypatch.delenv(k, raising=False)
    gk, claude = tmp_path / "gk", tmp_path / "claude"
    monkeypatch.setenv("GNOMONKIT_HOME", str(gk))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = %s\n\temail = %s\n[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n"
                         "[gpg]\n\tformat = ssh\n" % MAINTAINER, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    C._CACHE.clear()
    return World(tmp_path, gk, claude, gitconfig, keyring)


def keygen(path):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(path)], check=True,
                   capture_output=True, creationflags=NO_WINDOW)
    return open(str(path) + ".pub", encoding="utf-8").read().strip()


class World:
    def __init__(self, tmp, gk, claude, gitconfig, keyring):
        self.tmp, self.gk, self.claude, self.gitconfig = tmp, gk, claude, gitconfig
        self.key = keyring
        self.up = tmp / "upstream"
        self.overlay = tmp / "overlay.git"
        self.work = tmp / "overlay-work"

    # upstream commits
    def commit(self, files, message="change", key="good", who=MAINTAINER, committer=None):
        self.up.mkdir(exist_ok=True)
        if not (self.up / ".git").exists():
            sh("git", "init", "-q", str(self.up))
        for rel, text in files.items():
            p = self.up / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        sh("git", "add", "-A", cwd=self.up)
        args = ["git", "-c", "user.name=%s" % who[0], "-c", "user.email=%s" % who[1]]
        if key:
            args += ["-c", "user.signingkey=%s" % self.key(key)[0], "commit", "-q", "-S", "-m", message]
        else:
            args += ["-c", "commit.gpgsign=false", "commit", "-q", "-m", message]
        c = committer or who
        sh(*args, cwd=self.up, env={"GIT_COMMITTER_NAME": c[0], "GIT_COMMITTER_EMAIL": c[1]})
        return sh("git", "rev-parse", "HEAD", cwd=self.up)

    def first_release(self, name=MKT):
        sha = self.commit({".claude-plugin/marketplace.json": json.dumps({"name": name, "plugins": [
            {"name": "tool", "source": "./plugins/tool"}]}),
            "plugins/tool/.claude-plugin/plugin.json": json.dumps({"name": "tool", "version": "1.0.0"}),
            "plugins/tool/hooks/run.py": "print('v1')\n", "docs/README.md": "# docs\n"}, "first release")
        self.record(name, sha)
        return sha

    def record(self, name, sha):
        plugins = self.claude / "plugins"
        plugins.mkdir(parents=True, exist_ok=True)
        (plugins / "known_marketplaces.json").write_text(json.dumps({name: {
            "source": {"source": "git", "url": str(self.up)}, "installLocation": str(plugins / "marketplaces" / name)}}),
            encoding="utf-8")
        (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"tool@" + name: [
            {"scope": "user", "installPath": "x", "version": "1.0.0", "gitCommitSha": sha}]}}), encoding="utf-8")

    # the overlay
    def make_overlay(self, signers=("good",), github=None, branch_name="main"):
        sh("git", "init", "-q", "--bare", "-b", branch_name, str(self.overlay))
        sh("git", "clone", "-q", str(self.overlay), str(self.work))
        sh("git", "checkout", "-q", "-b", branch_name, cwd=self.work)
        lines = "".join("%s %s\n" % (MAINTAINER[1], self.key(k)[1]) for k in signers)
        self.overlay_commit({"sync/signers/%s.allowed_signers" % MKT: "# test signers\n" + lines,
                             "README.md": "overlay\n"}, push=True)
        cfg = {"overlay": {"remote": str(self.overlay), "github": github or "", "branch": ""}}
        C.atomic_write(G.config_path(), C.dumps(cfg))

    def overlay_commit(self, files, push=True, branch="main"):
        for rel, text in files.items():
            p = self.work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        sh("git", "add", "-A", cwd=self.work)
        sh("git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "overlay change", cwd=self.work)
        if push:
            sh("git", "push", "-q", "origin", "HEAD:refs/heads/" + branch, cwd=self.work)


def cli(capsys, *argv):
    rc = gnomonkit.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def approved_head():
    return sh("git", "-C", G.approved_dir(MKT), "rev-parse", "HEAD")


def last():
    return G.load_sources()["sources"][MKT]["last"]


def adopt(world, capsys, **kw):
    rc, out, err = cli(capsys, "sync", "adopt", MKT)
    assert rc == 0, err
    return out


# ---- adopt -------------------------------------------------------------------------------------------
def test_adopt_pins_the_installed_commit_and_prints_steps_it_does_not_run(world, capsys, monkeypatch):
    pin = world.first_release()
    world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"})          # upstream is ahead of the install
    world.make_overlay()
    ran = []
    real_popen = subprocess.Popen

    def watch(cmd, *a, **k):
        ran.append(cmd if isinstance(cmd, str) else " ".join(map(str, cmd)))
        return real_popen(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", watch)
    out = adopt(world, capsys)
    assert approved_head() == pin
    assert "claude plugin marketplace remove %s" % MKT in out
    assert 'claude plugin marketplace add "%s"' % G.approved_dir(MKT) in out
    assert "claude plugin install tool@%s -s user" % MKT in out
    assert "uninstalls its plugins" in out
    assert not [c for c in ran if c.startswith("claude")]                 # printed, never run
    rc, out2, _ = cli(capsys, "sync", "adopt", MKT)                       # again: the same steps, no change
    assert rc == 0 and "already behind the gate" in out2 and approved_head() == pin


def test_adopt_refuses_a_repository_that_declares_another_marketplace(world, capsys):
    world.first_release(name="othermkt")
    world.record(MKT, sh("git", "rev-parse", "HEAD", cwd=world.up))
    rc, _, err = cli(capsys, "sync", "adopt", MKT)
    assert rc == 2 and "declares the marketplace" in err
    assert MKT not in G.load_sources()["sources"]


def test_adopt_options_that_choose_the_source_need_a_terminal(world, capsys):
    world.first_release()
    rc, _, err = cli(capsys, "sync", "adopt", MKT, "--url", str(world.up))
    assert rc == 2 and "terminal" in err
    assert not os.path.exists(G.sources_path())


# ---- the decision ------------------------------------------------------------------------------------
def test_signed_commits_by_an_approved_key_auto_advance(world, capsys):
    world.first_release()
    world.make_overlay()
    adopt(world, capsys)
    head = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"})
    rc, out, _ = cli(capsys, "sync", "check")
    assert rc == 0 and "moved on its own" in out
    assert approved_head() == head and last()["state"] == "advanced"
    assert not G.list_held()


@pytest.mark.parametrize("case", ["unsigned", "unknown-key", "new-contributor"])
def test_what_holds_an_update(world, capsys, case):
    pin = world.first_release()
    world.make_overlay()
    adopt(world, capsys)
    files = {"plugins/tool/hooks/run.py": "print('changed')\n"}
    if case == "unsigned":
        head = world.commit(files, key=None)
    elif case == "unknown-key":
        head = world.commit(files, key="stranger")
    else:
        head = world.commit(files, who=NEWCOMER, committer=MAINTAINER)
    rc, out, _ = cli(capsys, "sync", "check")
    assert rc == 0 and "update held for review" in out
    assert approved_head() == pin
    hid = G.held_id(MKT, head)
    h = G.read_held(hid)
    assert h and h["pin"] == pin and h["head"] == head
    assert [r["kind"] for r in h["reasons"]] == [case]
    assert {f["path"] for f in h["files"]} == {"plugins/tool/hooks/run.py"}
    if case == "new-contributor":
        assert h["reasons"][0]["paths"] == ["plugins/tool/hooks/run.py"] and NEWCOMER[1] in h["reasons"][0]["who"]
    rc, out, _ = cli(capsys, "sync", "explain", hid)
    assert rc == 0 and "plugins/tool/hooks/run.py" in out and "approve-draft %s" % hid in out
    assert "print(" not in out                                           # file names, never file contents


def test_a_new_contributor_touching_only_docs_does_not_hold(world, capsys):
    world.first_release()
    world.make_overlay()
    adopt(world, capsys)
    head = world.commit({"docs/guide.md": "more words\n", "README.md": "readme\n"}, who=NEWCOMER, committer=NEWCOMER)
    cli(capsys, "sync", "check")
    assert last()["state"] == "advanced" and approved_head() == head


def test_markdown_that_steers_an_agent_counts_as_code(world):
    assert G.is_code("plugins/tool/skills/x/SKILL.md") and G.is_code("CLAUDE.md") and G.is_code("sub/AGENTS.md")
    assert not G.is_code("docs/x.py") and not G.is_code("README.md") and not G.is_code("assets/logo.svg")
    assert G.is_code("plugins/tool/hooks/run.py")


def test_a_rewritten_branch_holds(world, capsys):
    world.first_release()
    world.make_overlay()
    adopt(world, capsys)
    sh("git", "-c", "user.signingkey=%s" % world.key("good")[0], "commit", "-q", "--amend", "-S", "-m", "rewritten",
       cwd=world.up)
    cli(capsys, "sync", "check")
    assert last()["state"] == "held"
    assert G.read_held(last()["held"])["reasons"][0]["kind"] == "rewritten"


def test_if_due_skips_a_recent_check(world, capsys):
    world.first_release()
    world.make_overlay()
    adopt(world, capsys)
    cli(capsys, "sync", "check")
    head = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"})
    cli(capsys, "sync", "check", "--if-due", "--quiet")
    assert approved_head() != head
    cli(capsys, "sync", "check", "--quiet")
    assert approved_head() == head


# ---- nothing runs from a fetched tree ----------------------------------------------------------------
HOOKS = ("post-checkout", "post-merge", "reference-transaction", "pre-commit", "post-index-change", "fsmonitor-watchman")


def plant(folder, marker):
    folder.mkdir(parents=True, exist_ok=True)
    for name in HOOKS:
        p = folder / name
        p.write_text("#!/bin/sh\necho %s >> '%s'\n" % (name, marker.as_posix()), encoding="utf-8", newline="\n")
        p.chmod(0o755)


def test_planted_hooks_never_run(world, capsys):
    marker = world.tmp / "hook-ran.txt"
    # control: these hooks do fire for plain git on this machine, so their silence below means something
    ctl = world.tmp / "control"
    sh("git", "init", "-q", str(ctl))
    plant(ctl / ".git" / "hooks", marker)
    (ctl / "f").write_text("x", encoding="utf-8")
    sh("git", "add", "f", cwd=ctl)
    sh("git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "c", cwd=ctl)
    if not marker.exists():
        pytest.skip("git hooks don't run on this machine, so their absence would prove nothing")
    marker.unlink()

    world.first_release()
    world.make_overlay()
    plant(world.up / ".git" / "hooks", marker)                          # in the source
    global_hooks = world.tmp / "global-hooks"                            # and in the user's own git config
    plant(global_hooks, marker)
    with open(world.gitconfig, "a", encoding="utf-8") as fh:
        fh.write("[core]\n\thooksPath = %s\n" % global_hooks.as_posix())
    adopt(world, capsys)
    plant(world.gk / "sync" / "quarantine" / (MKT + ".git") / "hooks", marker)
    plant(world.gk / "sync" / "approved" / MKT / ".git" / "hooks", marker)
    with open(world.gk / "sync" / "quarantine" / (MKT + ".git") / "config", "a", encoding="utf-8") as fh:
        fh.write("[core]\n\thooksPath = %s\n" % global_hooks.as_posix())
    head = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"}, message="signed")
    if marker.exists():
        marker.unlink()                                                  # the test's own commit ran them
    rc, _, _ = cli(capsys, "sync", "check")
    assert rc == 0 and approved_head() == head                           # fetched, advanced, checked out
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_a_remote_that_would_start_a_program_is_refused(world):
    for bad in ("ext::sh -c touch% /tmp/x", "-uhelper", "fd::3", "gopher://host/repo"):
        with pytest.raises(C.CommandError):
            G.check_remote(bad)
    assert G.check_remote("https://example.org/a/b.git").startswith("https://")


# ---- approvals come from the overlay's default branch as fetched ---------------------------------------
def held_update(world, capsys):
    world.first_release()
    world.make_overlay(github="example-org/overlay")
    adopt(world, capsys)
    head = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"}, key=None)
    cli(capsys, "sync", "check")
    assert last()["state"] == "held"
    return G.read_held(G.held_id(MKT, head))


def approval_toml(h):
    return G.approval_text(None, h)


def test_an_approval_only_in_a_working_tree_or_a_branch_approves_nothing(world, capsys):
    h = held_update(world, capsys)
    (world.work / "sync").mkdir(exist_ok=True)
    (world.work / "sync" / "approvals.toml").write_text(approval_toml(h), encoding="utf-8")   # on disk only
    cli(capsys, "sync", "check")
    assert last()["state"] == "held"
    world.overlay_commit({}, push=False)                                  # committed, not pushed
    cli(capsys, "sync", "check")
    assert last()["state"] == "held"
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/gnomonkit-sync/x", cwd=world.work)   # a branch, not merged
    cli(capsys, "sync", "check")
    assert last()["state"] == "held"
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/main", cwd=world.work)               # merged
    cli(capsys, "sync", "check")
    assert last()["state"] == "advanced" and approved_head() == h["head"]
    assert not G.list_held()


def test_approve_draft_pushes_a_sync_branch_and_opens_a_pr(world, capsys, monkeypatch):
    h = held_update(world, capsys)
    opened = []
    monkeypatch.setattr(sync, "open_pr", lambda *a: opened.append(a) or "https://example.org/pr/1")
    rc, out, err = cli(capsys, "sync", "approve-draft", h["id"], "--json")
    assert rc == 0, err
    res = json.loads(out)
    branch = "gnomonkit-sync/" + h["id"]
    assert res["branch"] == branch and res["pr"] == "https://example.org/pr/1" and not res["already"]
    (slug, base, head, title, body), = opened
    assert (slug, base, head) == ("example-org/overlay", "main", branch)
    assert h["head"][:12] in body and "unsigned" in body
    text = sh("git", "--git-dir", str(world.overlay), "show", "%s:sync/approvals.toml" % branch)
    assert 'head = "%s"' % h["head"] in text and 'from = "%s"' % h["pin"] in text
    assert sh("git", "--git-dir", str(world.overlay), "rev-parse", "main") != sh(
        "git", "--git-dir", str(world.overlay), "rev-parse", branch)        # drafted, not merged
    cli(capsys, "sync", "check")
    assert last()["state"] == "held"
    rc, out, _ = cli(capsys, "sync", "approve-draft", h["id"])             # twice: nothing new pushed
    assert rc == 0 and "already exists" in out and len(opened) == 1
    sh("git", "--git-dir", str(world.overlay), "update-ref", "refs/heads/main", branch)    # the person merges
    cli(capsys, "sync", "check")
    assert last()["state"] == "advanced" and approved_head() == h["head"]


def test_approve_draft_signers_trusts_the_key_that_signed(world, capsys, monkeypatch):
    world.first_release()
    world.make_overlay()
    adopt(world, capsys)
    head = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"}, key="newkey")
    cli(capsys, "sync", "check")
    hid = G.held_id(MKT, head)
    monkeypatch.setattr(sync, "open_pr", lambda *a: pytest.fail("no GitHub slug, so no pull request"))
    rc, out, err = cli(capsys, "sync", "approve-draft", hid, "--signers", "--json")
    assert rc == 0, err
    res = json.loads(out)
    assert res["pr"] is None and res["branch"] == "gnomonkit-sync/%s-signers" % hid
    text = sh("git", "--git-dir", str(world.overlay), "show", "%s:sync/signers/%s.allowed_signers" % (res["branch"], MKT))
    assert world.key("newkey")[1].split()[1] in text and world.key("good")[1].split()[1] in text
    sh("git", "--git-dir", str(world.overlay), "update-ref", "refs/heads/main", res["branch"])
    cli(capsys, "sync", "check")
    assert last()["state"] == "advanced" and approved_head() == head


def test_approve_draft_without_an_overlay_refuses(world, capsys):
    rc, _, err = cli(capsys, "sync", "approve-draft", MKT + "-0123456789ab")
    assert rc == 2 and "no overlay" in err


def test_overlay_set_needs_a_terminal(world, capsys):
    sh("git", "init", "-q", "--bare", str(world.overlay))
    rc, _, err = cli(capsys, "sync", "overlay", "set", str(world.overlay))
    assert rc == 2 and "terminal" in err and not os.path.exists(G.config_path())


def test_a_local_overlay_must_be_a_bare_repository(world, capsys):
    world.make_overlay()
    for bad in (world.work, world.tmp / "no-such-folder"):
        rc, _, err = cli(capsys, "sync", "overlay", "set", str(bad))
        assert rc == 2 and "not a bare repository" in err                # refused before the terminal question
    assert G.check_overlay_remote(str(world.overlay)) == os.path.abspath(str(world.overlay))
    assert G.check_overlay_remote("https://example.org/a/overlay.git").startswith("https://")


# ---- no overlay: the suggested key, nothing held -------------------------------------------------------
def test_without_an_overlay_only_the_suggested_key_is_checked(world, capsys):
    name = G.SUGGESTED_FOR
    world.first_release(name=name)
    signers = world.tmp / "suggested.allowed_signers"
    signers.write_text("# suggested\n%s %s\n" % (MAINTAINER[1], world.key("good")[1]), encoding="utf-8")
    C.atomic_write(G.config_path(), C.dumps({"suggested": {"signers": str(signers)}}))
    rc, out, err = cli(capsys, "sync", "adopt", name)
    assert rc == 0, err
    assert "No overlay is set" in out
    good = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"}, who=NEWCOMER, committer=NEWCOMER)
    cli(capsys, "sync", "check")
    s = G.load_sources()["sources"][name]["last"]
    assert s["state"] == "advanced" and s["pin"] == good                  # signature only, no contributor rule
    world.commit({"plugins/tool/hooks/run.py": "print('v3')\n"}, key=None)
    rc, out, _ = cli(capsys, "sync", "check")
    s = G.load_sources()["sources"][name]["last"]
    assert s["state"] == "unverified" and s["pin"] == good and "not offered" in out
    assert not G.list_held()


def test_the_shipped_suggested_key_file_holds_no_key():
    path = os.path.join(CORE, "sync", "suggested.allowed_signers")
    with open(path, encoding="utf-8") as fh:
        assert G.signer_lines(fh.read()) == []


def test_status_lists_held_updates_and_the_explain_command(world, capsys):
    h = held_update(world, capsys)
    rc, out, _ = cli(capsys, "sync", "status")
    assert rc == 0 and h["id"] in out and "sync explain %s" % h["id"] in out
    assert "does not read %s from the approved checkout" % MKT in out


# ---- every commit in the range is checked, and only the allowed signers decide ---------------------------
def gated(world, capsys, github=None):
    pin = world.first_release()
    world.make_overlay(github=github)
    adopt(world, capsys)
    return pin


def held_kinds(head):
    return [r["kind"] for r in G.read_held(G.held_id(MKT, head))["reasons"]]


def test_an_author_name_with_a_field_separator_is_still_checked(world, capsys):
    pin = gated(world, capsys)
    odd = ("Odd" + chr(0x1f) + "Person", mail("odd"))
    head = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"}, key=None, who=odd, committer=MAINTAINER)
    cli(capsys, "sync", "check")
    assert last()["state"] == "held" and approved_head() == pin
    assert set(held_kinds(head)) == {"unsigned", "new-contributor"}


def test_a_log_that_does_not_match_the_commit_list_holds_the_whole_range(world, capsys, monkeypatch):
    pin = gated(world, capsys)
    world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"})
    head = world.commit({"plugins/tool/hooks/run.py": "print('v3')\n"})       # both signed: would move
    real = G.git

    def one_record_short(args, *a, **k):
        r = real(args, *a, **k)
        if "log" in args and "--format=" + G._FMT in args:
            r.stdout = r.stdout.split("\n", len(G._FIELDS))[-1]
        return r
    monkeypatch.setattr(G, "git", one_record_short)
    cli(capsys, "sync", "check")
    assert last()["state"] == "held" and approved_head() == pin
    assert held_kinds(head) == ["unverifiable"]


def fake_ssh_program(world):
    """A stand-in for ssh-keygen that calls every signature good, as a hostile git setting could name."""
    fake = world.tmp / "fake-keygen"
    fake.write_text('#!/bin/sh\ncase "$*" in\n  *find-principals*) echo %s; exit 0;;\n'
                    '  *verify*) echo \'Good "git" signature for %s with ED25519 key SHA256:AAAA\'; exit 0;;\n'
                    '  *) exec ssh-keygen "$@";;\nesac\n' % (MAINTAINER[1], MAINTAINER[1]),
                    encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    return fake.as_posix()


@pytest.mark.parametrize("how", ["global-config", "environment"])
def test_a_signature_program_from_the_users_settings_decides_nothing(world, capsys, monkeypatch, how):
    pin = gated(world, capsys)
    head = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"}, key="stranger")
    fake = fake_ssh_program(world)
    empty = world.tmp / "empty.allowed_signers"
    empty.write_text("", encoding="utf-8")
    control = subprocess.run(["git", "-c", "gpg.ssh.program=" + fake, "-c", "gpg.ssh.allowedSignersFile=" +
                              empty.as_posix(), "verify-commit", head], cwd=world.up, capture_output=True,
                             creationflags=NO_WINDOW)
    if control.returncode != 0:
        pytest.skip("this git can't run the stand-in program, so its silence below would prove nothing")
    if how == "global-config":
        with open(world.gitconfig, "a", encoding="utf-8") as fh:
            fh.write('[gpg "ssh"]\n\tprogram = %s\n' % fake)
    else:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "gpg.ssh.program")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", fake)
    cli(capsys, "sync", "check")
    assert last()["state"] == "held" and approved_head() == pin
    assert held_kinds(head) == ["unknown-key"]


def test_an_openpgp_signature_counts_as_unsigned(world, capsys, monkeypatch):
    gpg = shutil.which("gpg")
    if not gpg:
        pytest.skip("needs gpg")
    pin = gated(world, capsys)
    home = tempfile.mkdtemp(prefix="gkg-")              # short: gpg-agent's socket path has a length limit
    gh = home
    if os.name == "nt" and gh[1:2] == ":" and "/usr/bin/" in gpg.replace("\\", "/").lower():
        gh = "/" + gh[0].lower() + gh[2:].replace("\\", "/")              # Git for Windows' gpg reads POSIX paths
    monkeypatch.setenv("GNUPGHOME", gh)

    def run(*args):
        return subprocess.run(list(args), capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
    try:
        made = run("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "", "--quick-gen-key",
                   "%s <%s>" % MAINTAINER, "ed25519", "sign", "never")
        if made.returncode != 0:
            pytest.skip("gpg could not make a key here: %s" % made.stderr[-300:])
        fpr = [ln.split(":")[9] for ln in run("gpg", "--batch", "--with-colons", "--list-secret-keys").stdout.splitlines()
               if ln.startswith("fpr:")][0]
        (world.up / "plugins" / "tool" / "hooks" / "run.py").write_text("print('v2')\n", encoding="utf-8")
        sh("git", "add", "-A", cwd=world.up)
        sh("git", "-c", "gpg.format=openpgp", "-c", "user.signingkey=" + fpr, "commit", "-q", "-S", "-m", "openpgp",
           cwd=world.up)
        head = sh("git", "rev-parse", "HEAD", cwd=world.up)
        if subprocess.run(["git", "verify-commit", head], cwd=world.up, capture_output=True,
                          creationflags=NO_WINDOW).returncode != 0:
            pytest.skip("the keyring here does not trust its own key, so the check below would prove nothing")
        cli(capsys, "sync", "check")
        assert last()["state"] == "held" and approved_head() == pin
        reasons = G.read_held(G.held_id(MKT, head))["reasons"]
        assert [r["kind"] for r in reasons] == ["unsigned"] and "SSH" in reasons[0]["text"]
    finally:
        run("gpgconf", "--kill", "gpg-agent")
        shutil.rmtree(home, ignore_errors=True)


# ---- the overlay: fetched by branch, never read from a working copy, and nothing moves without it ---------
def set_overlay(remote, branch="", github=""):
    C.atomic_write(G.config_path(), C.dumps({"overlay": {"remote": str(remote), "github": github, "branch": branch}}))


@pytest.mark.parametrize("how", ["working-clone", "missing-branch"])
def test_an_overlay_that_cannot_be_fetched_holds_even_signed_commits(world, capsys, how):
    pin = gated(world, capsys)
    head = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"})          # signed: would move on its own
    if how == "working-clone":
        set_overlay(world.work)
    else:
        set_overlay(world.overlay, branch="no-such-branch")
    rc, out, _ = cli(capsys, "sync", "check")
    assert last()["state"] == "held" and approved_head() == pin and "could not be fetched" in out
    assert held_kinds(head) == ["overlay-unavailable"]
    set_overlay(world.overlay)
    cli(capsys, "sync", "check")
    assert last()["state"] == "advanced" and approved_head() == head and not G.list_held()


def test_a_held_update_keeps_its_reasons_while_the_overlay_is_down(world, capsys):
    h = held_update(world, capsys)
    set_overlay(world.overlay, branch="no-such-branch", github="example-org/overlay")
    cli(capsys, "sync", "check")
    assert [r["kind"] for r in G.read_held(h["id"])["reasons"]] == ["overlay-unavailable", "unsigned"]


def test_an_unpushed_approval_in_a_local_clone_approves_nothing(world, capsys):
    h = held_update(world, capsys)
    sh("git", "checkout", "-q", "-b", "scratch", cwd=world.work)
    world.overlay_commit({"sync/approvals.toml": approval_toml(h)}, push=False)  # committed on a side branch only
    set_overlay(world.work)                                                      # the setting names the clone
    cli(capsys, "sync", "check")
    assert last()["state"] == "held" and approved_head() == h["pin"]


# ---- approvals part of the way, and from before the pin -------------------------------------------------
def approve(world, frm, head):
    path = world.work / "sync" / "approvals.toml"
    prev = path.read_text(encoding="utf-8") if path.exists() else None
    world.overlay_commit({"sync/approvals.toml": G.approval_text(prev, {"source": MKT, "pin": frm, "head": head,
                                                                         "reasons": []})})


def test_an_approval_of_part_of_the_range_moves_the_pin_that_far(world, capsys):
    pin = gated(world, capsys)
    c1 = world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"}, key=None)
    c2 = world.commit({"plugins/tool/hooks/run.py": "print('v3')\n"}, key=None)
    cli(capsys, "sync", "check")
    assert last()["state"] == "held" and approved_head() == pin
    approve(world, pin, c1)                                                       # the first commit only
    cli(capsys, "sync", "check")
    assert last()["state"] == "held" and last()["pin"] == c1 and approved_head() == c1
    h = G.read_held(G.held_id(MKT, c2))
    assert h["pin"] == c1 and [r.get("commit") for r in h["reasons"]] == [c2]
    approve(world, pin, c2)                                                       # its range starts before the pin
    rc, out, _ = cli(capsys, "sync", "check")
    assert last()["state"] == "advanced" and approved_head() == c2 and not G.list_held()
    assert "moved by your overlay's approval" in out


# ---- the approved checkout, the held records, adopt -----------------------------------------------------
def test_a_changed_approved_checkout_is_put_back(world, capsys):
    pin = gated(world, capsys)
    a = G.approved_dir(MKT)
    hooks = os.path.join(a, "plugins", "tool", "hooks")
    with open(os.path.join(hooks, "run.py"), "w", encoding="utf-8") as fh:
        fh.write("print('changed on disk')\n")
    planted = [os.path.join(hooks, "extra.py"), os.path.join(hooks, "evil.pyc"),
               os.path.join(hooks, "__pycache__", "run.cpython-39.pyc")]
    os.makedirs(os.path.dirname(planted[2]))
    for p in planted:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("planted\n")
    os.makedirs(os.path.join(a, ".git", "info"), exist_ok=True)
    with open(os.path.join(a, ".git", "info", "exclude"), "a", encoding="utf-8") as fh:
        fh.write("*.pyc\n")                                                     # ignored files go too
    rc, out, _ = cli(capsys, "sync", "check")
    assert rc == 0 and approved_head() == pin and last()["state"] == "current" and "put back" in out
    assert set(last()["restored"]) == {"plugins/tool/hooks/run.py", "plugins/tool/hooks/extra.py",
                                       "plugins/tool/hooks/evil.pyc"}        # Python's own cache goes quietly
    with open(os.path.join(hooks, "run.py"), encoding="utf-8") as fh:
        assert fh.read() == "print('v1')\n"
    assert not any(os.path.exists(p) for p in planted)


def test_a_pin_the_quarantine_could_not_keep_is_reported(world, capsys, monkeypatch):
    gated(world, capsys)
    world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"})
    monkeypatch.setattr(G, "keep_pin", lambda *a: False)
    cli(capsys, "sync", "check")
    assert last()["state"] == "advanced" and any("could not keep a ref" in n for n in last().get("notes") or [])


def test_held_records_are_chosen_by_their_file_names(world, capsys):
    h = held_update(world, capsys)
    other = os.path.join(G.held_dir(), "othermkt-0123456789ab.json")
    with open(other, "w", encoding="utf-8") as fh:
        json.dump(h, fh)                                                        # claims to be this source's record
    assert [x["id"] for x in G.list_held()] == [h["id"]]
    G.clear_held(MKT)
    assert os.path.exists(other) and not G.list_held(MKT)


def test_a_summary_names_kinds_and_counts_only(world, capsys):
    gated(world, capsys)
    world.commit({"plugins/tool/hooks/run.py": "print('v2')\n"}, key=None, who=NEWCOMER, committer=NEWCOMER)
    cli(capsys, "sync", "check")
    s = last()["summary"]
    assert s == "1 unsigned commit, 1 new contributor touching code"
    assert last()["kinds"] == {"unsigned": 1, "new-contributor": 1}
    assert NEWCOMER[1] not in s and "run.py" not in s


def test_adopt_refuses_plugins_from_outside_the_repository(world, capsys):
    sha = world.commit({".claude-plugin/marketplace.json": json.dumps({"name": MKT, "plugins": [
        {"name": "tool", "source": "./plugins/tool"},
        {"name": "far", "source": {"source": "github", "repo": "example-org/far"}},
        {"name": "near", "source": "../sibling"}]}),
        "plugins/tool/.claude-plugin/plugin.json": json.dumps({"name": "tool", "version": "1.0.0"})}, "release")
    world.record(MKT, sha)
    rc, _, err = cli(capsys, "sync", "adopt", MKT)
    assert rc == 2 and "outside its repository" in err and "far" in err and "near" in err
    assert "tool," not in err and MKT not in G.load_sources()["sources"]


def test_every_kind_of_reason_is_known_where_it_is_shown():
    import importlib.util
    spec = importlib.util.spec_from_file_location("plugin_freshness_kinds",
                                                  os.path.join(CORE, "hooks", "plugin_freshness.py"))
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    sys.path.insert(0, os.path.join(ROOT, "plugins", "portal"))
    from gnomonkit_portal import updates
    kinds = set(G.KIND_WORDS) - {"other"}
    assert kinds <= set(fresh.KIND_WORDS) and kinds <= set(updates.REASON_KINDS)
