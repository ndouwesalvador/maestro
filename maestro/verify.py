"""Objective verification: run a step's check command and capture a compact
result. Objective signals (tests, linters, compilers) are cheap and let the
Executor run autonomously — the Supervisor is only pulled in when a check
fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .protocol import VerificationReport


def _tail(text: str, lines: int = 25) -> str:
    rows = (text or "").strip().splitlines()
    return "\n".join(rows[-lines:])


def run_check(
    command: str, cwd: str | Path, step_id: str = "", timeout: int = 120
) -> VerificationReport:
    if not command:
        # No check defined: treat as trivially passed but make it visible.
        return VerificationReport(step_id, True, "(no check)", 0, "", "")

    # Disable bytecode caching for the check: the Executor edits source rapidly,
    # and a same-length edit within the same second can otherwise be masked by a
    # stale .pyc (Python invalidates on source mtime + size). Compiling fresh
    # every time keeps verification honest.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return VerificationReport(
            step_id=step_id,
            passed=proc.returncode == 0,
            command=command,
            returncode=proc.returncode,
            stdout_tail=_tail(proc.stdout),
            stderr_tail=_tail(proc.stderr),
        )
    except subprocess.TimeoutExpired:
        return VerificationReport(
            step_id, False, command, -1, "", f"check timed out after {timeout}s"
        )
