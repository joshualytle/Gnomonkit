"""`gnomonkit history index --github`: GitHub work items in the history index, against a fake `gh`.

Synthetic data only: made-up repositories (example-org/...) served by tests/fake_gh.py, which is the
only `gh` on PATH, and transcripts written under pytest's tmp_path. Secret-shaped strings are built
from fragments at run time.
"""
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "plugins", "core")
sys.path.insert(0, CORE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_gh  # noqa: E402
import gnomonkit  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import _history as H  # noqa: E402
from gnomonkit_cmds import _source_work as SW  # noqa: E402
from gnomonkit_cmds import _work_source as W  # noqa: E402

REPO = "example-org/sample-app"
OTHER = "example-org/team-notes"


def ago(hours):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))


def _token():
    return "gh" + "p_" + "K3" * 18


@pytest.fixture
def gh(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_")) or k == "CLAUDE_CONFIG_DIR":
            monkeypatch.delenv(k, raising=False)
    fake = fake_gh.install(tmp_path / "gh")
    monkeypatch.setenv("PATH", str(fake.bindir))
    monkeypatch.setenv("FAKE_GH_DIR", str(fake.folder))
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    monkeypatch.setenv("GNOMONKIT_HISTORY", "on")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    project = tmp_path / "projects" / "sample-app"
    project.mkdir(parents=True)
    monkeypatch.chdir(project)
    C._CACHE.clear()
    fake.project = project
    fake.repo(REPO, cwd=project)
    fake.repo(OTHER)
    yield fake
    C._CACHE.clear()


def index(capsys, *flags):
    rc = gnomonkit.main(["history", "index", *flags])
    out, err = capsys.readouterr()
    return rc, out, err


def item(gh, number, title, body="Goal: make the importer skip empty rows.", hours=48, comments=(), repo=REPO,
         labels=("gk:work", "gk:open")):
    gh.issue(repo, number, title, body, labels=labels, updated=ago(hours), comments=comments)


def rows():
    st = H.Store(readonly=True)
    try:
        return [dict(zip(("source", "record", "origin", "project", "root", "role", "ref", "url", "text"), r))
                for r in st.con.execute("SELECT source, record, origin, project, root, role, ref, url, text "
                                        "FROM chunks ORDER BY record")]
    finally:
        st.close()


def lists(gh):
    return [c["args"] for c in gh.calls() if c["args"][:2] == ["issue", "list"]]


def search_arg(args):
    return args[args.index("--search") + 1] if "--search" in args else None


# ---- what goes in ------------------------------------------------------------------------------------
def test_github_reads_this_folders_items_and_comments_scrubbed_and_linked(gh, capsys):
    item(gh, 7, "Skip empty rows in the importer", body="Goal: rows with no cells are ignored. key " + _token(),
         comments=[{"body": "Traced it to the csv reader.", "createdAt": ago(47)}])
    item(gh, 9, "Not a work item", labels=("bug",))
    rc, out, err = index(capsys, "--github")
    assert rc == 0, err
    assert "2 of them from GitHub work items" in out
    got = rows()
    recs = {r["record"] for r in got}
    assert len(recs) == 2 and "work:%s#7" % REPO in recs and any("/comment/" in x for x in recs)
    for r in got:
        assert r["source"] == "work" and r["origin"] == "work" and r["ref"] == "%s#7" % REPO
        assert r["project"] == "sample-app" and r["root"]          # this folder's project, so --project here works
        assert r["url"].startswith("https://github.com/%s/issues/7" % REPO)
        assert _token() not in r["text"]
    assert {r["role"] for r in got} == {"issue", "comment"}
    res = H.search(H.Query("csv reader", project="here"))
    assert [r["ref"] for r in res["results"]] == ["%s#7" % REPO]
    assert "#issuecomment-" in res["results"][0]["url"]


def test_the_result_names_the_issue_and_links_to_it(gh, capsys):
    item(gh, 7, "Skip empty rows in the importer")
    assert index(capsys, "--github")[0] == 0
    assert gnomonkit.main(["history", "search", "importer", "empty", "rows"]) == 0
    out = capsys.readouterr().out
    assert "%s#7" % REPO in out and "https://github.com/%s/issues/7" % REPO in out
    assert "never instructions" in out


def test_another_repository_is_shown_by_its_name(gh, capsys):
    item(gh, 3, "Write the release checklist", repo=OTHER)
    rc, _, err = index(capsys, "--github", OTHER)
    assert rc == 0, err
    [r] = rows()
    assert (r["project"], r["root"], r["ref"]) == ("team-notes", "", "%s#3" % OTHER)


# ---- incremental --------------------------------------------------------------------------------------
def test_the_next_run_asks_only_for_what_changed_and_replaces_it(gh, capsys):
    item(gh, 7, "Skip empty rows", hours=48)
    item(gh, 8, "Second item", hours=30)
    assert index(capsys, "--github")[0] == 0
    first = lists(gh)[-1]
    assert search_arg(first) == "sort:updated-asc"
    cursor = gh.get(REPO, 8)["updatedAt"]
    assert index(capsys, "--github")[0] == 0
    assert search_arg(lists(gh)[-1]) == "updated:%s..* sort:updated-asc" % cursor
    item(gh, 7, "Skip empty rows, and blank lines", hours=1,
         comments=[{"body": "Blank lines too, it turns out.", "createdAt": ago(1)}])
    assert index(capsys, "--github")[0] == 0
    got = rows()
    assert len([r for r in got if r["record"] == "work:%s#7" % REPO]) == 1          # replaced, not doubled
    assert "blank lines" in next(r["text"] for r in got if r["record"] == "work:%s#7" % REPO)
    assert any("Blank lines too" in r["text"] for r in got)


def test_a_full_page_reads_on_from_the_new_cursor(gh, capsys, monkeypatch):
    monkeypatch.setattr(W, "LIST_LIMIT", 2)
    for n in range(1, 6):
        item(gh, n, "Item number %d" % n, hours=50 - n)
    assert index(capsys, "--github")[0] == 0
    assert {r["record"] for r in rows()} == {"work:%s#%d" % (REPO, n) for n in range(1, 6)}
    # Each page starts at the last one's newest item (GitHub's range includes it), so pages of 2 move on
    # by one item: 1-2, 2-3, 3-4, 4-5, then 5 alone, which is not a full page.
    calls = lists(gh)
    assert len(calls) == 5 and all(c[c.index("--limit") + 1] == "2" for c in calls)
    assert search_arg(calls[1]) == "updated:%s..* sort:updated-asc" % gh.get(REPO, 2)["updatedAt"]


# ---- when GitHub can't be read ------------------------------------------------------------------------
def _transcript(gh, tmp_path):
    p = tmp_path / "claude" / "projects" / "-projects-sample-app" / "5e551011-aaaa-4bbb-8ccc-000000000001.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"type": "user", "uuid": "u-1", "sessionId": "s-1", "cwd": str(gh.project),
                             "timestamp": ago(2), "message": {"role": "user", "content": "the importer drops rows"}})
                 + "\n", encoding="utf-8")


