"""Configuration: model pricing and the backend factory.

Edit PRICES to match your provider. They only affect the ledger's dollar
figures — the token counts are always measured directly.
"""

from __future__ import annotations

import os

from .agents.base import Agent
from .ledger import Price

# Illustrative prices (USD per 1M tokens). Edit to match your provider.
SUPERVISOR_PRICE = Price(input_per_mtok=5.0, output_per_mtok=25.0)
LOCAL_PRICE = Price(input_per_mtok=0.0, output_per_mtok=0.0)  # local = free

# Per-provider prices. For subscription CLIs the figure is the API-equivalent
# value (what those tokens *would* cost on the API), so the ledger still shows
# what your subscription + free models saved you versus paying per token.
PROVIDER_PRICES = {
    "anthropic": Price(5.0, 25.0),
    "claude-cli": Price(5.0, 25.0),   # Claude subscription (API-equivalent value)
    "codex-cli": Price(2.5, 10.0),    # ChatGPT/Codex subscription (API-equivalent)
    "claude-code": Price(5.0, 25.0),  # Claude Code autonomous agent (subscription)
    "codex": Price(2.5, 10.0),        # Codex autonomous agent (subscription)
    "opencode": Price(0.0, 0.0),      # opencode autonomous agent (free models)
    "deepseek": Price(0.3, 1.2),      # DeepSeek API (cheap)
    "gemini": Price(0.0, 0.0),        # Gemini free tier
    "openrouter": Price(0.0, 0.0),    # OpenRouter free open-source models
    "openai": Price(0.0, 0.0),
    "ollama": Price(0.0, 0.0),        # local / cloud open-source = free
    "mock": Price(0.0, 0.0),
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


_KIND_ALIASES = {"claude": "claude-cli", "codex": "codex-cli"}


def build_agent(role: str, kind: str, model_override: str = "") -> Agent:
    """Create an Agent for `role` using backend `kind` (optional model override)."""
    kind = kind.lower()
    kind = _KIND_ALIASES.get(kind, kind)  # bare "claude"/"codex" -> the completion CLI

    if kind == "mock":
        raise SystemExit("The 'mock' backend is only available via `maestro demo`.")

    if kind == "anthropic":
        from .agents.anthropic_agent import AnthropicAgent

        model = model_override or _env(
            "MAESTRO_SUPERVISOR_MODEL" if role == "supervisor" else "MAESTRO_EXECUTOR_MODEL",
            "claude-opus-4-8",
        )
        return AnthropicAgent(model=model, role=role)

    if kind == "ollama":
        from .agents.ollama_agent import OllamaAgent

        model = model_override or _env(
            "MAESTRO_EXECUTOR_MODEL" if role == "executor" else "MAESTRO_SUPERVISOR_MODEL",
            "qwen2.5-coder:7b",
        )
        return OllamaAgent(model=model, host=_env("OLLAMA_HOST", "http://localhost:11434"), role=role)

    if kind == "openai":
        from .agents.openai_compat import OpenAICompatAgent

        model = model_override or _env(
            "MAESTRO_SUPERVISOR_MODEL" if role == "supervisor" else "MAESTRO_EXECUTOR_MODEL",
            "local-model",
        )
        return OpenAICompatAgent(
            model=model,
            base_url=_env("OPENAI_BASE_URL", "http://localhost:1234/v1"),
            api_key=_env("OPENAI_API_KEY", "not-needed"),
            role=role,
        )

    # DeepSeek, Gemini and OpenRouter are all OpenAI-compatible endpoints.
    if kind in ("deepseek", "gemini", "openrouter"):
        from .agents.openai_compat import OpenAICompatAgent

        presets = {
            "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"),
            "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai",
                       "GEMINI_API_KEY", "gemini-2.0-flash"),
            "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                           "deepseek/deepseek-chat-v3-0324:free"),
        }
        base_url, key_env, default_model = presets[kind]
        env_model = _env(f"MAESTRO_{kind.upper()}_MODEL", default_model)
        return OpenAICompatAgent(
            model=model_override or env_model,
            base_url=base_url,
            api_key=_env(key_env, "not-needed"),
            role=role,
        )

    if kind == "claude-cli":
        from .agents.cli_agent import ClaudeCliAgent

        return ClaudeCliAgent(model=model_override or _env("MAESTRO_CLAUDE_MODEL", ""), role=role)

    if kind == "codex-cli":
        from .agents.cli_agent import CodexCliAgent

        return CodexCliAgent(model=model_override or _env("MAESTRO_CODEX_MODEL", ""), role=role)

    raise SystemExit(
        f"Unknown backend '{kind}'. Use: claude-cli | codex-cli | ollama | "
        "deepseek | gemini | openrouter | anthropic | openai."
    )


def price_for(kind: str) -> Price:
    """Per-token price for a provider (illustrative; see PROVIDER_PRICES)."""
    return PROVIDER_PRICES.get(kind.lower(), LOCAL_PRICE)


# Providers that EDIT files themselves (full agent CLIs), vs completion backends
# that return text Maestro applies.
AUTONOMOUS_KINDS = {"claude-code", "codex", "opencode"}


def build_autonomous_agent(kind: str, model_override: str = ""):
    """Create an autonomous coding-agent backend (edits files in a directory)."""
    kind = kind.lower()
    if kind == "claude-code":
        from .agents.autonomous import ClaudeCodeAgent

        return ClaudeCodeAgent(model=model_override or _env("MAESTRO_CLAUDE_MODEL", ""))
    if kind == "codex":
        from .agents.autonomous import CodexAgent

        return CodexAgent(model=model_override or _env("MAESTRO_CODEX_MODEL", ""))
    if kind == "opencode":
        from .agents.autonomous import OpencodeAgent

        return OpencodeAgent(model=model_override or _env("MAESTRO_OPENCODE_MODEL", ""))
    raise SystemExit(f"Unknown autonomous backend '{kind}'. Use: claude-code | codex | opencode.")
