"""Backend for any OpenAI-compatible /v1/chat/completions endpoint.

Works with LM Studio, vLLM, llama.cpp's server, OpenRouter, Together, etc.
Can play either role. Standard library only.
"""

from __future__ import annotations

import json
import urllib.request

from .base import Agent, Response, Usage, estimate_tokens


class OpenAICompatAgent(Agent):
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "not-needed",
        role: str = "executor",
        temperature: float = 0.1,
        timeout: int = 600,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.role = role
        self.name = f"openai:{model}"
        self.temperature = temperature
        self.timeout = timeout

    def chat(self, system: str, user: str) -> Response:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        text = data["choices"][0]["message"]["content"]
        usage_obj = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_obj.get("prompt_tokens", estimate_tokens(system + user))),
            output_tokens=int(usage_obj.get("completion_tokens", estimate_tokens(text))),
        )
        return Response(text=text, usage=usage, model=self.model)
