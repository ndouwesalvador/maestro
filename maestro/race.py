"""Parallel best-of-N: race several models on the SAME task at once, then keep
the cheapest one that actually passed the check.

Each racer works on its own private copy of the repo, so they never collide.
This is the "run several models in parallel" interface — mix subscription
brains (claude-cli, codex-cli) with free open-source ones (ollama) and let the
objective check decide the winner.
"""

from __future__ import annotations

import os
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .config import AUTONOMOUS_KINDS, build_agent, build_autonomous_agent, price_for
from .orchestrator import EXEC_SYS, _exec_user
from .protocol import Step, parse_edits, parse_summary
from .verify import run_check
from .workspace import Workspace


@dataclass
class RacerResult:
    model: str
    passed: bool
    attempts: int
    input_tokens: int
    output_tokens: int
    cost: float
    summary: str = ""
    workdir: str = ""
    error: str = ""
    reason: str = ""  # passed | failed | stopped | runaway | stalled | timeout | error
    spec: str = ""  # the original --models entry, used to key live UI state

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _parse_spec(spec: str):
    """Split a racer spec into (provider, model). 'ollama:gpt-oss:120b' ->
    ('ollama', 'gpt-oss:120b'); 'claude-cli' -> ('claude-cli', '')."""
    spec = spec.strip()
    if ":" in spec:
        kind, model = spec.split(":", 1)
        return kind.strip().lower(), model.strip()
    return spec.lower(), ""


def _completion_loop(agent, ws, task, check, max_attempts, cancel=None, progress=None):
    """Agent returns SEARCH/REPLACE text; Maestro applies it, then checks."""
    in_tok = out_tok = attempt = 0
    summary = ""
    instruction = task
    for attempt in range(1, max_attempts + 1):
        if cancel and cancel():
            return False, "stopped", attempt - 1, in_tok, out_tok, summary
        if progress:
            progress({"status": "running", "attempts": attempt})
        step = Step(id="race", title="task", instruction=instruction, check=check)
        resp = agent.chat(EXEC_SYS, _exec_user(step, ws.context(), ", ".join(ws.files())))
        in_tok += resp.usage.input_tokens
        out_tok += resp.usage.output_tokens
        summary = parse_summary(resp.text) or summary
        ws.apply_edits(parse_edits(resp.text))
        report = run_check(check, ws.root, "race")
        if report.passed:
            return True, "passed", attempt, in_tok, out_tok, summary
        instruction = (
            f"{task}\n\nYour previous attempt FAILED the check:\n"
            f"{report.compact()}\nFix the code so the check passes."
        )
    return False, "failed", attempt, in_tok, out_tok, summary


def _autonomous_loop(agent, ws, task, check, max_attempts, cancel=None, progress=None):
    """Agent edits files in its copy itself, under the watchdog; we run the check."""
    in_tok = out_tok = attempt = 0
    summary = ""
    reason = "failed"
    instruction = f"{task}\n\nWhen you are done, this command must exit 0: {check}"
    for attempt in range(1, max_attempts + 1):
        if cancel and cancel():
            return False, "stopped", attempt - 1, in_tok, out_tok, summary
        if progress:
            progress({"status": "running", "attempts": attempt})
        res = agent.act(instruction, ws.root, check, cancel)
        in_tok += res.usage.input_tokens
        out_tok += res.usage.output_tokens
        summary = res.summary or summary
        reason = res.reason or reason
        if run_check(check, ws.root, "race").passed:
            return True, "passed", attempt, in_tok, out_tok, summary
        # If the watchdog cut the agent off, don't keep retrying a misbehaving agent.
        if reason in ("stopped", "runaway", "stalled", "timeout"):
            return False, reason, attempt, in_tok, out_tok, summary
        instruction = (
            f"{task}\n\nYour previous attempt FAILED:\n{run_check(check, ws.root, 'race').compact()}\n"
            f"Fix the code so `{check}` exits 0."
        )
    return False, "failed", attempt, in_tok, out_tok, summary


_COPY_IGNORE = ("node_modules", ".next", ".git", ".maestro", "dist", "build",
                ".turbo", ".cache", "__pycache__", ".venv", "venv", ".pytest_cache")


def _provision_deps(repo: Path, workdir: Path) -> None:
    """Make heavy dependency dirs (node_modules) available in the copy without a
    full data copy: hardlink them (same volume) so checks like `tsc` work, the
    copy stays cheap, and deleting the copy NEVER touches the real deps. Falls
    back to a real copy if hardlinking isn't possible."""
    src = repo / "node_modules"
    if not src.is_dir():
        return
    dst = workdir / "node_modules"
    try:
        shutil.copytree(src, dst, copy_function=os.link, dirs_exist_ok=True)
    except Exception:
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        except Exception:
            pass


