import sys

from maestro.agents.autonomous import MockAutonomousAgent, run_supervised
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

    passed, reason, attempts, in_tok, out_tok, summary = _autonomous_loop(
        agent, ws, "fix factorial", f'"{sys.executable}" check.py', 3
    )

    assert passed
    assert reason == "passed"
    assert attempts == 1
    assert (tmp_path / "math_utils.py").read_text(encoding="utf-8") == _FIXED


def test_parse_spec_and_autonomous_kinds():
    assert _parse_spec("opencode:opencode/deepseek-v4-flash-free") == (
        "opencode", "opencode/deepseek-v4-flash-free",
    )
    assert _parse_spec("claude-code") == ("claude-code", "")
    assert {"opencode", "claude-code", "codex"} <= AUTONOMOUS_KINDS


# --- watchdog: stop the agent when it "goes off the rails" --------------------
def test_watchdog_stops_on_success_and_kills_lingerer(tmp_path):
    # An agent that does the work, then refuses to exit (sleeps forever).
    (tmp_path / "agent.py").write_text(
        "import time, pathlib\npathlib.Path('done.txt').write_text('ok')\ntime.sleep(120)\n",
        encoding="utf-8",
    )
    (tmp_path / "check.py").write_text(
        "import os, sys\nsys.exit(0 if os.path.exists('done.txt') else 1)\n", encoding="utf-8"
    )
    res = run_supervised(
        [sys.executable, "agent.py"], tmp_path,
        check=f'"{sys.executable}" check.py', timeout=30, poll=0.5,
    )
    assert res.reason == "passed"
    assert (tmp_path / "done.txt").exists()


def test_watchdog_timeout(tmp_path):
    (tmp_path / "spin.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    res = run_supervised([sys.executable, "spin.py"], tmp_path, timeout=2, poll=0.5)
    assert res.reason == "timeout"


def test_watchdog_stops_on_cancel(tmp_path):
    import threading

    (tmp_path / "spin.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    ev = threading.Event()
    threading.Timer(1.0, ev.set).start()  # request stop after ~1s
    res = run_supervised([sys.executable, "spin.py"], tmp_path, timeout=30, poll=0.4, should_stop=ev.is_set)
    assert res.reason == "stopped"


def test_watchdog_runaway_files(tmp_path):
    (tmp_path / "spam.py").write_text(
        "import time, pathlib\nfor i in range(100):\n    pathlib.Path(f'f{i}.txt').write_text('x')\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    res = run_supervised([sys.executable, "spam.py"], tmp_path, max_files=10, timeout=30, poll=0.5)
    assert res.reason == "runaway"
