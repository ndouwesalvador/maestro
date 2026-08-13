"""Auto-orchestrate: an LLM 'tech lead' decomposes a high-level goal into small
checkable sub-tasks, then each sub-task is delegated to free agents in parallel
(best-of-N) and applied on success.

This is the in-app equivalent of the AGENTS.md / CLAUDE.md playbook: instead of
you writing each task+check by hand, an orchestrator model writes them, and the
free agents do the work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional

from .config import build_agent
from .protocol import _extract_json
from .race import _parse_spec, delegate
from .workspace import Workspace

AUTO_PLAN_SYS = (
    "You are a pragmatic tech lead. Break the GOAL into 1-5 INDEPENDENT sub-tasks "
    "that a junior dev could each do alone. EVERY sub-task MUST include a shell "
    "`check` command that exits 0 only when it is done (run tests, build, lint, "
    "typecheck, or a tiny script). Prefer checks that already exist in the repo. "
    "Keep tasks concrete and small. Reply with ONLY JSON:\n"
    '{"steps":[{"title":"short label","task":"precise instruction","check":"shell cmd"}]}'
)


def _plan(orchestrator, goal: str, tree: str) -> List[dict]:
    resp = orchestrator.chat(AUTO_PLAN_SYS, f"GOAL:\n{goal}\n\nFILE TREE:\n{tree}\n")
    try:
        data = _extract_json(resp.text)
    except Exception as exc:
        raise RuntimeError(
            f"orchestrator '{orchestrator.name}' did not return valid JSON ({exc}); "
            f"it replied: {resp.text[:200]!r}"
        ) from exc
    steps = []
    for s in data.get("steps", []):
        if s.get("task") and s.get("check"):
            steps.append({"title": s.get("title") or s["task"][:48],
                          "task": s["task"], "check": s["check"]})
    return steps


def auto_run(
    goal: str,
    repo: str,
    orchestrator_spec: str,
    executor_specs: List[str],
    max_attempts: int = 2,
    on_plan: Optional[Callable[[List[dict]], None]] = None,
    on_step: Optional[Callable[[int, dict], None]] = None,
    logger: Optional[Callable[[str], None]] = None,
    mode: str = "race",
    use_cache: bool = True,
) -> dict:
    log = logger or (lambda _m: None)
    repo_path = Path(repo).resolve()

    # 1) Decompose the goal. The orchestrator must be a completion model;
    #    picking an autonomous agent here resolves to its completion twin
    #    (claude-code -> claude-cli) rather than failing.
    okind, omodel = _parse_spec(orchestrator_spec)
    orchestrator = build_agent("supervisor", okind, omodel)
    tree = Workspace(repo_path).tree(120)
    log(f"[auto] planning with {orchestrator.name} ...")
    steps = _plan(orchestrator, goal, tree)
    if on_plan:
        on_plan(steps)
    log(f"[auto] {len(steps)} sub-task(s) planned")

    # 2) Delegate each sub-task. delegate() runs its own baseline first, so a
    #    step the plan invented but that is already satisfied costs nothing.
    results = []
    for i, s in enumerate(steps):
        if on_step:
            on_step(i, {"status": "running"})
        log(f"[auto] step {i + 1}/{len(steps)}: {s['title']}")
        res = delegate(executor_specs, str(repo_path), s["task"], s["check"],
                       max_attempts, apply=True, logger=logger, mode=mode,
                       use_cache=use_cache)
        info = {
            "status": "done" if res["ok"] else "failed",
            "winner": res["winner"], "applied_files": res["applied_files"],
            "results": res["results"], "skipped": res.get("skipped"),
            "cached": res.get("cached", False), "detail": res.get("detail", ""),
            "tokens": res.get("tokens", 0), "undo": res.get("undo"),
        }
        results.append({**s, "ok": res["ok"], **info})
        if on_step:
            on_step(i, info)

    return {
        "goal": goal,
        "ok": bool(results) and all(r["ok"] for r in results),
        "steps": results,
        "tokens": sum(r.get("tokens", 0) for r in results),
        "free_steps": sum(1 for r in results if r.get("skipped") or r.get("cached")),
    }
