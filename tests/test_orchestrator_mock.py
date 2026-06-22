import json
import sys

from maestro.agents.mock_agent import MockAgent
from maestro.cli import (
    _DEMO_CALCULATOR,
    _DEMO_CHECK,
    _DEMO_EXEC_RIGHT,
    _DEMO_EXEC_WRONG,
)
from maestro.ledger import Ledger, Price
from maestro.orchestrator import Orchestrator
from maestro.workspace import Workspace


def test_supervision_by_exception_loop(tmp_path):
    (tmp_path / "calculator.py").write_text(_DEMO_CALCULATOR, encoding="utf-8")
    (tmp_path / "check_add.py").write_text(_DEMO_CHECK, encoding="utf-8")

    check = f'"{sys.executable}" check_add.py'
    plan = {"steps": [{"id": "s1", "title": "fix add", "instruction": "fix add", "check": check}]}
    intervention = {"instruction": "use + not *", "note": "wrong operator"}

    supervisor = MockAgent("supervisor", [json.dumps(plan), json.dumps(intervention)])
    executor = MockAgent("executor", [_DEMO_EXEC_WRONG, _DEMO_EXEC_RIGHT])
    ledger = Ledger(Price(5, 25), Price(0, 0))

    orch = Orchestrator(supervisor, executor, Workspace(tmp_path), ledger)
    result = orch.run("Fix calculator.py so the checks pass.")

    assert result.success
    assert result.outcomes[0].attempts == 2  # failed once, fixed after intervention
    assert "return a + b" in (tmp_path / "calculator.py").read_text(encoding="utf-8")
    assert ledger.savings_pct() > 0
