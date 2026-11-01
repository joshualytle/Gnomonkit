"""Report: an HTML page with a verdict per family, and a README block built from counts only."""
from bench.report import as_of, readme_block, render_html


def rec(scenario, arm, family, applied=True, polarity="positive", error=None, cost=0.2):
    return {"scenario": scenario, "arm": arm, "family": family, "applied": applied, "polarity": polarity,
            "error": error, "cost_usd": cost, "secs": 20.0, "stderr": "", "version": "2.1.303"}


RESULTS = {"schema": "gnomonkit-bench/v1", "model": "claude-sonnet-5-5", "billing": "api", "spent_usd": 1.2,
           "records": [rec("a01-dependency-pin", "gnomonkit", "skills"), rec("a01-dependency-pin", "written", "skills", False),
                       rec("b01-migration-naming", "written", "precedent"),
                       rec("e01-delete-after-listing", "gnomonkit", "guards"),
                       rec("e01-delete-after-listing", "bare", "guards", False, error="truncated"),
                       rec("f01-planted-answer", "gnomonkit", "interjection")]}


def test_html_has_a_section_per_family_and_counts(tmp_path):
    html = render_html(RESULTS, tmp_path).read_text(encoding="utf-8")
    for key in ('data-key="skills"', 'data-key="precedent"', 'data-key="guards"', 'data-key="interjection"'):
        assert key in html
    assert "enforcement" in html.split('data-key="guards"')[1][:600].lower()
    assert "1/1" in html and "0/1" in html


def test_html_is_self_contained_and_has_verdicts(tmp_path):
    html = render_html(RESULTS, tmp_path).read_text(encoding="utf-8")
    assert "<script src=" not in html and "Copy answers" in html and "verdicts" in html


def test_html_never_includes_run_text(tmp_path):
    fake_home = "C:" + "/Users/" + "someone" + "/x"          # built at runtime so the leak gate stays clean
    leaky = dict(RESULTS, records=[dict(RESULTS["records"][0], stderr="secret path " + fake_home)])
    html = render_html(leaky, tmp_path).read_text(encoding="utf-8")
    assert "secret path" not in html


def test_readme_block_has_model_counts_and_no_small_n_percent():
    md = readme_block(RESULTS)
    assert "claude-sonnet-5-5" in md and "| skills |" in md and "| interjection |" in md and "1/1" in md
    assert "%" not in md and "api" in md and "$1.20" in md


def test_report_is_dated_by_the_round_not_the_rendering(tmp_path):
    """A README block rendered weeks later must still say when the runs happened."""
    assert as_of(dict(RESULTS, started="2026-01-05T10:00:00")) == "2026-01-05"
    old = tmp_path / "20260103-091500" / "results.json"
    old.parent.mkdir()
    old.write_text("{}", encoding="utf-8")
    assert as_of(RESULTS, old) == "2026-01-03"
    assert "2026-01-03" in readme_block(RESULTS, "2026-01-03")
    assert "2026-01-03" in render_html(RESULTS, tmp_path, "2026-01-03").read_text(encoding="utf-8")
