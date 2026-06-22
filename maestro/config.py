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
    "openai": Price(0.0, 0.0),
    "ollama": Price(0.0, 0.0),        # local / cloud open-source = free
    "mock": Price(0.0, 0.0),
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def build_agent(role: str, kind: str, model_override: str = "") -> Agent:
    """Create an Agent for `role` using backend `kind` (optional model override)."""
    kind = kind.lower()

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

    if kind == "claude-cli":
        from .agents.cli_agent import ClaudeCliAgent

        return ClaudeCliAgent(model=model_override or _env("MAESTRO_CLAUDE_MODEL", ""), role=role)

    if kind == "codex-cli":
        from .agents.cli_agent import CodexCliAgent

        return CodexCliAgent(model=model_override or _env("MAESTRO_CODEX_MODEL", ""), role=role)

    raise SystemExit(
        f"Unknown backend '{kind}'. Use: claude-cli | codex-cli | ollama | anthropic | openai."
    )


def price_for(kind: str) -> Price:
    """Per-token price for a provider (illustrative; see PROVIDER_PRICES)."""
    return PROVIDER_PRICES.get(kind.lower(), LOCAL_PRICE)
