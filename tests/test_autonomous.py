import sys

from maestro.agents.autonomous import MockAutonomousAgent
from maestro.config import AUTONOMOUS_KINDS
from maestro.race import _autonomous_loop, _parse_spec
from maestro.workspace import Workspace

_BROKEN = "def factorial(n):\n    r = 1\n    for i in range(1, n):\n        r *= i\n    return r\n"
_FIXED = "def factorial(n):\n    r = 1\n    for i in range(1, n + 1):\n        r *= i\n    return r\n"
_CHECK = "from math_utils import factorial\nassert factorial(5) == 120\nprint('ok')\n"


def test_autonomous_loop_fixes_and_passes(tmp_path):
    (tmp_path / "math_utils.py").write_text(_BROKEN, encoding="utf-8")
    (tmp_path / "check.py").write_text(_CHECK, encoding="utf-8")
    ws = Workspace(tmp_path)
    agent = MockAutonomousAgent({"math_utils.py": _FIXED})

    passed, attempts, in_tok, out_tok, summary = _autonomous_loop(
        agent, ws, "fix factorial", f'"{sys.executable}" check.py', 3
    )

    assert passed
    assert attempts == 1
    assert (tmp_path / "math_utils.py").read_text(encoding="utf-8") == _FIXED


def test_parse_spec_and_autonomous_kinds():
    assert _parse_spec("opencode:opencode/deepseek-v4-flash-free") == (
        "opencode", "opencode/deepseek-v4-flash-free",
    )
    assert _parse_spec("claude-code") == ("claude-code", "")
    assert {"opencode", "claude-code", "codex"} <= AUTONOMOUS_KINDS
