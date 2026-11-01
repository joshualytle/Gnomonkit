"""file_correction.py: files corrections as issues, only ever in a private repo, through a fake gh."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "core" / "skills" / "learned-triggers" / "file_correction.py"

FAKE_GH = r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
if args[:2] == ["repo", "view"]:
    print(json.dumps({"isPrivate": os.environ.get("FAKE_PRIVATE") == "1", "nameWithOwner": args[2]}))
elif args[:2] == ["issue", "list"]:
    print(json.dumps([
        {"number": 4, "title": "Ask fewer questions", "comments": [{}], "url": "u4"},
        {"number": 9, "title": "Search history before asking", "comments": [{}, {}, {}], "url": "u9"},
    ]))
elif args[:2] == ["issue", "create"]:
    body = open(args[args.index("--body-file") + 1], encoding="utf-8").read()
    with open(os.environ["FAKE_LOG"] + ".body", "w", encoding="utf-8") as fh:
        fh.write(body)
    print("https://github.com/me/overlay/issues/12")
elif args[:2] == ["issue", "comment"]:
    body = open(args[args.index("--body-file") + 1], encoding="utf-8").read()
    with open(os.environ["FAKE_LOG"] + ".body", "w", encoding="utf-8") as fh:
        fh.write(body)
    print("https://github.com/me/overlay/issues/9#issuecomment-1")
elif args[:2] == ["label", "create"]:
    pass
else:
    sys.exit(3)
'''


@pytest.fixture
def gh(tmp_path):
    fake = tmp_path / "gh.py"
    fake.write_text(FAKE_GH, encoding="utf-8")
    log = tmp_path / "gh.log"

    def run(*args, private=True, repo="me/overlay"):
        # No option, GNOMONKIT_* setting or personal profile from the developer's own setup.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_"))}
        env.update({"GNOMONKIT_GH": json.dumps([sys.executable, str(fake)]), "FAKE_LOG": str(log),
                    "FAKE_PRIVATE": "1" if private else "0", "GNOMONKIT_HOME": str(tmp_path / "gk")})
        if repo:
            env["CLAUDE_PLUGIN_OPTION_CORRECTIONS_REPO"] = repo
        r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)
        calls = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        body = (tmp_path / "gh.log.body").read_text(encoding="utf-8") if (tmp_path / "gh.log.body").exists() else ""
        return r, calls, body
    return run


def test_list_orders_by_how_often_the_correction_repeated(gh):
    r, calls, _ = gh("list")
    assert r.returncode == 0
    lines = [l for l in r.stdout.splitlines() if l.startswith("#")]
    assert lines[0].startswith("#9") and "3" in lines[0] and lines[1].startswith("#4")
    lst = next(c for c in calls if c[:2] == ["issue", "list"])
    assert "--label" in lst and "learned-trigger" in lst and "--state" in lst and "open" in lst


def test_refuses_a_public_repo_before_writing_anything(gh, tmp_path):
    body = tmp_path / "b.md"
    body.write_text("the user corrected me", encoding="utf-8")
    r, calls, _ = gh("new", "--title", "t", "--body-file", str(body), private=False)
    assert r.returncode != 0 and "private" in r.stderr.lower()
    assert all(c[:2] == ["repo", "view"] for c in calls)


def test_refuses_without_a_repo(gh):
    r, calls, _ = gh("list", repo="")
    assert r.returncode != 0 and "corrections_repo" in r.stderr and calls == []


def test_the_personal_profile_names_the_repo_when_no_option_does(gh, tmp_path):
    (tmp_path / "gk").mkdir()
    (tmp_path / "gk" / "personal.toml").write_text('[corrections]\nrepo = "example-org/agent-notes"\n',
                                                   encoding="utf-8")
    r, calls, _ = gh("list", repo="")
    assert r.returncode == 0
    assert any(c[:3] == ["repo", "view", "example-org/agent-notes"] for c in calls)
    r, calls, _ = gh("list", repo="me/overlay")   # the option wins
    assert any(c[:3] == ["repo", "view", "me/overlay"] for c in calls[-3:])


def test_new_issue_is_labelled_and_secrets_are_redacted(gh, tmp_path):
    body = tmp_path / "b.md"
    token = "ghp_" + "Z9y8X7w6" * 5
    body.write_text(f"Proposed trigger: search history first.\nSeen with token={token}", encoding="utf-8")
    r, calls, sent = gh("new", "--title", "Search history before asking", "--body-file", str(body))
    assert r.returncode == 0 and "issues/12" in r.stdout
    create = next(c for c in calls if c[:2] == ["issue", "create"])
    assert create[create.index("--label") + 1] == "learned-trigger"
    assert token not in sent and "search history first" in sent
    assert any(c[:2] == ["label", "create"] for c in calls)


def test_repeat_comments_on_the_existing_issue(gh, tmp_path):
    body = tmp_path / "b.md"
    body.write_text("Happened again: asked before searching.", encoding="utf-8")
    r, calls, sent = gh("repeat", "9", "--body-file", str(body))
    assert r.returncode == 0 and "issuecomment" in r.stdout
    comment = next(c for c in calls if c[:2] == ["issue", "comment"])
    assert comment[2] == "9" and "Happened again" in sent


def test_repeat_needs_a_real_issue_number(gh, tmp_path):
    body = tmp_path / "b.md"
    body.write_text("x", encoding="utf-8")
    r, calls, _ = gh("repeat", "9; rm -rf /", "--body-file", str(body))
    assert r.returncode != 0 and not any(c[:2] == ["issue", "comment"] for c in calls)
