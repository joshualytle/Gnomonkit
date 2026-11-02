"""The published round: both README tables come from bench/latest.json and nowhere else."""
import pytest

from bench import published
from bench.__main__ import main


def rec(scenario, arm, family, applied=True, rep=0, polarity="positive", error=None):
    return {"scenario": scenario, "arm": arm, "family": family, "applied": applied, "polarity": polarity,
            "error": error, "rep": rep, "cost_usd": 0.1, "secs": 1.0, "stderr": "", "version": "x"}


RESULTS = {"schema": "gnomonkit-bench/v1", "started": "2026-03-04", "model": "sonnet", "records": [
    rec("f01-planted-answer", "bare", "interjection", False, 0), rec("f01-planted-answer", "bare", "interjection", False, 1),
    rec("f01-planted-answer", "gnomonkit", "interjection", True, 0),
    rec("f01-planted-answer", "gnomonkit", "interjection", False, 1),
    rec("f01-planted-answer", "written", "interjection", True, 0),
    rec("a01-dependency-pin", "bare", "skills", True, 0), rec("a01-dependency-pin", "gnomonkit", "skills", True, 1),
    rec("a01-dependency-pin", "gnomonkit", "skills", True, 0, polarity="control"),
    rec("b01-migration-naming", "written", "precedent", True, 0),
    rec("e01-delete-after-listing", "bare", "guards", True, 0, error="truncated"),
]}


def test_the_committed_readmes_match_latest_json():
    """The guard itself: a hand edit to either table, or a new round in only one of them, fails here."""
    assert published.stale(published.load()) == []


def test_summary_counts_positive_runs_and_skips_errors():
    s = published.summary(RESULTS, "2026-03-04")
    assert s["round"] == "2026-03-04" and s["reps"] == 2 and s["mode"] == "container"
    assert s["tally"]["interjection"] == {"bare": [0, 2], "gnomonkit": [1, 2], "written": [1, 1]}
    assert s["tally"]["skills"] == {"bare": [1, 1], "gnomonkit": [1, 1]}  # the control run is not counted
    assert "guards" not in s["tally"]  # its one run errored


def test_both_blocks_come_from_the_same_counts():
    s = published.summary(RESULTS, "2026-03-04")
    main_b, bench_b = published.main_block(s), published.bench_block(s)
    assert "One round on Sonnet in March 2026" in main_b and "scenario twice:" in main_b
    assert "| interjection (2) | 0/2 | 1/2 |" in main_b
    assert "precedent" not in main_b  # the project README shows the families with a gnomonkit arm
    assert "Sonnet, March 2026 (round of 2026-03-04)" in bench_b
    assert "| interjection (2) | 0/2 | 1/1 | 1/2 |" in bench_b  # bare | written | gnomonkit
    assert "| precedent (1) | no arm yet | 1/1 | no arm yet |" in bench_b


def test_splice_replaces_only_the_marked_block_and_refuses_missing_markers():
    start, end = published._marks("main")
    text = f"before\n{start}\nold table\n{end}\nafter\n"
    assert published.splice(text, "main", "new table") == f"before\n{start}\nnew table\n{end}\nafter\n"
    with pytest.raises(ValueError):
        published.splice("no markers here\n", "main", "x")


def test_publish_writes_latest_and_both_readmes(tmp_path, monkeypatch):
    start_m, end_m = published._marks("main")
    start_b, end_b = published._marks("latest")
    main_r, bench_r, latest = tmp_path / "README.md", tmp_path / "bench-README.md", tmp_path / "latest.json"
    main_r.write_text(f"# x\n{start_m}\nstale\n{end_m}\n", encoding="utf-8")
    bench_r.write_text(f"# y\n{start_b}\nstale\n{end_b}\n", encoding="utf-8")
    monkeypatch.setattr(published, "MAIN_README", main_r)
    monkeypatch.setattr(published, "BENCH_README", bench_r)
    monkeypatch.setattr(published, "LATEST", latest)
    results = tmp_path / "results.json"
    import json
    results.write_text(json.dumps(RESULTS), encoding="utf-8")

    assert main(["publish", str(results)]) == 0
    assert "stale" not in main_r.read_text(encoding="utf-8") and "stale" not in bench_r.read_text(encoding="utf-8")
    assert main(["publish", "--check"]) == 0
    bench_r.write_text(bench_r.read_text(encoding="utf-8").replace("0/2", "2/2"), encoding="utf-8")
    assert main(["publish", "--check"]) == 1
