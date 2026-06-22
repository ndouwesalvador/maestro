"""Supervisor backend backed by the Anthropic API (Claude).

Requires the optional `anthropic` package:  pip install "maestro-ai[anthropic]"
"""

from __future__ import annotations

import os

from .base import Agent, Response, Usage


class AnthropicAgent(Agent):
    def __init__(
        self,
        model: str = "claude-opus-4-8",
        api_key: str | None = None,
        role: str = "supervisor",
        max_tokens: int = 2000,
    ) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise SystemExit(
                "The Anthropic backend needs the 'anthropic' package.\n"
                '  pip install "maestro-ai[anthropic]"'
            ) from exc

        import anthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY is not set (see .env.example).")

        self._client = anthropic.Anthropic(api_key=key)
        self.model = model
        self.role = role
        self.name = f"anthropic:{model}"
        self.max_tokens = max_tokens

    def chat(self, system: str, user: str) -> Response:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in msg.content if getattr(block, "type", "") == "text"
        )
        usage = Usage(
            input_tokens=int(msg.usage.input_tokens),
            output_tokens=int(msg.usage.output_tokens),
        )
        return Response(text=text, usage=usage, model=self.model)
