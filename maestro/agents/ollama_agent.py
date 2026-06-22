"""Executor backend backed by a local Ollama server (https://ollama.com).

Uses only the standard library (urllib) so Maestro's core stays dependency
free. Token usage is read straight from Ollama's response counters.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import Agent, Response, Usage


class OllamaAgent(Agent):
    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        role: str = "executor",
        temperature: float = 0.1,
        timeout: int = 600,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.role = role
        self.name = f"ollama:{model}"
        self.temperature = temperature
        self.timeout = timeout

    def chat(self, system: str, user: str) -> Response:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": self.temperature},
        }
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"Ollama HTTP {exc.code} for model '{self.model}': {detail}"
            ) from None

        text = data.get("message", {}).get("content", "")
        usage = Usage(
            input_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
        )
        return Response(text=text, usage=usage, model=self.model)
