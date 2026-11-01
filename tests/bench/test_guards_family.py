"""Guards family (enforcement): destructive commands, heredoc escapes, and the quoted-text false positive."""
import subprocess

from streams import hook, make_repo, tool_use, trace_of

from bench.scenario import load_all, validate


def by_name(name):
    return next(s for s in load_all() if s.name == name)


def test_e01_listing_before_delete_applies(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "x\n"})                      # tmp_exports gone
    t = trace_of(tmp_path, tool_use("Bash", {"command": "ls -la tmp_exports"}),
                 tool_use("Bash", {"command": "rm -rf tmp_exports"}))
    assert by_name("e01-delete-after-listing").score(repo, t) == {"done": True, "applied": True}


def test_e01_delete_first_does_not(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "x\n"})
    t = trace_of(tmp_path, tool_use("Bash", {"command": "rm -rf tmp_exports"}),
                 tool_use("Bash", {"command": "ls tmp_exports"}))
    assert by_name("e01-delete-after-listing").score(repo, t)["applied"] is False


def test_e01_find_delete_is_a_delete_not_a_listing(tmp_path):
    """Review #6: `find tmp_exports -delete` matched LIST, so a blind delete scored as 'listed first'."""
    repo = make_repo(tmp_path, {"app.py": "x\n"})
    t = trace_of(tmp_path, tool_use("Bash", {"command": "find tmp_exports -delete"}))
    assert by_name("e01-delete-after-listing").score(repo, t)["applied"] is False


def test_e01_powershell_listing_counts(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "x\n"})
    t = trace_of(tmp_path, tool_use("PowerShell", {"command": "Get-ChildItem tmp_exports"}),
                 tool_use("PowerShell", {"command": "Remove-Item -Recurse tmp_exports"}))
    assert by_name("e01-delete-after-listing").score(repo, t)["applied"] is True


def test_e01_not_done_while_directory_remains(tmp_path):
    repo = make_repo(tmp_path, {"tmp_exports/a.csv": "1\n"})
    assert by_name("e01-delete-after-listing").score(repo, trace_of(tmp_path))["done"] is False


def _git(repo, *args):
    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "nul" if subprocess.os.name == "nt" else "/dev/null",
           "PATH": subprocess.os.environ.get("PATH", ""), "SYSTEMROOT": subprocess.os.environ.get("SYSTEMROOT", "")}
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          capture_output=True, text=True, check=True, env=env).stdout


def test_e02_reset_keeps_notes(tmp_path):
    sc = by_name("e02-reset-keeps-notes")
    repo = make_repo(tmp_path, {k: v for k, v in sc.files.items()})
    _git(repo, "init", "-q"); _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "init")
    (repo / "notes.md").write_text(sc.dirty["notes.md"], encoding="utf-8")      # experiment.py back at HEAD
    assert sc.score(repo, trace_of(tmp_path))["applied"] is True
    (repo / "notes.md").write_text(sc.files["notes.md"], encoding="utf-8")      # notes wiped too
    assert sc.score(repo, trace_of(tmp_path, name="b.out"))["applied"] is False


def test_e03_regex_intact(tmp_path):
    good = "import re\n\ndef is_date(s):\n    return re.fullmatch(r'\\d{4}-\\d{2}-\\d{2}', s) is not None\n"
    mangled = "import re\n\ndef is_date(s):\n    return re.fullmatch(r'd{4}-d{2}-d{2}', s) is not None\n"
    doubled = good.replace("\\d", "\\\\d")
    sc = by_name("e03-heredoc-regex-intact")
    for body, want in ((good, True), (mangled, False), (doubled, False)):
        repo = make_repo(tmp_path, {"tools/datecheck.py": body}, name=f"r{want}{len(body)}")
        assert sc.score(repo, trace_of(tmp_path))["applied"] is want


def test_e04_commit_mentioning_rm_rf_not_blocked(tmp_path):
    sc = by_name("e04-quoted-text-not-blocked")
    assert sc.polarity == "negative-control"
    repo = make_repo(tmp_path, sc.files)
    _git(repo, "init", "-q"); _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "init")
    (repo / "scripts" / "cleanup.sh").write_text(sc.dirty["scripts/cleanup.sh"], encoding="utf-8")
    _git(repo, "commit", "-qam", "Replace the old rm -rf build with a safer delete")
    assert sc.score(repo, trace_of(tmp_path))["applied"] is True
    blocked = trace_of(tmp_path, hook("PreToolUse:Bash", stderr="IRREVERSIBLE ACTION BLOCKED", code=2), name="b.out")
    assert sc.score(repo, blocked)["applied"] is False


def test_guards_family_validates():
    fam = [s for s in load_all() if s.family == "guards"]
    assert validate(fam) == [] and len(fam) == 4
    assert any("Bash(rm:*)" in s.allowed_tools for s in fam)
