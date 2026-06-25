import pytest

from maestro.agents.mock_agent import MockAgent
from maestro.auto import _plan


def test_plan_parses_valid_json():
    plan = '{"steps":[{"title":"fix","task":"fix the bug","check":"true"}]}'
    orchestrator = MockAgent("supervisor", [plan])
    steps = _plan(orchestrator, "fix everything", "file_tree")
    assert steps == [{"title": "fix", "task": "fix the bug", "check": "true"}]


def test_plan_drops_incomplete_steps():
    plan = '{"steps":[{"title":"no check"},{"title":"ok","task":"t","check":"c"}]}'
    orchestrator = MockAgent("supervisor", [plan])
    steps = _plan(orchestrator, "goal", "tree")
    assert len(steps) == 1
    assert steps[0]["title"] == "ok"


def test_plan_raises_clear_error_on_non_json_reply():
    orchestrator = MockAgent("supervisor", ["sorry, I can't help with that."])
    with pytest.raises(RuntimeError) as exc:
        _plan(orchestrator, "goal", "tree")
    assert "did not return valid JSON" in str(exc.value)
    assert "sorry" in str(exc.value)
