"""Aggregation: k/n that excludes errors, a one-sided Fisher test, and the ship gate."""
import pytest

from bench.aggregate import compare, family_tally, fisher_one_sided, floor_flags, fmt_kn, gate, tally


def rec(scenario, arm, applied=True, error=None, family="skills", polarity="positive", cost=0.1):
    return {"scenario": scenario, "arm": arm, "applied": applied, "error": error, "family": family,
            "polarity": polarity, "cost_usd": cost}


def positives(new, base, n, family="skills"):
    out = []
    for i in range(n):
        out.append(rec(f"p{i % 3}", "gnomonkit", applied=i < new, family=family))
        out.append(rec(f"p{i % 3}", "written", applied=i < base, family=family))
    return out


def control(name, new_fail, base_fail, n):
    out = []
    for i in range(n):
        out.append(rec(name, "gnomonkit", applied=i >= new_fail, polarity="negative-control"))
        out.append(rec(name, "written", applied=i >= base_fail, polarity="negative-control"))
    return out


def test_fisher_known_values():
    assert fisher_one_sided(15, 15, 6, 15) == pytest.approx(0.000350, rel=1e-2)   # comb(21,15)/comb(30,15)
    assert fisher_one_sided(5, 10, 5, 10) == pytest.approx(0.6719, rel=1e-3)
    assert fisher_one_sided(0, 0, 3, 5) == 1.0


def test_errors_excluded_from_n():
    t = tally([rec("b01", "written", applied=True, family="precedent"),
               rec("b01", "written", error="truncated", applied=False, family="precedent")])
    assert t[("precedent", "b01", "written")] == {"k": 1, "n": 1, "errors": 1}


def test_behaviour_errors_count_as_failures_not_exclusions():
    """Review #5: an arm that makes the model loop or time out must not have those runs dropped."""
    recs = [rec("a01", "gnomonkit", applied=True),
            dict(rec("a01", "gnomonkit", applied=False, error="timeout"), error_kind="behaviour"),
            dict(rec("a01", "gnomonkit", applied=False, error="no-result"), error_kind="infra")]
    assert tally(recs)[("skills", "a01", "gnomonkit")] == {"k": 1, "n": 2, "errors": 1}


def test_family_tally_counts_positives_only():
    t = family_tally(positives(3, 1, 3) + control("c", 0, 0, 3))
    assert t[("skills", "gnomonkit")] == {"k": 3, "n": 3, "errors": 0}


def test_gate_ships_on_clear_gain():
    g = gate(positives(15, 6, 15) + control("a04", 0, 0, 3), "skills", "gnomonkit")
    assert (g["k_new"], g["n_new"], g["k_base"], g["n_base"]) == (15, 15, 6, 15)
    assert g["p"] < 0.10 and g["controls_ok"] and g["verdict"] == "ship"


def test_gate_holds_when_a_control_regresses():
    g = gate(positives(15, 6, 15) + control("a04", new_fail=2, base_fail=0, n=3), "skills", "gnomonkit")
    assert g["p"] < 0.10 and g["controls_ok"] is False and g["verdict"] == "hold"


def test_gate_tolerates_one_extra_control_failure():
    g = gate(positives(15, 6, 15) + control("a04", new_fail=1, base_fail=0, n=3), "skills", "gnomonkit")
    assert g["controls_ok"] is True


def test_gate_holds_without_significance():
    assert gate(positives(5, 5, 10), "skills", "gnomonkit")["verdict"] == "hold"


def test_floor_flags_too_easy_scenarios():
    recs = [rec("a01", "bare", applied=True), rec("a01", "bare", applied=True), rec("a01", "bare", applied=False),
            rec("a02", "bare", applied=False), rec("a02", "bare", applied=False),
            rec("c", "bare", applied=True, polarity="negative-control")]
    assert floor_flags(recs) == ["a01"]


def test_compare_rounds():
    old = [rec("a01", "gnomonkit", applied=False), rec("a01", "gnomonkit", applied=True)]
    new = [rec("a01", "gnomonkit", applied=True), rec("a01", "gnomonkit", applied=True)]
    row = compare(old, new)[0]
    assert (row["scenario"], row["arm"], row["old"], row["new"]) == ("a01", "gnomonkit", "1/2", "2/2")
    assert row["delta"] == pytest.approx(0.5)


def test_fmt_kn_hides_percent_below_ten():
    assert fmt_kn(4, 6) == "4/6" and fmt_kn(9, 12) == "9/12 (75%)" and fmt_kn(0, 0) == "0/0"
