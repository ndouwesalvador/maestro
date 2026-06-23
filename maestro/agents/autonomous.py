"""Autonomous coding-agent backends.

Unlike the completion agents (which return SEARCH/REPLACE text that Maestro
applies), these are full agent CLIs — Claude Code, Codex, opencode — that edit
files themselves inside a given working directory. Maestro hands each one a copy
of the repo and a task, lets it work, then runs the check. This is what lets a
paid subscription agent and a free open-source agent compete on equal footing.
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .base import Usage, estimate_tokens
from .cli_agent import _parse_claude_json, _resolve


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        import signal

        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


def _run_capture(cmd, cwd, timeout):
    """Run a command; if it refuses to exit (some agent CLIs leave a server
    alive), kill its process tree after `timeout` and proceed — the objective
    check decides success anyway. Returns (stdout, timed_out)."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        return out or "", False
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            out, _ = proc.communicate(timeout=15)
        except Exception:
            out = ""
        return out or "", True


@dataclass
class ActResult:
    usage: Usage
    summary: str
    raw: str = ""


class AutonomousAgent(ABC):
    name: str = ""
    role: str = "executor"
    model: str = ""

    @abstractmethod
    def act(self, task: str, workdir) -> ActResult:
        """Edit files under `workdir` to accomplish `task`."""
        raise NotImplementedError


class ClaudeCodeAgent(AutonomousAgent):
    """Claude Code as an autonomous editor (your Claude subscription)."""

    def __init__(self, model: str = "", timeout: int = 900) -> None:
        self.model = model
        self.name = "claude-code" + (f":{model}" if model else "")
        self.timeout = timeout

    def act(self, task: str, workdir) -> ActResult:
        cmd = _resolve("claude") + ["-p", "--output-format", "json", "--dangerously-skip-permissions"]
        if self.model:
            cmd += ["--model", self.model]
        proc = subprocess.run(
            cmd, input=task, cwd=str(workdir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude failed ({proc.returncode}): {(proc.stderr or proc.stdout or '')[-400:]}")
        text, usage = _parse_claude_json(proc.stdout)
        return ActResult(usage, (text or "edited").strip()[:140], proc.stdout)


class CodexAgent(AutonomousAgent):
    """Codex as an autonomous editor (your ChatGPT/Codex subscription)."""

    def __init__(self, model: str = "", timeout: int = 900) -> None:
        self.model = model
        self.name = "codex" + (f":{model}" if model else "")
        self.timeout = timeout

    def act(self, task: str, workdir) -> ActResult:
        cmd = _resolve("codex") + ["exec", "--sandbox", "workspace-write"]
        if self.model:
            cmd += ["-m", self.model]
        cmd += ["-"]  # task from stdin
        proc = subprocess.run(
            cmd, input=task, cwd=str(workdir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"codex failed ({proc.returncode}): {(proc.stderr or '')[-400:]}")
        out = (proc.stdout or "").strip()
        return ActResult(Usage(estimate_tokens(task), estimate_tokens(out)), out[-140:] or "edited", out)


class OpencodeAgent(AutonomousAgent):
    """opencode as an autonomous editor — gateway to free models (DeepSeek, etc.).

    Uses --pure (skip plugins) and a bounded run: opencode can leave a server
    process alive instead of exiting, so if it lingers past the timeout we kill
    the tree and let the check decide whether the edits worked.
    """

    def __init__(self, model: str = "", timeout: int = 240) -> None:
        self.model = model
        self.name = "opencode" + (f":{model}" if model else "")
        self.timeout = timeout

    def act(self, task: str, workdir) -> ActResult:
        cmd = _resolve("opencode") + ["run", "--pure", "--dir", str(workdir)]
        if self.model:
            cmd += ["-m", self.model]
        cmd += [task]
        out, timed_out = _run_capture(cmd, str(workdir), self.timeout)
        out = out.strip()
        note = "edited (opencode lingered; proceeded)" if timed_out else "edited"
        return ActResult(Usage(estimate_tokens(task), estimate_tokens(out)), out[-140:] or note, out)


class MockAutonomousAgent(AutonomousAgent):
    """Writes a fixed set of files into the workdir — for tests and offline demos."""

    def __init__(self, files: dict, name: str = "mock-auto") -> None:
        self._files = files
        self.name = name
        self.model = "mock"

    def act(self, task: str, workdir) -> ActResult:
        wd = Path(workdir)
        for rel, content in self._files.items():
            (wd / rel).write_text(content, encoding="utf-8")
        return ActResult(Usage(estimate_tokens(task), 20), "wrote " + ", ".join(self._files))
