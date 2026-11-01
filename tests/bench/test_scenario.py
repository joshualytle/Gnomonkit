"""Scenario model: validation catches the mistakes that would make a benchmark lie."""
from bench.scenario import Scenario, validate


def _score(repo, trace):
    return {"done": True, "applied": True}


def _sc(name, family, polarity, prompt="Do the task. Do the work directly, without asking questions.",
        hint_terms=()):
    return Scenario(name=name, family=family, polarity=polarity, files={"app.py": ""}, prompt=prompt,
                    score=_score, hint_terms=hint_terms)


def test_validate_catches_each_problem():
    ok = _sc("a1", "skills", "positive")
    problems = validate([
        ok, ok,                                              # duplicate name
        _sc("x", "nonsense", "positive"),                    # bad family
        _sc("y", "skills", "maybe"),                         # bad polarity
        _sc("z", "skills", "positive", prompt="use exact pinned versions", hint_terms=("pinned",)),
    ])
    assert any("duplicate" in p for p in problems)
    assert any("family" in p for p in problems)
    assert any("polarity" in p for p in problems)
    assert any("names the rule" in p and "z" in p for p in problems)


def test_family_needs_three_positives_and_a_control():
    problems = validate([_sc(f"p{i}", "skills", "positive") for i in range(2)])
    assert any("skills" in p and "3 positive" in p for p in problems)
    assert any("skills" in p and "negative-control" in p for p in problems)


def test_complete_family_is_valid():
    family = [_sc(f"p{i}", "guards", "positive") for i in range(3)] + [_sc("n", "guards", "negative-control")]
    assert validate(family) == []


def test_hint_match_is_whole_word_and_case_insensitive():
    clean = _sc("w", "skills", "positive", prompt="Pinnedness aside, add it.", hint_terms=("pinned",))
    caught = _sc("v", "skills", "positive", prompt="Add it, PINNED please.", hint_terms=("pinned",))
    problems = validate([clean, caught])
    assert not any("names the rule" in p and "'w'" in p for p in problems)
    assert any("names the rule" in p and "'v'" in p for p in problems)
