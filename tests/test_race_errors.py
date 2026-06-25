from maestro.agents.cli_agent import ClaudeCliAgent, CodexCliAgent
from maestro.config import build_agent
from maestro.race import _solo_run


def test_build_agent_aliases_bare_claude_to_claude_cli():
    agent = build_agent("executor", "claude")
    assert isinstance(agent, ClaudeCliAgent)


def test_build_agent_aliases_bare_codex_to_codex_cli():
    agent = build_agent("executor", "codex")
    assert isinstance(agent, CodexCliAgent)


def test_solo_run_unknown_backend_is_a_clean_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.txt").write_text("hello", encoding="utf-8")

    result = _solo_run("not-a-real-provider", repo, "do something", "true", 1)

    assert result.passed is False
    assert result.reason == "error"
    assert "Unknown backend" in result.error
