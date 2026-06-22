"""The agent-to-agent (A2A) protocol.

Communication between the Supervisor and the Executor is *structured*, not
free-form chat. Structure is what keeps the expensive Supervisor's token
budget tiny: it exchanges plans, compact failure reports and short
instructions, never whole files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional


# --------------------------------------------------------------------------- #
# Plan / Step
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    """A single unit of work the Executor must complete."""

    id: str
    title: str
    instruction: str          # precise, self-contained order for the Executor
    check: str                # shell command; exit code 0 means "step is done"
    status: str = "pending"   # pending | done | failed


@dataclass
class Plan:
    goal: str
    steps: List[Step] = field(default_factory=list)

    @staticmethod
    def from_text(text: str, goal: str) -> "Plan":
        """Parse the Supervisor's JSON plan into a Plan object."""
        data = _extract_json(text)
        raw_steps = data.get("steps", [])
        if not raw_steps:
            raise ValueError("Supervisor returned a plan with no steps.")
        steps = []
        for i, s in enumerate(raw_steps, start=1):
            steps.append(
                Step(
                    id=str(s.get("id") or f"s{i}"),
                    title=str(s.get("title") or f"Step {i}"),
                    instruction=str(s.get("instruction") or "").strip(),
                    check=str(s.get("check") or "").strip(),
                )
            )
        return Plan(goal=goal, steps=steps)


# --------------------------------------------------------------------------- #
# Edits (SEARCH / REPLACE blocks — the Executor's output format)
# --------------------------------------------------------------------------- #
@dataclass
class Edit:
    """A single search/replace edit targeting one file."""

    path: str
    search: str
    replace: str


_EDIT_RE = re.compile(
    r"^(?P<path>[^\n]+?)\n"
    r"<{5,9} SEARCH\n"
    r"(?P<search>.*?)\n"
    r"={5,9}\n"
    r"(?P<replace>.*?)\n"
    r">{5,9} REPLACE",
    re.DOTALL | re.MULTILINE,
)


def parse_edits(text: str) -> List[Edit]:
    """Extract every SEARCH/REPLACE block from an Executor response."""
    edits: List[Edit] = []
    for m in _EDIT_RE.finditer(text):
        edits.append(
            Edit(
                path=m.group("path").strip().strip("`").strip(),
                search=m.group("search"),
                replace=m.group("replace"),
            )
        )
    return edits


def parse_summary(text: str) -> str:
    """Pull the Executor's one-line SUMMARY, falling back to the first line."""
    for line in text.splitlines():
        if line.strip().upper().startswith("SUMMARY:"):
            return line.split(":", 1)[1].strip()
    first = text.strip().splitlines()
    return first[0].strip() if first else ""


# --------------------------------------------------------------------------- #
# Verification report
# --------------------------------------------------------------------------- #
@dataclass
class VerificationReport:
    step_id: str
    passed: bool
    command: str
    returncode: int
    stdout_tail: str
    stderr_tail: str

    def compact(self, limit: int = 1200) -> str:
        """A token-frugal failure report — this is all the Supervisor ever sees."""
        out = (
            f"command: {self.command}\n"
            f"exit_code: {self.returncode}\n"
            f"stdout:\n{self.stdout_tail}\n"
            f"stderr:\n{self.stderr_tail}\n"
        )
        return out[:limit]


# --------------------------------------------------------------------------- #
# Intervention (the Supervisor's correction after a failure)
# --------------------------------------------------------------------------- #
@dataclass
class Intervention:
    instruction: str
    note: str = ""

    @staticmethod
    def from_text(text: str) -> "Intervention":
        try:
            data = _extract_json(text)
            return Intervention(
                instruction=str(data.get("instruction") or "").strip(),
                note=str(data.get("note") or "").strip(),
            )
        except Exception:
            # If the Supervisor didn't emit clean JSON, treat the whole reply
            # as the corrected instruction.
            return Intervention(instruction=text.strip())


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> dict:
    """Best-effort extraction of a JSON object from a model reply."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n", "", t)
        t = re.sub(r"\n```\s*$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1 and j > i:
        t = t[i : j + 1]
    return json.loads(t)
