"""Adapters that drive local AI coding CLIs (Claude Code, Codex) in headless
mode — using the user's SUBSCRIPTION instead of a per-token API key.

This is the key to mixing "paid" brains (a Claude Pro/Max or ChatGPT/Codex
plan) with free open-source models *without any API key*: we shell out to the
CLI, neutralise its own editing tools so it behaves as a plain text model, and
let Maestro's orchestrator apply and verify the result.

Personal use of your own licensed CLIs. Mind each plan's rate limits.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import List, Tuple

from .base import Agent, Response, Usage, estimate_tokens


def _resolve(name: str) -> List[str]:
    """Return an argv prefix that runs `name`, handling Windows .ps1 shims."""
    path = shutil.which(name) or name
    if path.lower().endswith(".ps1"):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    return [path]


# Tools we forbid so Claude Code answers as a pure model instead of editing
# files itself — Maestro stays in control of applying and checking changes.
# (Only names this Claude version knows; unknown names make the CLI error out.)
_CLAUDE_NO_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit"


class ClaudeCliAgent(Agent):
    """Drives `claude -p` (Claude Code headless) on your Claude subscription."""

    def __init__(self, model: str = "", role: str = "supervisor", timeout: int = 600) -> None:
        self.model = model
        self.role = role
        self.name = "claude-cli" + (f":{model}" if model else "")
        self.timeout = timeout

    def chat(self, system: str, user: str) -> Response:
        cmd = _resolve("claude") + [
            "-p",
            "--output-format", "json",
            "--disallowedTools", _CLAUDE_NO_TOOLS,
            "--append-system-prompt", system,
        ]
        if self.model:
            cmd += ["--model", self.model]
        proc = subprocess.run(
            cmd, input=user, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed ({proc.returncode}): {(proc.stderr or '')[-500:]}")
        text, usage = _parse_claude_json(proc.stdout)
        return Response(text=text, usage=usage, model=self.model or "claude")


def _parse_claude_json(stdout: str) -> Tuple[str, Usage]:
    data = None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        for line in reversed((stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    if not isinstance(data, dict):
        text = (stdout or "").strip()
        return text, Usage(estimate_tokens(text), estimate_tokens(text))
    text = data.get("result") or ""
    u = data.get("usage") or {}
    usage = Usage(int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)))
    if usage.input_tokens == 0 and usage.output_tokens == 0:
        usage = Usage(estimate_tokens(text), estimate_tokens(text))
    return text, usage


class CodexCliAgent(Agent):
    """Experimental: drives `codex exec` (read-only) on your ChatGPT/Codex plan."""

    def __init__(self, model: str = "", role: str = "executor", timeout: int = 600) -> None:
        self.model = model
        self.role = role
        self.name = "codex-cli" + (f":{model}" if model else "")
        self.timeout = timeout

    def chat(self, system: str, user: str) -> Response:
        prompt = f"{system}\n\n{user}"
        cmd = _resolve("codex") + ["exec", "--sandbox", "read-only"]
        if self.model:
            cmd += ["-m", self.model]
        cmd += ["-"]  # read the prompt from stdin
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"codex CLI failed ({proc.returncode}): {(proc.stderr or '')[-500:]}")
        text = (proc.stdout or "").strip()
        return Response(
            text=text,
            usage=Usage(estimate_tokens(prompt), estimate_tokens(text)),
            model=self.model or "codex",
        )
