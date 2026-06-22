"""A deterministic, offline agent driven by a fixed script.

Used by `maestro demo` and the test-suite so the whole orchestration loop can
run with zero setup (no API key, no GPU). The mock does not "think" — it
replays canned replies — but it still reports realistic token usage based on
the *actual* prompts it receives, so the ledger reflects the real
architectural asymmetry (the Executor reads files; the Supervisor does not).
"""

from __future__ import annotations

from typing import List

from .base import Agent, Response, Usage, estimate_tokens


class MockAgent(Agent):
    def __init__(self, role: str, script: List[str], name: str = "mock") -> None:
        self.role = role
        self.name = name
        self.model = "mock"
        self._script = list(script)
        self._i = 0

    def chat(self, system: str, user: str) -> Response:
        if self._i < len(self._script):
            text = self._script[self._i]
        else:
            text = "{}"
        self._i += 1
        usage = Usage(
            input_tokens=estimate_tokens(system) + estimate_tokens(user),
            output_tokens=estimate_tokens(text),
        )
        return Response(text=text, usage=usage, model=self.model)
