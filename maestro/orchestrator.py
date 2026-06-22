"""The conductor.

Loop:
    1. Supervisor plans (sees only a compact file tree).
    2. For each step, the Executor attempts it (sees full files) and the
       check runs.
    3. On failure, a *compact* report is escalated to the Supervisor, which
       returns one corrected instruction. Retry.

The Supervisor is invoked O(steps + failures); the Executor does all the
heavy reading and writing. That asymmetry is where the token savings live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .agents.base import Agent
from .ledger import Ledger
from .protocol import (
    Intervention,
    Plan,
    Step,
    VerificationReport,
    parse_edits,
    parse_summary,
)
from .verify import run_check
from .workspace import Workspace

# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
PLAN_SYS = (
    "You are the SUPERVISOR, a senior engineer. To save tokens you NEVER see "
    "full source files — only a compact file tree. Decompose the GOAL into the "
    "smallest number of concrete steps. Each step needs:\n"
    "  - instruction: a precise, self-contained order for a junior developer\n"
    "  - check: a shell command that exits 0 only when the step is complete\n"
    "Keep it to 1-4 steps. EVERY step must edit code and have a real, runnable "
    "shell check. Do NOT add 'explore', 'investigate' or 'review' steps - there "
    "is no human to perform them.\n"
    "Reply with ONLY JSON:\n"
    '{"steps":[{"id":"s1","title":"...","instruction":"...","check":"..."}]}'
)

EXEC_SYS = (
    "You are the EXECUTOR, a local coding model. Carry out the SUPERVISOR's "
    "instruction by editing files. Output ONE OR MORE search/replace blocks and "
    "nothing else, in EXACTLY this format:\n\n"
    "path/to/file.ext\n"
    "<<<<<<< SEARCH\n"
    "<exact existing lines>\n"
    "=======\n"
    "<new lines>\n"
    ">>>>>>> REPLACE\n\n"
    "The SEARCH text must match the shown file byte-for-byte. Only edit files "
    "from the EDITABLE FILES list, using their exact paths; never invent code "
    "that is not shown. Finish with one line:\n"
    "SUMMARY: <one sentence on what you changed>"
)

INTERVENE_SYS = (
    "You are the SUPERVISOR. The EXECUTOR's last attempt FAILED its check. To "
    "save tokens you see only a compact failure report — never the full files. "
    "Diagnose the divergence and give ONE corrected, precise instruction.\n"
    'Reply with ONLY JSON: {"instruction":"...","note":"<short reason>"}'
)


def _plan_user(goal: str, tree: str) -> str:
    return f"GOAL:\n{goal}\n\nFILE TREE:\n{tree}\n"


def _exec_user(step: Step, file_context: str, editable: str) -> str:
    return (
        f"INSTRUCTION:\n{step.instruction}\n\n"
        f"EDITABLE FILES (use these exact paths): {editable}\n\n"
        f"PROJECT FILES:\n{file_context}\n"
    )


def _intervene_user(
    step: Step, report: VerificationReport, last_summary: str, apply_errors: list
) -> str:
    parts = [
        f"STEP: {step.title}",
        f"INSTRUCTION GIVEN: {step.instruction}",
        f"EXECUTOR SAID: {last_summary}",
    ]
    if apply_errors:
        parts.append(
            "EDITS THAT DID NOT APPLY (the Executor's search/replace was wrong):\n"
            + "\n".join(f"  - {e}" for e in apply_errors)
        )
    parts.append(f"FAILURE REPORT:\n{report.compact()}")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class StepOutcome:
    step: Step
    passed: bool
    attempts: int


@dataclass
class RunResult:
    plan: Plan
    outcomes: List[StepOutcome] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return bool(self.outcomes) and all(o.passed for o in self.outcomes)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class Orchestrator:
    def __init__(
        self,
        supervisor: Agent,
        executor: Agent,
        workspace: Workspace,
        ledger: Ledger,
        max_attempts: int = 3,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.supervisor = supervisor
        self.executor = executor
        self.ws = workspace
        self.ledger = ledger
        self.max_attempts = max_attempts
        self.log = logger or (lambda _msg: None)

    def run(self, goal: str) -> RunResult:
        # 1) Plan — Supervisor sees only the compact tree.
        self.log(f"\n[SUPERVISOR] planning: {goal}")
        presp = self.supervisor.chat(PLAN_SYS, _plan_user(goal, self.ws.tree()))
        self.ledger.record(self.supervisor.name, "supervisor", presp.usage, "plan")
        plan = Plan.from_text(presp.text, goal)
        self.log(f"[SUPERVISOR] {len(plan.steps)} step(s) planned.")

        result = RunResult(plan=plan)
        for step in plan.steps:
            outcome = self._run_step(step)
            result.outcomes.append(outcome)
            if not outcome.passed:
                self.log(f"[STOP] step '{step.title}' could not be completed.")
                break
        return result

    def _run_step(self, step: Step) -> StepOutcome:
        self.log(f"\n--- Step {step.id}: {step.title} ---")
        last_summary = ""
        for attempt in range(1, self.max_attempts + 1):
            # 2) Executor attempts the step — it sees the full files.
            eresp = self.executor.chat(
                EXEC_SYS, _exec_user(step, self.ws.context(), ", ".join(self.ws.files()))
            )
            self.ledger.record(
                self.executor.name, "executor", eresp.usage, f"exec:{step.id}"
            )
            last_summary = parse_summary(eresp.text)
            edits = parse_edits(eresp.text)
            outcome = self.ws.apply_edits(edits)
            self.log(
                f"[EXECUTOR] attempt {attempt}: {len(edits)} edit(s), "
                f"{len(outcome.files_changed)} file(s) changed. {last_summary}"
            )
            if outcome.failed:
                self.log(f"           unmatched edits: {outcome.failed}")

            # 3) Objective check.
            report = run_check(step.check, self.ws.root, step.id)
            if report.passed:
                self.log(f"[CHECK] PASS ({step.check})")
                step.status = "done"
                return StepOutcome(step, True, attempt)
            self.log(f"[CHECK] FAIL (exit {report.returncode})")

            if attempt == self.max_attempts:
                break

            # 4) Escalate a COMPACT report to the Supervisor.
            self.log("[SUPERVISOR] intervening on divergence...")
            iresp = self.supervisor.chat(
                INTERVENE_SYS, _intervene_user(step, report, last_summary, outcome.failed)
            )
            self.ledger.record(
                self.supervisor.name, "supervisor", iresp.usage, f"intervene:{step.id}"
            )
            intervention = Intervention.from_text(iresp.text)
            if intervention.instruction:
                step.instruction = intervention.instruction
            self.log(f"[SUPERVISOR] correction: {intervention.note or intervention.instruction[:80]}")

        step.status = "failed"
        return StepOutcome(step, False, self.max_attempts)