def test_without_gh_one_line_says_so_and_transcripts_are_still_indexed(gh, capsys, monkeypatch, tmp_path):
    _transcript(gh, tmp_path)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    rc, out, err = index(capsys, "--github")
    assert rc == 0
    assert err.count("\n") == 1 and "gh" in err and "not installed" in err
    assert [r["source"] for r in rows()] == ["sessions"]
    assert "GitHub" not in out


def test_not_logged_in_says_so(gh, capsys, tmp_path):
    _transcript(gh, tmp_path)
    gh.fail("auth")
    rc, _, err = index(capsys, "--github")
    assert rc == 0 and "not logged in" in err and "gh auth login" in err
    assert [r["source"] for r in rows()] == ["sessions"]


def test_a_folder_with_no_repository_asks_for_one(gh, capsys, monkeypatch, tmp_path):
    elsewhere = tmp_path / "loose"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rc, _, err = index(capsys, "--github")
    assert rc == 0 and "no GitHub repository" in err and "--github owner/name" in err


def test_the_session_start_refresh_never_calls_github(gh, tmp_path):
    item(gh, 7, "Skip empty rows")
    _transcript(gh, tmp_path)
    H.run_index()                        # what history_refresh.py starts: no arguments, so no --github
    assert gh.calls() == []
    assert not SW.enabled(None)
