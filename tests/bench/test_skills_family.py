"""Skills family: a core skill's situation comes up; the scorer checks the behaviour it teaches."""
import json

from streams import hook, make_repo, trace_of

from bench.scenario import load_all, validate


def by_name(name):
    return next(s for s in load_all() if s.name == name)


def _pkg(dayjs):
    deps = {"express": "4.19.2", "zod": "3.23.8"}
    if dayjs is not None:
        deps["dayjs"] = dayjs
    return {"package.json": json.dumps({"name": "acme-app", "dependencies": deps}, indent=2)}


def test_a01_exact_version_applies(tmp_path):
    r = by_name("a01-dependency-pin").score(make_repo(tmp_path, _pkg("1.11.13")), trace_of(tmp_path))
    assert r == {"done": True, "applied": True}


def test_a01_range_does_not(tmp_path):
    for i, v in enumerate(("^1.11.13", "~1.11.13", "latest", "1.x", "*")):
        r = by_name("a01-dependency-pin").score(make_repo(tmp_path, _pkg(v), name=f"r{i}"), trace_of(tmp_path))
        assert r["applied"] is False and r["done"] is True, v


def test_a01_missing_dependency_is_not_done(tmp_path):
    r = by_name("a01-dependency-pin").score(make_repo(tmp_path, _pkg(None)), trace_of(tmp_path))
    assert r["done"] is False and r["applied"] is False


GOOD_JOB = """import os, time
def main():
    removed = 0
    for name in os.listdir('tmp_exports'):
        removed += 1
    if removed == 0:
        print('nothing to remove: 0 files older than 30 days')
    print(f'removed {removed} files')
"""
SILENT_JOB = """import os
def main():
    for name in os.listdir('tmp_exports'):
        os.remove(name)
"""


def test_a02_counted_job_applies(tmp_path):
    r = by_name("a02-nightly-job-counts").score(make_repo(tmp_path, {"jobs/nightly_cleanup.py": GOOD_JOB}),
                                                trace_of(tmp_path))
    assert r["applied"] is True


PER_FILE_PRINT_JOB = """import os, time
def main(d='tmp_exports'):
    if not os.path.isdir(d):
        return
    for name in os.listdir(d):
        p = os.path.join(d, name)
        os.remove(p)
        print(f"Deleted {p}")
"""


def test_a02_per_file_print_is_not_a_count(tmp_path):
    """Review #6: a per-file print and a directory check passed both regexes with no count and no zero case."""
    r = by_name("a02-nightly-job-counts").score(make_repo(tmp_path, {"jobs/nightly_cleanup.py": PER_FILE_PRINT_JOB}),
                                                trace_of(tmp_path))
    assert r["applied"] is False


def test_a02_silent_job_does_not(tmp_path):
    r = by_name("a02-nightly-job-counts").score(make_repo(tmp_path, {"jobs/nightly_cleanup.py": SILENT_JOB}),
                                                trace_of(tmp_path))
    assert r["applied"] is False and r["done"] is True


def test_a03_runbook_with_impact_and_verification(tmp_path):
    md = "# Restart ingest\n\nImpact: ingest is unavailable for about 2 minutes.\n\n1. Restart.\n2. Verify the queue drains.\n"
    r = by_name("a03-runbook-impact").score(make_repo(tmp_path, {"runbooks/restart-ingest.md": md}), trace_of(tmp_path))
    assert r["applied"] is True


def test_a03_runbook_without_impact(tmp_path):
    md = "# Restart ingest\n\n1. Run the restart command.\n2. Verify it is running.\n"
    r = by_name("a03-runbook-impact").score(make_repo(tmp_path, {"runbooks/restart-ingest.md": md}), trace_of(tmp_path))
    assert r["applied"] is False


def test_a04_quiet_rename(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def process_item(x):\n    return x\n\nprocess_item(1)\n"})
    sc = by_name("a04-quiet-rename")
    assert sc.polarity == "negative-control"
    assert sc.score(repo, trace_of(tmp_path, name="q.out"))["applied"] is True
    noisy = trace_of(tmp_path, hook("PreToolUse:Edit", ctx="  skill: copy-discipline"), name="n.out")
    assert sc.score(repo, noisy)["applied"] is False


def test_a04_incomplete_rename_is_not_done(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def process_item(x):\n    return x\n\nproc(1)\n"})
    assert by_name("a04-quiet-rename").score(repo, trace_of(tmp_path))["done"] is False


def _run(tmp_path, answer, *events, name="d.out"):
    from streams import result, write_stream

    from bench.stream import parse_stream
    return parse_stream(write_stream(tmp_path, list(events) + [result(answer)], name=name))


PAGE = '<section id="r-south" data-ask="Fund|Hold"><h2>South grew 5,170</h2></section>'


def test_a05_page_with_verdicts_applies(tmp_path):
    from streams import tool_use
    t = _run(tmp_path, "South and east grew; the page has the breakdown.",
             tool_use("Write", {"file_path": "C:/scratch/region-growth.html", "content": PAGE + " east"}))
    assert by_name("a05-data-breakdown-page").score(make_repo(tmp_path, {}), t) == {"done": True, "applied": True}


def test_a05_text_answer_is_done_not_applied(tmp_path):
    t = _run(tmp_path, "South grew by 5,170 and east by 4,550; north and west shrank.")
    assert by_name("a05-data-breakdown-page").score(make_repo(tmp_path, {}), t) == {"done": True, "applied": False}


def test_a05_html_without_verdict_sections_is_not_a_page(tmp_path):
    from streams import tool_use
    t = _run(tmp_path, "South and east grew.",
             tool_use("Write", {"file_path": "out/report.html", "content": "<table><tr><td>south</td></tr></table>"}))
    assert by_name("a05-data-breakdown-page").score(make_repo(tmp_path, {}), t)["applied"] is False


def test_a06_right_count_without_page_applies(tmp_path):
    from bench.scenarios.skills import ORDER_ROWS
    sc = by_name("a06-one-number-no-page")
    assert sc.polarity == "negative-control"
    assert sc.score(make_repo(tmp_path, {}), _run(tmp_path, f"There are {ORDER_ROWS} orders.")) == \
        {"done": True, "applied": True}


def test_a06_page_for_one_number_is_not_applied(tmp_path):
    from streams import tool_use

    from bench.scenarios.skills import ORDER_ROWS
    t = _run(tmp_path, f"{ORDER_ROWS} orders.", tool_use("Write", {"file_path": "count.html", "content": PAGE}))
    assert by_name("a06-one-number-no-page").score(make_repo(tmp_path, {}), t)["applied"] is False


def test_a06_wrong_count_is_not_done(tmp_path):
    assert by_name("a06-one-number-no-page").score(make_repo(tmp_path, {}), _run(tmp_path, "33 orders"))["done"] is False


def test_skills_family_validates_and_names_expected_skills():
    fam = [s for s in load_all() if s.family == "skills"]
    assert validate(fam) == [] and len(fam) == 13
    expected = {s.name: s.expect_skill for s in fam}
    assert expected["a05-data-breakdown-page"] == "report-pages"
    assert expected["a06-one-number-no-page"] is None
    assert expected["a01-dependency-pin"] == "package-manager-pinning"
    assert expected["a02-nightly-job-counts"] == "silent-no-op"
    assert expected["a03-runbook-impact"] == "production-impact-callout"
    assert expected["a04-quiet-rename"] is None
