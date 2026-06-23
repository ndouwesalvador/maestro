"""Command-line interface.

    maestro demo                      # zero-config offline demo (tiny)
    maestro demo --pro                # zero-config offline demo (realistic module)
    maestro run --task T --repo DIR   # drive a real local/frontier pair
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

from . import __version__
from .config import LOCAL_PRICE, SUPERVISOR_PRICE, build_agent, price_for
from .ledger import Ledger
from .orchestrator import Orchestrator
from .workspace import Workspace

BANNER = r"""
  __  __         _
 |  \/  | __ _ ___| |_ _ __ ___    One frontier model conducts
 | |\/| |/ _` / -_)  _| '__/ _ \   an orchestra of free local models.
 |_|  |_|\__,_\___|\__|_|  \___/   v%s
""" % __version__


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _print_result(result, ledger: Ledger) -> int:
    print("\n" + ledger.summary())
    verdict = "SUCCESS" if result.success else "INCOMPLETE"
    passed = sum(o.passed for o in result.outcomes)
    print(f"\nRESULT: {verdict} ({passed}/{len(result.outcomes)} steps passed)")
    return 0 if result.success else 1


def _run_scripted_demo(
    files: Dict[str, str],
    supervisor_script: List[str],
    executor_script: List[str],
    goal: str,
) -> int:
    """Run the orchestrator with deterministic mock agents in a temp workspace.

    The mock agents replay canned replies, but token usage is measured from the
    *actual* prompts (the Executor receives full files; the Supervisor does
    not), so the ledger reflects the real architectural asymmetry.
    """
    from .agents.mock_agent import MockAgent

    workdir = Path(tempfile.mkdtemp(prefix="maestro-demo-"))
    try:
        for name, content in files.items():
            (workdir / name).write_text(content, encoding="utf-8")

        supervisor = MockAgent("supervisor", supervisor_script, name="mock-claude")
        executor = MockAgent("executor", executor_script, name="mock-local")
        ledger = Ledger(SUPERVISOR_PRICE, LOCAL_PRICE)
        orch = Orchestrator(supervisor, executor, Workspace(workdir), ledger, logger=print)
        result = orch.run(goal)
        return _print_result(result, ledger)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# simple demo scenario (tiny)
# --------------------------------------------------------------------------- #
_DEMO_CALCULATOR = '''\
"""Tiny math utilities used by the Maestro demo."""


def add(a, b):
    # BUG: this subtracts instead of adding.
    return a - b


def sub(a, b):
    return a - b


def mul(a, b):
    return a * b


def clamp(value, low, high):
    """Clamp value into the inclusive range [low, high]."""
    if value < low:
        return low
    if value > high:
        return high
    return value
'''

_DEMO_CHECK = '''\
from calculator import add, sub, mul

assert add(2, 3) == 5, f"add(2,3) returned {add(2, 3)}, expected 5"
assert sub(5, 2) == 3, f"sub(5,2) returned {sub(5, 2)}, expected 3"
assert mul(2, 3) == 6, f"mul(2,3) returned {mul(2, 3)}, expected 6"
print("OK: all checks passed")
'''

_DEMO_EXEC_WRONG = """\
calculator.py
<<<<<<< SEARCH
    # BUG: this subtracts instead of adding.
    return a - b
=======
    return a * b
>>>>>>> REPLACE
SUMMARY: Rewrote the buggy line in add().
"""

_DEMO_EXEC_RIGHT = """\
calculator.py
<<<<<<< SEARCH
    return a * b
=======
    return a + b
>>>>>>> REPLACE
SUMMARY: add() now returns a + b.
"""


def _simple_demo() -> int:
    check_cmd = f'"{sys.executable}" check_add.py'
    plan = {
        "steps": [
            {
                "id": "s1",
                "title": "Fix add() so it returns a + b",
                "instruction": "In calculator.py, make add(a, b) return the sum a + b.",
                "check": check_cmd,
            }
        ]
    }
    intervention = {
        "instruction": "You used multiplication. add() must use addition: replace 'return a * b' with 'return a + b'.",
        "note": "divergence: wrong operator (* instead of +)",
    }
    return _run_scripted_demo(
        files={"calculator.py": _DEMO_CALCULATOR, "check_add.py": _DEMO_CHECK},
        supervisor_script=[json.dumps(plan), json.dumps(intervention)],
        executor_script=[_DEMO_EXEC_WRONG, _DEMO_EXEC_RIGHT],
        goal="Fix the bug in calculator.py so all checks in check_add.py pass.",
    )


# --------------------------------------------------------------------------- #
# pro demo scenario (realistic module, two supervised steps)
# --------------------------------------------------------------------------- #
_PRO_TEMPERATURE = '''\
"""Temperature conversion toolkit (Maestro real-world demo).

A small but realistic module - several conversions, a classifier, a formatter,
a generic dispatcher and a couple of "feels like" estimators. It is the kind of
file a coding agent must read in full before it can safely edit. Two
independent bugs are planted so you can watch Maestro plan, delegate to the
local model, catch a divergence, correct it, and verify - across two steps.
"""

import math

ABSOLUTE_ZERO_C = -273.15
KELVIN_OFFSET = 273.15
RANKINE_OFFSET = 459.67


def c_to_f(celsius):
    """Convert degrees Celsius to degrees Fahrenheit."""
    # BUG: the freezing-point offset is wrong (should be 32).
    return celsius * 9 / 5 + 31


def f_to_c(fahrenheit):
    """Convert degrees Fahrenheit to degrees Celsius."""
    return (fahrenheit - 32) * 5 / 9


def c_to_k(celsius):
    """Convert degrees Celsius to Kelvin."""
    # BUG: the absolute-zero offset should be 273.15, not 273.
    return celsius + 273


def k_to_c(kelvin):
    """Convert Kelvin to degrees Celsius."""
    return kelvin - KELVIN_OFFSET


def f_to_k(fahrenheit):
    """Convert degrees Fahrenheit to Kelvin (via Celsius)."""
    return c_to_k(f_to_c(fahrenheit))


def k_to_f(kelvin):
    """Convert Kelvin to degrees Fahrenheit (via Celsius)."""
    return c_to_f(k_to_c(kelvin))


def c_to_rankine(celsius):
    """Convert degrees Celsius to degrees Rankine."""
    return c_to_f(celsius) + RANKINE_OFFSET


def is_valid_celsius(celsius):
    """Reject temperatures below absolute zero."""
    return celsius >= ABSOLUTE_ZERO_C


def classify(celsius):
    """Return a coarse human label for a Celsius temperature."""
    if celsius <= 0:
        return "freezing"
    if celsius < 15:
        return "cold"
    if celsius < 25:
        return "mild"
    if celsius < 35:
        return "warm"
    return "hot"


def format_temp(value, unit="C"):
    """Format a temperature with one decimal place and its unit letter."""
    return f"{value:.1f} {unit.upper()}"


def dew_point(celsius, humidity):
    """Approximate dew point in Celsius (Magnus formula); humidity in %."""
    a, b = 17.27, 237.7
    gamma = (a * celsius) / (b + celsius) + math.log(max(humidity, 1e-6) / 100.0)
    return (b * gamma) / (a - gamma)


_CONVERTERS = {
    ("C", "F"): c_to_f,
    ("F", "C"): f_to_c,
    ("C", "K"): c_to_k,
    ("K", "C"): k_to_c,
    ("F", "K"): f_to_k,
    ("K", "F"): k_to_f,
}


def convert(value, from_unit, to_unit):
    """Convert `value` between any two of C / F / K."""
    from_unit, to_unit = from_unit.upper(), to_unit.upper()
    if from_unit == to_unit:
        return value
    try:
        return _CONVERTERS[(from_unit, to_unit)](value)
    except KeyError:
        raise ValueError(f"cannot convert {from_unit} -> {to_unit}")


def _demo_table():
    """Print a small reference table (used when run as a script)."""
    for c in (-40, 0, 25, 37, 100):
        print(format_temp(c, "C"), "=", format_temp(c_to_f(c), "F"),
              "=", format_temp(c_to_k(c), "K"), "->", classify(c))


if __name__ == "__main__":
    _demo_table()
'''

_PRO_CHECK1 = '''\
from temperature import c_to_f

assert abs(c_to_f(0) - 32) < 1e-9, c_to_f(0)
assert abs(c_to_f(100) - 212) < 1e-9, c_to_f(100)
assert abs(c_to_f(-40) - (-40)) < 1e-9, c_to_f(-40)
print("step1 ok: Celsius -> Fahrenheit")
'''

_PRO_CHECK2 = '''\
from temperature import c_to_k

assert abs(c_to_k(0) - 273.15) < 1e-9, c_to_k(0)
assert abs(c_to_k(100) - 373.15) < 1e-9, c_to_k(100)
print("step2 ok: Celsius -> Kelvin")
'''

_PRO_EXEC_S1_WRONG = """\
temperature.py
<<<<<<< SEARCH
    # BUG: the freezing-point offset is wrong (should be 32).
    return celsius * 9 / 5 + 31
=======
    return celsius * 9 / 5 + 33
>>>>>>> REPLACE
SUMMARY: Adjusted the Fahrenheit offset (first attempt).
"""

_PRO_EXEC_S1_RIGHT = """\
temperature.py
<<<<<<< SEARCH
    return celsius * 9 / 5 + 33
=======
    return celsius * 9 / 5 + 32
>>>>>>> REPLACE
SUMMARY: Corrected the Fahrenheit offset to 32.
"""

_PRO_EXEC_S2 = """\
temperature.py
<<<<<<< SEARCH
    # BUG: the absolute-zero offset should be 273.15, not 273.
    return celsius + 273
=======
    return celsius + 273.15
>>>>>>> REPLACE
SUMMARY: Use 273.15 as the Kelvin offset.
"""


def _pro_demo() -> int:
    check1 = f'"{sys.executable}" check_step1.py'
    check2 = f'"{sys.executable}" check_step2.py'
    plan = {
        "steps": [
            {
                "id": "s1",
                "title": "Fix Celsius -> Fahrenheit offset",
                "instruction": "In temperature.py, fix c_to_f so freezing maps correctly: c_to_f(0) must be 32.",
                "check": check1,
            },
            {
                "id": "s2",
                "title": "Fix Celsius -> Kelvin offset",
                "instruction": "In temperature.py, fix c_to_k to use the 273.15 absolute-zero offset.",
                "check": check2,
            },
        ]
    }
    intervention = {
        "instruction": "The Fahrenheit offset must be exactly 32. Replace '+ 33' with '+ 32' in c_to_f.",
        "note": "off-by-one on the freezing point (33 instead of 32)",
    }
    return _run_scripted_demo(
        files={
            "temperature.py": _PRO_TEMPERATURE,
            "check_step1.py": _PRO_CHECK1,
            "check_step2.py": _PRO_CHECK2,
        },
        supervisor_script=[json.dumps(plan), json.dumps(intervention)],
        executor_script=[_PRO_EXEC_S1_WRONG, _PRO_EXEC_S1_RIGHT, _PRO_EXEC_S2],
        goal="Fix the two bugs in temperature.py so check_step1.py and check_step2.py pass.",
    )


def cmd_demo(args) -> int:
    print(BANNER)
    if getattr(args, "pro", False):
        print("Running the realistic offline demo (no API key, no GPU)...\n")
        return _pro_demo()
    print("Running the offline demo (no API key, no GPU required)...\n")
    return _simple_demo()


# --------------------------------------------------------------------------- #
# run (real backends)
# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    _load_dotenv()

    task_arg = args.task
    goal = Path(task_arg).read_text(encoding="utf-8") if Path(task_arg).is_file() else task_arg

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        raise SystemExit(f"--repo is not a directory: {repo}")

    if args.copy:
        dest = Path(".maestro") / repo.name
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(repo, dest)
        repo = dest.resolve()
        print(f"[workspace] working on a copy: {repo}")

    supervisor = build_agent("supervisor", args.supervisor)
    executor = build_agent("executor", args.executor)
    ledger = Ledger(price_for(args.supervisor), price_for(args.executor))

    print(BANNER)
    print(f"Supervisor: {supervisor.name}   Executor: {executor.name}\n")

    orch = Orchestrator(
        supervisor, executor, Workspace(repo), ledger,
        max_attempts=args.max_attempts, logger=print,
    )
    result = orch.run(goal)
    return _print_result(result, ledger)


# --------------------------------------------------------------------------- #
# race (multiple models in parallel, best-of-N)
# --------------------------------------------------------------------------- #
def cmd_race(args) -> int:
    _load_dotenv()
    task = Path(args.task).read_text(encoding="utf-8") if Path(args.task).is_file() else args.task
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        raise SystemExit("--models must list at least one provider, e.g. claude-cli,ollama")

    print(BANNER)
    print(f"RACE — {len(models)} model(s), same task, best-of-N\n")

    from .race import pick_winner, race

    results = race(models, args.repo, task, args.check, args.max_attempts, logger=print)

    print("\n+---------------------- RACE RESULTS ----------------------+")
    for r in sorted(results, key=lambda r: (not r.passed, r.cost, r.tokens)):
        status = "PASS" if r.passed else (r.reason or ("ERR" if r.error else "fail"))
        note = r.error or r.summary
        print(f"  {status:<8} {r.model:<22} tokens={r.tokens:<7} cost=${r.cost:.4f}  {note[:38]}")
    print("+----------------------------------------------------------+")

    winner = pick_winner(results)
    if winner:
        print(f"\nWINNER: {winner.model}  (cheapest passing, ${winner.cost:.4f})")
        print(f"  fixed copy: {winner.workdir}")
        return 0
    print("\nNo model passed the check.")
    return 1


# --------------------------------------------------------------------------- #
# delegate (offload work to free agents; for orchestrators like Claude/codex)
# --------------------------------------------------------------------------- #
def cmd_delegate(args) -> int:
    _load_dotenv()
    task = Path(args.task).read_text(encoding="utf-8") if Path(args.task).is_file() else args.task
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    from .race import delegate

    res = delegate(
        models, args.repo, task, args.check, args.max_attempts,
        apply=not args.no_apply, logger=(None if args.json else print),
    )

    if args.json:
        print(json.dumps(res))
        return 0 if res["ok"] else 1

    if res["ok"]:
        files = ", ".join(res["applied_files"]) or "(none)"
        print(f"\n✓ delegated to {res['winner']} — check passed; applied {len(res['applied_files'])} file(s): {files}")
        return 0
    print("\n✗ no agent passed the check; nothing applied.")
    for r in res["results"]:
        print(f"  - {r['spec']}: {r['error'] or r['reason']}")
    return 1


# --------------------------------------------------------------------------- #
# serve (web control room)
# --------------------------------------------------------------------------- #
def cmd_serve(args) -> int:
    _load_dotenv()
    print(BANNER)
    from .server import serve

    serve(args.host, args.port, open_browser=not args.no_browser)
    return 0


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    # Model output can contain non-ASCII; keep Windows consoles from crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(prog="maestro", description="Frontier model conducts free local models.")
    parser.add_argument("--version", action="version", version=f"maestro {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_demo = sub.add_parser("demo", help="run the offline, zero-config demo")
    p_demo.add_argument("--pro", action="store_true", help="use the larger, more realistic demo project")

    providers = ["claude-cli", "codex-cli", "ollama", "deepseek", "gemini", "openrouter", "anthropic", "openai"]

    p_run = sub.add_parser("run", help="run on a real task with real backends")
    p_run.add_argument("--task", required=True, help="task description, or path to a .md/.txt file")
    p_run.add_argument("--repo", required=True, help="directory the Executor may edit")
    p_run.add_argument("--supervisor", default="claude-cli", choices=providers)
    p_run.add_argument("--executor", default="ollama", choices=providers)
    p_run.add_argument("--max-attempts", type=int, default=3)
    p_run.add_argument("--copy", action="store_true", help="work on a copy under .maestro/ instead of in place")

    p_race = sub.add_parser("race", help="run several models in parallel on the same task; keep the winner")
    p_race.add_argument("--task", required=True, help="task description, or path to a file")
    p_race.add_argument("--repo", required=True, help="directory to fix (each model gets its own copy)")
    p_race.add_argument("--check", required=True, help="shell command that exits 0 when the task is done")
    p_race.add_argument("--models", required=True, help="comma-separated providers, e.g. claude-cli,ollama,codex-cli")
    p_race.add_argument("--max-attempts", type=int, default=3)

    p_del = sub.add_parser("delegate", help="offload a task to free agents in parallel and apply the winner (for orchestrators)")
    p_del.add_argument("--task", required=True, help="task description, or path to a file")
    p_del.add_argument("--repo", required=True, help="the real directory to fix (winner is applied here)")
    p_del.add_argument("--check", required=True, help="shell command that exits 0 when the task is done")
    p_del.add_argument("--models", default="opencode:opencode/deepseek-v4-flash-free,ollama:gpt-oss:120b-cloud",
                       help="comma-separated free agents to race")
    p_del.add_argument("--max-attempts", type=int, default=2)
    p_del.add_argument("--no-apply", action="store_true", help="don't write changes back to the repo")
    p_del.add_argument("--json", action="store_true", help="compact JSON output (for agents)")

    p_serve = sub.add_parser("serve", help="launch the web control room (dashboard)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--no-browser", action="store_true", help="don't auto-open a browser")

    args = parser.parse_args(argv)
    if args.cmd == "demo":
        return cmd_demo(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "race":
        return cmd_race(args)
    if args.cmd == "delegate":
        return cmd_delegate(args)
    if args.cmd == "serve":
        return cmd_serve(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
