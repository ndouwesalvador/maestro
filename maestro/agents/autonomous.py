"""Autonomous coding-agent backends + a watchdog that stops them when they
"go off the rails".

Unlike completion agents (which return SEARCH/REPLACE text Maestro applies),
these are full agent CLIs — Claude Code, Codex, opencode — that edit files
themselves. That power needs guardrails: `run_supervised` launches the agent in
an isolated copy and watches it live, killing the whole process tree the moment
it:
  - passes the check        -> "passed"  (done, stop early)
  - finishes on its own     -> "exited"
  - edits too many files     -> "runaway" (rampaging off-task)
  - goes quiet after editing -> "stalled" (looping / talking, not progressing)
  - exceeds the time budget  -> "timeout"
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..verify import run_check
from .base import Usage, estimate_tokens
from .cli_agent import _parse_claude_json, _resolve

_IGNORE = {".git", "__pycache__", ".pytest_cache", ".maestro", "node_modules", ".opencode"}


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


def _snapshot(root: Path) -> dict:
    snap = {}
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(root)
        if any(part in _IGNORE for part in rel.parts):
            continue
        try:
            st = p.stat()
            snap[str(rel)] = (st.st_mtime, st.st_size)
        except OSError:
            pass
    return snap


def _changed(base: dict, snap: dict) -> int:
    keys = set(base) | set(snap)
    return sum(1 for k in keys if base.get(k) != snap.get(k))


@dataclass
class SupervisedResult:
    reason: str          # passed | exited | runaway | stalled | timeout
    output: str = ""
    changed_files: int = 0


def run_supervised(
    cmd,
    cwd,
    stdin_text: str = "",
    check: str = "",
    timeout=None,
    stall=None,
    max_files=None,
    poll: float = 4.0,
) -> SupervisedResult:
    """Run an agent CLI under a watchdog. Always returns (and always leaves no
    process behind), stopping on the first trigger above."""
    timeout = timeout or int(os.environ.get("MAESTRO_AGENT_TIMEOUT", "240"))
    stall = stall or int(os.environ.get("MAESTRO_AGENT_STALL", "60"))
    max_files = max_files or int(os.environ.get("MAESTRO_AGENT_MAX_FILES", "40"))

    cwd = Path(cwd)
    base = _snapshot(cwd)
    out_path = Path(tempfile.mkstemp(suffix=".maestro-out")[1])
    reason = "exited"
    try:
        with open(out_path, "w", encoding="utf-8", errors="replace") as fo:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd),
                stdin=(subprocess.PIPE if stdin_text else None),
                stdout=fo, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            )
            if stdin_text:
                try:
                    proc.stdin.write(stdin_text)
                    proc.stdin.close()
                except Exception:
                    pass

            start = last_change = time.time()
            seen_change = False
            last_snap = base
            while True:
                if check and run_check(check, cwd).passed:
                    reason = "passed"
                    break
                if proc.poll() is not None:
                    reason = "exited"
                    break
                snap = _snapshot(cwd)
                if _changed(base, snap) > max_files:
                    reason = "runaway"
                    break
                if snap != last_snap:
                    last_snap, last_change, seen_change = snap, time.time(), True
                now = time.time()
                if now - start > timeout:
                    reason = "timeout"
                    break
                if seen_change and now - last_change > stall:
                    reason = "stalled"
                    break
                time.sleep(poll)

            changed = _changed(base, _snapshot(cwd))
            if proc.poll() is None:
                _kill_tree(proc.pid)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        output = out_path.read_text(encoding="utf-8", errors="replace")
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass
    return SupervisedResult(reason, output, changed)


@dataclass
class ActResult:
    usage: Usage
    summary: str
    raw: str = ""
    reason: str = ""


class AutonomousAgent(ABC):
    name: str = ""
    role: str = "executor"
    model: str = ""

    @abstractmethod
    def act(self, task: str, workdir, check: str = "") -> ActResult:
        """Edit files under `workdir` to accomplish `task`, under the watchdog."""
        raise NotImplementedError


class ClaudeCodeAgent(AutonomousAgent):
    """Claude Code as an autonomous editor (your Claude subscription)."""

    def __init__(self, model: str = "") -> None:
        self.model = model
        self.name = "claude-code" + (f":{model}" if model else "")

    def act(self, task: str, workdir, check: str = "") -> ActResult:
        cmd = _resolve("claude") + ["-p", "--output-format", "json", "--dangerously-skip-permissions"]
        if self.model:
            cmd += ["--model", self.model]
        res = run_supervised(cmd, workdir, stdin_text=task, check=check)
        text, usage = _parse_claude_json(res.output)
        return ActResult(usage, (text or "").strip()[:120] or res.reason, res.output, res.reason)


class CodexAgent(AutonomousAgent):
    """Codex as an autonomous editor (your ChatGPT/Codex subscription)."""

    def __init__(self, model: str = "") -> None:
        self.model = model
        self.name = "codex" + (f":{model}" if model else "")

    def act(self, task: str, workdir, check: str = "") -> ActResult:
        cmd = _resolve("codex") + ["exec", "--sandbox", "workspace-write"]
        if self.model:
            cmd += ["-m", self.model]
        cmd += ["-"]
        res = run_supervised(cmd, workdir, stdin_text=task, check=check)
        return ActResult(
            Usage(estimate_tokens(task), estimate_tokens(res.output)),
            (res.output.strip()[-120:] or res.reason), res.output, res.reason,
        )


class OpencodeAgent(AutonomousAgent):
    """opencode as an autonomous editor — gateway to free models (DeepSeek, etc.)."""

    def __init__(self, model: str = "") -> None:
        self.model = model
        self.name = "opencode" + (f":{model}" if model else "")

    def act(self, task: str, workdir, check: str = "") -> ActResult:
        cmd = _resolve("opencode") + ["run", "--dir", str(workdir)]
        if self.model:
            cmd += ["-m", self.model]
        cmd += [task]
        res = run_supervised(cmd, workdir, check=check)
        return ActResult(Usage(estimate_tokens(task), 50), res.reason, res.output, res.reason)


class MockAutonomousAgent(AutonomousAgent):
    """Writes a fixed set of files into the workdir — for tests and offline demos."""

    def __init__(self, files: dict, name: str = "mock-auto") -> None:
        self._files = files
        self.name = name
        self.model = "mock"

    def act(self, task: str, workdir, check: str = "") -> ActResult:
        wd = Path(workdir)
        for rel, content in self._files.items():
            (wd / rel).write_text(content, encoding="utf-8")
        return ActResult(Usage(estimate_tokens(task), 20), "wrote " + ", ".join(self._files), reason="exited")
