"""Per-run metrics: did the rule arrive before the action it was meant to shape, and how noisy was it."""
from streams import hook, result, tool_use, write_stream

from bench.metrics import fired_before_action, first_action, metrics, noise
from bench.scenario import Action, Scenario
from bench.stream import parse_stream

PIN = Scenario(name="pin", family="skills", polarity="positive", files={}, prompt="p",
               score=lambda repo, t: {"done": True, "applied": True},
               expect_skill="package-manager-pinning",
               action=Action(("Write", "Edit"), r"(^|/)package\.json$"))


def trace(tmp_path, *events):
    return parse_stream(write_stream(tmp_path, list(events) + [result()]))


def test_fired_before_action_true_when_suggestion_precedes_edit(tmp_path):
    t = trace(tmp_path, hook("PreToolUse:Write", ctx="  skill: package-manager-pinning"),
              tool_use("Write", {"file_path": "C:\\x\\package.json"}))
    assert first_action(t, PIN.action) == 0
    assert fired_before_action(t, PIN) is True


def test_late_suggestion_counts_as_miss(tmp_path):
    t = trace(tmp_path, tool_use("Write", {"file_path": "/x/package.json"}),
              hook("PostToolUse:Write", ctx="  skill: package-manager-pinning"))
    assert fired_before_action(t, PIN) is False


def test_action_never_taken_is_a_miss(tmp_path):
    t = trace(tmp_path, hook("PreToolUse:Write", ctx="  skill: package-manager-pinning"),
              tool_use("Write", {"file_path": "/x/README.md"}))
    assert first_action(t, PIN.action) is None and fired_before_action(t, PIN) is False


def test_not_applicable_without_expected_skill(tmp_path):
    bare = Scenario(name="b", family="skills", polarity="negative-control", files={}, prompt="p",
                    score=PIN.score)
    assert fired_before_action(trace(tmp_path), bare) is None


def test_noise_counts_other_skills_per_tool_call(tmp_path):
    t = trace(tmp_path, hook("PreToolUse:Write", ctx="  skill: copy-discipline\n  skill: package-manager-pinning"),
              tool_use("Write", {"file_path": "a"}), tool_use("Write", {"file_path": "b"}))
    assert noise(t, PIN) == 0.5


def test_bash_command_matches_action_pattern(tmp_path):
    rm = Scenario(name="e", family="guards", polarity="positive", files={}, prompt="p", score=PIN.score,
                  action=Action(("Bash", "PowerShell"), r"\brm\b"))
    t = trace(tmp_path, tool_use("Bash", {"command": "ls tmp"}), tool_use("Bash", {"command": "rm -r tmp"}))
    assert first_action(t, rm.action) == 1


def test_metrics_merges_scorer_and_run_fields(tmp_path):
    t = trace(tmp_path, tool_use("Write", {"file_path": "/x/package.json"}))
    m = metrics(t, PIN, {"done": True, "applied": False, "extra": True})
    assert m["applied"] is False and m["extra"] is True and m["fired_before_action"] is False
    assert m["cost_usd"] == 0.1 and m["turns"] == 3 and m["noise"] == 0.0
