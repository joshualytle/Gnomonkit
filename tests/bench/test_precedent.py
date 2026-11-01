"""Precedent family: each scorer must tell a run that followed the planted rule from one that didn't."""
import pytest
from streams import hook, make_repo, text, tool_use, trace_of

from bench.scenario import load_all, validate


def by_name(name):
    return next(s for s in load_all() if s.name == name)


def empty_trace(tmp_path):
    return trace_of(tmp_path)


APPLY = {
    "b01-migration-naming": ({"db/migrations/20261021_0930_create_widgets.sql": "create table widgets(id int);"},
                             {"db/migrations/001_init.sql": "create table widgets(id int);"}),
    "b02-http-library": ({"fetch_status.py": "import httpx\n\ndef fetch_status(url):\n    return httpx.get(url, timeout=5).status_code\n"},
                         {"fetch_status.py": "import requests\n\ndef fetch_status(url):\n    return requests.get(url).status_code\n"}),
    "b03-test-command-flag": ({"scripts/run_tests.sh": "#!/bin/sh\npython -m pytest -p no:cacheprovider -q\n"},
                              {"scripts/run_tests.sh": "#!/bin/sh\npytest -q\n"}),
    "b05-archive-manifest-subagent": ({"archiver.py": "def archive_old(paths):\n    open('archive/MANIFEST.tsv','a')\n"},
                                      {"archiver.py": "def archive_old(paths):\n    pass\n"}),
    "b06-edit-effects": ({"config_schema.py": "SCHEMA_VERSION = 5\nretries: int = 3\n", "CHANGES.md": "- v5: added retries\n"},
                         {"config_schema.py": "SCHEMA_VERSION = 4\nretries: int = 3\n", "CHANGES.md": "- v4: added port\n"}),
    "b09-entry-outranks-lesson": ({"config/exporter.toml": "batch_size = 500\n"},
                                  {"config/exporter.json": "{\"batch_size\": 500}\n"}),
}


@pytest.mark.parametrize("name", sorted(APPLY))
def test_scorer_applies_when_rule_followed(tmp_path, name):
    good, _ = APPLY[name]
    assert by_name(name).score(make_repo(tmp_path, good), empty_trace(tmp_path))["applied"] is True


@pytest.mark.parametrize("name", sorted(APPLY))
def test_scorer_rejects_when_rule_broken(tmp_path, name):
    _, bad = APPLY[name]
    r = by_name(name).score(make_repo(tmp_path, bad), empty_trace(tmp_path))
    assert r["applied"] is False and r["done"] is True


def test_b04_beside_module_by_subagent_on_windows_path(tmp_path):
    t = trace_of(tmp_path, tool_use("Write", {"file_path": "C:\\run\\repo\\slugify_test.py"}, sub=True))
    r = by_name("b04-test-layout-subagent").score(make_repo(tmp_path, {"slugify_test.py": "def test_x(): pass"}), t)
    assert r["applied"] is True and r["subagent"] is True


def test_b04_tests_folder_breaks_rule(tmp_path):
    r = by_name("b04-test-layout-subagent").score(make_repo(tmp_path, {"tests/test_slugify.py": "x"}),
                                                  empty_trace(tmp_path))
    assert r["applied"] is False


def test_b07_deviation_must_be_explained(tmp_path):
    repo = make_repo(tmp_path, {"ci/smoke.sh": "echo smoke ok\n"})
    explained = trace_of(tmp_path, text("Used sh rather than a .ps1 script because the runner lacks PowerShell."),
                         name="a.out")
    silent = trace_of(tmp_path, text("Added ci/smoke.sh."), name="b.out")
    assert by_name("b07-deviation-explained").score(repo, explained)["applied"] is False   # sub text only
    explained_main = trace_of(tmp_path, name="c.out")
    explained_main.result = "Wrote sh instead of the usual PowerShell, since the runner has none."
    assert by_name("b07-deviation-explained").score(repo, explained_main)["applied"] is True
    assert by_name("b07-deviation-explained").score(repo, silent)["applied"] is False


def test_b07_restating_the_prompt_is_not_an_explanation(tmp_path):
    """Review #6: 'PowerShell' is in the prompt, so echoing it back used to pass."""
    repo = make_repo(tmp_path, {"ci/smoke.sh": "echo smoke ok\n"})
    t = trace_of(tmp_path, name="e.out")
    t.result = "Added ci/smoke.sh since the Linux CI runner has no PowerShell installed."
    assert by_name("b07-deviation-explained").score(repo, t)["applied"] is False
    t.result = "Added ci/smoke.sh. Note: the project convention is PowerShell (.ps1), but the runner has none."
    assert by_name("b07-deviation-explained").score(repo, t)["applied"] is True


def test_b08_negative_control_needs_quiet(tmp_path):
    repo = make_repo(tmp_path, {"notes/hello.txt": "hello world"})
    quiet = trace_of(tmp_path, name="q.out")
    noisy = trace_of(tmp_path, hook("PreToolUse:Write", ctx="  skill: copy-discipline"), name="n.out")
    sc = by_name("b08-negative-control")
    assert sc.polarity == "negative-control"
    assert sc.score(repo, quiet)["applied"] is True and sc.score(repo, noisy)["applied"] is False


def test_precedent_family_validates_and_skips_gnomonkit_arm():
    fam = [s for s in load_all() if s.family == "precedent"]
    assert validate(fam) == [] and len(fam) == 9
    assert all("gnomonkit" not in s.arms and s.claude_md for s in fam if s.name != "b08-negative-control")