def _solo_run(spec, repo, task, check, max_attempts, cancel=None, on_event=None):
    """One model attempts the task on its own copy, self-correcting on failure."""
    kind, model_override = _parse_spec(spec)
    safe = spec.replace(":", "_").replace("/", "_")
    # Unique per-run dir so a stale lock from a previous agent can never block us.
    workdir = Path(".maestro") / "race" / f"{safe}-{uuid.uuid4().hex[:6]}"
    progress = (lambda info: on_event(spec, info)) if on_event else None
    try:
        workdir.parent.mkdir(parents=True, exist_ok=True)
        # Copy the SOURCE only (isolated — edits never touch the real repo);
        # heavy deps are hardlinked separately so the copy stays fast.
        shutil.copytree(repo, workdir, ignore=shutil.ignore_patterns(*_COPY_IGNORE))
        _provision_deps(repo, workdir)
        ws = Workspace(workdir)
        price = price_for(kind)

        if kind in AUTONOMOUS_KINDS:
            agent = build_autonomous_agent(kind, model_override)
            passed, reason, attempt, in_tok, out_tok, summary = _autonomous_loop(
                agent, ws, task, check, max_attempts, cancel, progress
            )
        else:
            agent = build_agent("executor", kind, model_override)
            passed, reason, attempt, in_tok, out_tok, summary = _completion_loop(
                agent, ws, task, check, max_attempts, cancel, progress
            )

        cost = in_tok / 1e6 * price.input_per_mtok + out_tok / 1e6 * price.output_per_mtok
        return RacerResult(agent.name, passed, attempt, in_tok, out_tok, cost,
                           summary, str(ws.root), reason=reason, spec=spec)
    except (Exception, SystemExit) as exc:  # one bad racer must not kill the whole race
        return RacerResult(spec, False, 0, 0, 0, 0.0, error=str(exc)[:300],
                           workdir=str(workdir), reason="error", spec=spec)


def race(
    models: List[str],
    repo: str,
    task: str,
    check: str,
    max_attempts: int = 3,
    logger: Optional[Callable[[str], None]] = None,
    on_result: Optional[Callable[[RacerResult], None]] = None,
    cancel: Optional[Callable[[str], bool]] = None,
    on_event: Optional[Callable[[str, dict], None]] = None,
) -> List[RacerResult]:
    log = logger or (lambda _m: None)
    repo_path = Path(repo).resolve()
    results: List[RacerResult] = []

    log(f"Racing {len(models)} model(s) in parallel: {', '.join(models)}\n")
    with ThreadPoolExecutor(max_workers=max(1, len(models))) as pool:
        futures = {
            pool.submit(
                _solo_run, m, repo_path, task, check, max_attempts,
                (lambda s=m: bool(cancel(s))) if cancel else None, on_event,
            ): m
            for m in models
        }
        for fut in as_completed(futures):
            r = fut.result()
            if r.error:
                log(f"  [{r.model}] ERROR: {r.error}")
            else:
                log(f"  [{r.model}] {r.reason}  attempts={r.attempts}  tokens={r.tokens}  cost=${r.cost:.4f}")
            if on_result:
                on_result(r)
            results.append(r)
    return results


def pick_winner(results: List[RacerResult]) -> Optional[RacerResult]:
    """Cheapest result that passed (ties broken by fewer attempts, fewer tokens)."""
    winners = [r for r in results if r.passed]
    if not winners:
        return None
    winners.sort(key=lambda r: (r.cost, r.attempts, r.tokens))
    return winners[0]


_APPLY_IGNORE = {".git", "__pycache__", ".pytest_cache", ".maestro", "node_modules", ".opencode"}


def _apply_changed(src: Path, dst: Path) -> List[str]:
    """Copy the files the winning agent changed (in its isolated copy) back into
    the real repo. Returns the list of applied relative paths."""
    changed: List[str] = []
    for p in src.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(src)
        if any(part in _APPLY_IGNORE for part in rel.parts):
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        target = dst / rel
        if (not target.exists()) or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            changed.append(str(rel).replace("\\", "/"))
    return changed


def delegate(models, repo, task, check, max_attempts=2, apply=True, logger=None, on_event=None) -> dict:
    """Offload `task` to free agents in parallel; apply the cheapest passing result
    back to the real repo. Returns a COMPACT dict so an orchestrator (Claude/codex)
    can delegate work and read the outcome with almost no tokens."""
    repo_path = Path(repo).resolve()
    results = race(models, str(repo_path), task, check, max_attempts, logger=logger, on_event=on_event)
    winner = pick_winner(results)
    applied: List[str] = []
    if winner and apply and winner.workdir:
        applied = _apply_changed(Path(winner.workdir), repo_path)
    return {
        "ok": bool(winner),
        "winner": winner.spec if winner else None,
        "check_passed": bool(winner),
        "applied_files": applied,
        "results": [
            {"model": r.model, "spec": r.spec, "reason": r.reason, "passed": r.passed,
             "attempts": r.attempts, "tokens": r.tokens, "cost": round(r.cost, 5), "error": r.error}
            for r in results
        ],
    }
