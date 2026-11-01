"""Command line: dry run needs no Claude, a full round needs --yes, gate exit codes follow the verdict."""
import json

from bench.__main__ import main


def test_dry_run_builds_every_scenario(capsys):
    assert main(["dry-run"]) == 0
    out = capsys.readouterr().out
    assert "scenarios" in out and "repos built" in out


def test_full_round_without_yes_stops_before_launching(tmp_path, capsys):
    calls = []
    rc = main(["run", "--mode", "full", "--out", str(tmp_path / "round"), "--allow-host"],
              launcher=lambda *a, **k: calls.append(a) or 0)
    assert rc == 3 and calls == []
    out = capsys.readouterr().out
    assert "runs" in out and "estimate" in out and "--yes" in out


def test_out_inside_a_repo_is_refused_before_anything_is_written():
    """Review #3: build_arms used to copy skills next to --out before the path check ran."""
    from pathlib import Path
    inside = Path(__file__).resolve().parent / "zz-out-check" / "round"
    rc = main(["run", "--mode", "full", "--out", str(inside), "--allow-host"], launcher=lambda *a, **k: 0)
    assert rc == 2 and not inside.parent.exists()


def _results(tmp_path, new_k):
    recs = []
    for i in range(15):
        recs.append({"scenario": f"a{i % 3}", "family": "skills", "polarity": "positive", "arm": "gnomonkit",
                     "applied": i < new_k, "error": None, "cost_usd": 0.1})
        recs.append({"scenario": f"a{i % 3}", "family": "skills", "polarity": "positive", "arm": "written",
                     "applied": i < 6, "error": None, "cost_usd": 0.1})
    p = tmp_path / f"r{new_k}.json"
    p.write_text(json.dumps({"schema": "gnomonkit-bench/v1", "model": "m", "records": recs}), encoding="utf-8")
    return p


def test_gate_exit_codes(tmp_path):
    assert main(["gate", str(_results(tmp_path, 15)), "--family", "skills", "--arm", "gnomonkit"]) == 0
    assert main(["gate", str(_results(tmp_path, 6)), "--family", "skills", "--arm", "gnomonkit"]) == 1


def test_report_writes_html_and_prints_readme_block(tmp_path, capsys):
    assert main(["report", str(_results(tmp_path, 15)), "--out", str(tmp_path / "rep")]) == 0
    assert (tmp_path / "rep" / "report.html").exists() and "| skills |" in capsys.readouterr().out
