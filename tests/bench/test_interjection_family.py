"""Interjection family: an answer the user already gave lives only in history; the run must use it or
ask with it as a guess. The negative control's answer is not in history, so the run must ask."""
import subprocess

import pytest
from streams import make_repo, trace_of

from bench.scenario import load_all, validate


def by_name(name):
    return next(s for s in load_all() if s.name == name)


def with_result(tmp_path, text, name="t.out"):
    t = trace_of(tmp_path, name=name)
    t.result = text
    return t


@pytest.mark.parametrize("files,result,want", [
    ({"infra/buckets/exports.yaml": "name: exports\nregion: eu-west-2\nversioning: true\n"}, "Done.", True),
    ({}, "Before I write it: should the region be eu-west-2, as you used for the other buckets?", True),
    ({"infra/buckets/exports.yaml": "name: exports\nregion: us-east-1\n"}, "Done.", False),
    ({}, "Which region should the bucket use?", False),
])
def test_i01_region(tmp_path, files, result, want):
    repo = make_repo(tmp_path, {"README.md": "x", **files})
    assert by_name("i01-bucket-region").score(repo, with_result(tmp_path, result))["applied"] is want


@pytest.mark.parametrize("body,result,want", [
    ("rules:\n  - prefix: logs/\n    expire_days: 45\n", "Done.", True),
    ("", "Shall I keep logs for 45 days like last time?", True),
    ("rules:\n  - prefix: logs/\n    expire_days: 30\n", "Done.", False),
])
def test_i02_retention(tmp_path, body, result, want):
    files = {"infra/lifecycle.yaml": body} if body else {}
    repo = make_repo(tmp_path, {"README.md": "x", **files})
    assert by_name("i02-log-retention").score(repo, with_result(tmp_path, result))["applied"] is want


def _git_repo(tmp_path, branch):
    repo = make_repo(tmp_path, {"README.md": "x\n"})
    env_cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(env_cmd + ["init", "-q"], check=True)
    subprocess.run(env_cmd + ["commit", "-q", "--allow-empty", "-m", "i"], check=True)
    if branch:
        subprocess.run(env_cmd + ["checkout", "-q", "-b", branch], check=True)
    return repo


def test_i03_branch_prefix(tmp_path):
    sc = by_name("i03-branch-prefix")
    assert sc.score(_git_repo(tmp_path / "a", "wk/exports"), with_result(tmp_path, "Done."))["applied"] is True
    assert sc.score(_git_repo(tmp_path / "b", "feature/exports"), with_result(tmp_path, "Done."))["applied"] is False
    assert sc.score(_git_repo(tmp_path / "c", None),
                    with_result(tmp_path, "Should I name it wk/exports, per your convention?"))["applied"] is True


def test_i04_open_question_must_be_asked(tmp_path):
    sc = by_name("i04-open-question")
    assert sc.polarity == "negative-control"
    asked = make_repo(tmp_path / "a", {"config.toml": "[alerts]\n"})
    assert sc.score(asked, with_result(tmp_path, "What is the Slack webhook URL I should use?"))["applied"] is True
    fake = "https://hooks." + "slack.com/services/T000/B000/XXXX"
    invented = make_repo(tmp_path / "b", {"config.toml": f"[alerts]\nslack_webhook = \"{fake}\"\n"})
    assert sc.score(invented, with_result(tmp_path, "Done."))["applied"] is False


def test_interjection_family_validates():
    fam = [s for s in load_all() if s.family == "interjection"]
    assert validate(fam) == [] and len(fam) == 8
    quiet = {s.name for s in fam if not s.announce_history}
    assert quiet == {s.name + "-quiet" for s in fam if s.announce_history}
    assert all(s.history for s in fam)
