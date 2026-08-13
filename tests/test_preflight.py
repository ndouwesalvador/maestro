import sys

from maestro import registry
from maestro.config import build_agent
from maestro.preflight import baseline, doctor, probe


def _check(script: str, tmp_path) -> str:
    (tmp_path / "chk.py").write_text(script, encoding="utf-8")
    return f'"{sys.executable}" chk.py'


def test_baseline_reports_green_when_the_check_already_passes(tmp_path):
    res = baseline(_check("import sys; sys.exit(0)\n", tmp_path), tmp_path)
    assert res["state"] == "green"


def test_baseline_reports_red_when_there_is_real_work(tmp_path):
    res = baseline(_check("import sys; print('boom'); sys.exit(1)\n", tmp_path), tmp_path)
    assert res["state"] == "red"
    assert "boom" in res["output"]


def test_baseline_flags_a_check_command_that_cannot_run(tmp_path):
    res = baseline("maestro-no-such-command-xyz --run", tmp_path)
    assert res["state"] == "broken"


def test_baseline_flags_an_empty_check(tmp_path):
    assert baseline("", tmp_path)["state"] == "broken"


def test_broken_detection_does_not_depend_on_the_shell_language(tmp_path):
    # A non-English Windows reports an unknown command in its own language and
    # with a plain exit code 1, so neither the message nor the code can be
    # matched — the diagnosis has to come from PATH.
    res = baseline("maestro-definitely-absent --flag", tmp_path)
    assert res["state"] == "broken"
    assert "not found on PATH" in res["detail"]


def test_baseline_flags_a_missing_python_module(tmp_path):
    res = baseline(f'"{sys.executable}" -m maestro_absent_module_xyz', tmp_path)
    assert res["state"] == "broken"


def test_a_failing_check_that_merely_mentions_a_missing_module_is_still_red(tmp_path):
    # A test suite legitimately printing "No module named ..." is doing its job;
    # that must not be mistaken for a check that cannot run.
    script = (
        "import sys\n"
        "print('E   ModuleNotFoundError: No module named \\'widget\\'')\n"
        "print('1 failed')\n"
        "sys.exit(1)\n"
    )
    assert baseline(_check(script, tmp_path), tmp_path)["state"] == "red"


def test_a_compound_shell_command_is_never_guessed_to_be_broken(tmp_path):
    res = baseline(f'cd . && "{sys.executable}" -c "import sys; sys.exit(1)"', tmp_path)
    assert res["state"] == "red"


def test_doctor_covers_every_registered_backend():
    reported = {r["kind"] for r in doctor()}
    assert reported == set(registry.BACKENDS)
    for row in doctor():
        assert isinstance(row["available"], bool)
        assert row["detail"]


def test_probe_explains_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    row = probe("deepseek")
    assert row["available"] is False
    assert "DEEPSEEK_API_KEY" in row["detail"]


def test_probe_accepts_an_api_key_that_is_set(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "abc123")
    assert probe("gemini")["available"] is True


# --- Claude Pro must be selectable, and selectable everywhere ---------------
def test_claude_code_is_registered_as_a_subscription_backend():
    b = registry.get("claude-code")
    assert b.tier == registry.SUBSCRIPTION
    assert b.autonomous is True
    assert b.binary == "claude"


def test_bare_and_friendly_claude_names_resolve():
    assert registry.resolve("claude") == "claude-cli"
    assert registry.resolve("claude-pro") == "claude-cli"
    assert registry.resolve("claude-code-pro") == "claude-code"


def test_claude_code_can_orchestrate_via_its_completion_twin():
    # Picking the autonomous agent where a planner is required must work
    # instead of dying with "Unknown backend" inside a worker thread.
    from maestro.agents.cli_agent import ClaudeCliAgent

    assert registry.completion_kind("claude-code") == "claude-cli"
    assert isinstance(build_agent("supervisor", "claude-code"), ClaudeCliAgent)
