"""Best-of-N across several models, and the delegation entry point.

Two ways to spend models on one task:

  race     — everyone runs at once. Fastest wall-clock; you pay every racer.
  cascade  — cheapest tier first, escalate only if it fails. Your Claude Pro
             quota becomes the LAST resort instead of the first one.

Around both sits the part that costs nothing and saves the most: a baseline run
of the check before any model is started (already green? invalid command?), a
content-addressed cache of past answers, and a context built from the check's
own failure output instead of a blind dump of whole files.

Each racer works in its own private copy, so they never collide, and the real
repo is only ever written once — with an undo snapshot taken first.
"""

from __future__ import annotations

import os
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import preflight, registry, store
from .config import AUTONOMOUS_KINDS, build_agent, build_autonomous_agent, price_for
from .focus import mentioned_files
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
    reason: str = ""  # passed | failed | stopped | runaway | stalled | timeout | error | skipped
    spec: str = ""  # the original --models entry, used to key live UI state
    tier: str = ""  # free | subscription | api-key

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _parse_spec(spec: str):
    """Split a racer spec into (provider, model). 'ollama:gpt-oss:120b' ->
    ('ollama', 'gpt-oss:120b'); 'claude-cli' -> ('claude-cli', '')."""
    spec = spec.strip()
    if ":" in spec:
        kind, model = spec.split(":", 1)
        return registry.resolve(kind.strip()), model.strip()
    return registry.resolve(spec), ""


def _tier_of(spec: str) -> str:
    b = registry.get(_parse_spec(spec)[0])
    return b.tier if b else "?"


# --------------------------------------------------------------------------- #
# the two execution loops
# --------------------------------------------------------------------------- #
def _focused_context(ws: Workspace, failure_output: str):
    """Context for the next attempt: the files the check actually complained
    about, falling back to the generic dump when it named none."""
    if failure_output:
        hits = mentioned_files(failure_output, ws.files(400))
        if hits:
            return ws.context(only=hits), hits
    return ws.context(), []


def _completion_loop(agent, ws, task, check, max_attempts, cancel=None, progress=None,
                     failure_output=""):
    """Agent returns SEARCH/REPLACE text; Maestro applies it, then checks."""
    in_tok = out_tok = attempt = 0
    summary = ""
    instruction = task
    for attempt in range(1, max_attempts + 1):
        if cancel and cancel():
            return False, "stopped", attempt - 1, in_tok, out_tok, summary
        if progress:
            progress({"status": "running", "attempts": attempt})
        context, focused = _focused_context(ws, failure_output)
        if progress and focused:
            progress({"focus": focused})
        step = Step(id="race", title="task", instruction=instruction, check=check)
        resp = agent.chat(EXEC_SYS, _exec_user(step, context, ", ".join(ws.files())))
        in_tok += resp.usage.input_tokens
        out_tok += resp.usage.output_tokens
        summary = parse_summary(resp.text) or summary
        ws.apply_edits(parse_edits(resp.text))
        report = run_check(check, ws.root, "race")
        if report.passed:
            return True, "passed", attempt, in_tok, out_tok, summary
        # Each failure sharpens the next prompt AND narrows the next context.
        failure_output = report.compact()
        instruction = (
            f"{task}\n\nYour previous attempt FAILED the check:\n"
            f"{failure_output}\nFix the code so the check passes."
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


# --------------------------------------------------------------------------- #
# isolated workspaces
# --------------------------------------------------------------------------- #
# One list, used to copy in, to apply out, and to fingerprint. See store.py —
# when the copy list and the apply list drifted apart, `npm run build` inside a
# racer's copy shipped the whole of .next/ back into the user's repo.
_COPY_IGNORE = tuple(sorted(store.IGNORE_DIRS))
_APPLY_IGNORE = store.IGNORE_DIRS


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


def _solo_run(spec, repo, task, check, max_attempts, cancel=None, on_event=None,
              failure_output=""):
    """One model attempts the task on its own copy, self-correcting on failure."""
    kind, model_override = _parse_spec(spec)
    safe = spec.replace(":", "_").replace("/", "_")
    # Unique per-run dir so a stale lock from a previous agent can never block us.
    workdir = store.state_dir() / "race" / f"{safe}-{uuid.uuid4().hex[:6]}"
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
                agent, ws, task, check, max_attempts, cancel, progress, failure_output
            )

        cost = in_tok / 1e6 * price.input_per_mtok + out_tok / 1e6 * price.output_per_mtok
        return RacerResult(agent.name, passed, attempt, in_tok, out_tok, cost,
                           summary, str(ws.root), reason=reason, spec=spec,
                           tier=_tier_of(spec))
    except (Exception, SystemExit) as exc:  # one bad racer must not kill the whole race
        return RacerResult(spec, False, 0, 0, 0, 0.0, error=str(exc)[:300],
                           workdir=str(workdir), reason="error", spec=spec,
                           tier=_tier_of(spec))


# --------------------------------------------------------------------------- #
# race / cascade
# --------------------------------------------------------------------------- #
def _parallel_race(models, repo_path, task, check, max_attempts, log, on_result,
                   cancel, on_event, failure_output) -> List[RacerResult]:
    results: List[RacerResult] = []
    with ThreadPoolExecutor(max_workers=max(1, len(models))) as pool:
        futures = {
            pool.submit(
                _solo_run, m, repo_path, task, check, max_attempts,
                (lambda s=m: bool(cancel(s))) if cancel else None, on_event,
                failure_output,
            ): m
            for m in models
        }
        for fut in as_completed(futures):
            r = fut.result()
            if r.error:
                log(f"  [{r.model}] ERROR: {r.error}")
            else:
                log(f"  [{r.model}] {r.reason}  attempts={r.attempts}  "
                    f"tokens={r.tokens}  cost=${r.cost:.4f}")
            if on_result:
                on_result(r)
            results.append(r)
    return results


def waves(models: List[str]) -> List[List[str]]:
    """Group models into escalation tiers, cheapest first.

    Free models cost 0 and go first; then metered APIs by price; then the
    subscription CLIs, whose price here is the API-equivalent value of the
    tokens they'd otherwise burn. That ordering is the whole point of cascade.
    """
    groups: Dict[float, List[str]] = {}
    for m in models:
        p = price_for(_parse_spec(m)[0])
        groups.setdefault(p.input_per_mtok + p.output_per_mtok, []).append(m)
    return [groups[k] for k in sorted(groups)]


def _cascade(models, repo_path, task, check, max_attempts, log, on_result, cancel,
             on_event, failure_output) -> List[RacerResult]:
    tiers = waves(models)
    log(f"Cascade — {len(tiers)} tier(s), cheapest first; stopping at the first that passes\n")
    if on_event:
        for tier in tiers[1:]:
            for m in tier:
                on_event(m, {"status": "queued", "reason": "queued"})

    results: List[RacerResult] = []
    started: set = set()
    for i, tier in enumerate(tiers, start=1):
        log(f"  tier {i}/{len(tiers)} [{_tier_of(tier[0])}]: {', '.join(tier)}")
        started.update(tier)
        got = _parallel_race(tier, repo_path, task, check, max_attempts, log,
                             on_result, cancel, on_event, failure_output)
        results.extend(got)
        if any(r.passed for r in got):
            log(f"  tier {i} passed — remaining tier(s) never spent")
            break

    for m in models:  # everything the cascade never had to reach
        if m not in started:
            r = RacerResult(m, False, 0, 0, 0, 0.0, reason="skipped", spec=m,
                            tier=_tier_of(m),
                            summary="not needed — a cheaper tier already passed")
            results.append(r)
            if on_result:
                on_result(r)
    return results


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
    mode: str = "race",
    failure_output: str = "",
) -> List[RacerResult]:
    log = logger or (lambda _m: None)
    repo_path = Path(repo).resolve()
    store.purge_workdirs()  # reap copies left by earlier runs

    if mode == "cascade":
        return _cascade(models, repo_path, task, check, max_attempts, log,
                        on_result, cancel, on_event, failure_output)

    log(f"Racing {len(models)} model(s) in parallel: {', '.join(models)}\n")
    return _parallel_race(models, repo_path, task, check, max_attempts, log,
                          on_result, cancel, on_event, failure_output)


def pick_winner(results: List[RacerResult]) -> Optional[RacerResult]:
    """Cheapest result that passed (ties broken by fewer attempts, fewer tokens)."""
    winners = [r for r in results if r.passed]
    if not winners:
        return None
    winners.sort(key=lambda r: (r.cost, r.attempts, r.tokens))
    return winners[0]


# --------------------------------------------------------------------------- #
# applying a winner back to the real repo
# --------------------------------------------------------------------------- #
def _diff_files(src: Path, dst: Path) -> Dict[str, bytes]:
    """Files in the winner's copy whose bytes differ from the real repo.

    Build output, caches and dependencies are excluded here, not just on the way
    in: a check like `npm run build` legitimately creates thousands of files
    inside the racer's copy, and none of them belong in the user's repo.
    """
    out: Dict[str, bytes] = {}
    for p in src.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(src).as_posix()
        if store.is_ignored(rel):
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        target = dst / rel
        try:
            if target.exists() and target.read_bytes() == data:
                continue
        except OSError:
            pass
        out[rel] = data
    return out


def _write_files(files: Dict[str, bytes], dst: Path) -> List[str]:
    written: List[str] = []
    for rel, data in files.items():
        target = dst / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written.append(rel)
        except OSError:
            continue
    return sorted(written)


def _apply_changed(src: Path, dst: Path) -> List[str]:
    """Copy the files the winning agent changed back into the real repo."""
    return _write_files(_diff_files(Path(src), Path(dst)), Path(dst))


# --------------------------------------------------------------------------- #
# delegate — the token-frugal entry point orchestrators call
# --------------------------------------------------------------------------- #
def _summarise(results: List[RacerResult]) -> List[dict]:
    return [
        {"model": r.model, "spec": r.spec, "reason": r.reason, "passed": r.passed,
         "attempts": r.attempts, "tokens": r.tokens, "cost": round(r.cost, 5),
         "tier": r.tier, "error": r.error}
        for r in results
    ]


def delegate(models, repo, task, check, max_attempts=2, apply=True, logger=None,
             on_event=None, mode="race", use_cache=True, use_baseline=True,
             cancel=None, on_result=None) -> dict:
    """Offload `task` to agents in parallel; apply the cheapest passing result
    back to the real repo. Returns a COMPACT dict so an orchestrator can
    delegate work and read the outcome with almost no tokens.

    Two things happen before any model is spent, and either can end the job for
    free: the check is run once against the real repo (already green, or plain
    invalid?), and the (task, check, code) triple is looked up in the cache.
    """
    repo_path = Path(repo).resolve()
    log = logger or (lambda _m: None)
    base_result = {"ok": False, "winner": None, "check_passed": False,
                   "applied_files": [], "results": [], "tokens": 0, "cost": 0.0,
                   "mode": mode, "undo": None}
    failure_output = ""

    # 1) Baseline: is this job worth running at all? Costs one subprocess.
    if use_baseline:
        base = preflight.baseline(check, repo_path)
        if base["state"] == "green":
            log(f"[baseline] {base['detail']} — no model was started")
            return {**base_result, "ok": True, "check_passed": True,
                    "skipped": "already-green", "detail": base["detail"]}
        if base["state"] == "broken":
            log(f"[baseline] {base['detail']}")
            return {**base_result, "skipped": "invalid-check", "detail": base["detail"]}
        failure_output = base["output"]

    # 2) Cache: have we already solved exactly this, on exactly this code?
    key = ""
    if use_cache:
        try:
            key = store.cache_key(repo_path, task, check)
        except Exception:
            key = ""
    if key and apply:
        entry = store.cache_get(key)
        if entry:
            files = store.cache_files(entry)
            undo = store.snapshot(repo_path, list(files), {"source": "cache", "task": task[:200]})
            written = _write_files(files, repo_path)
            if run_check(check, repo_path, "cache").passed:
                log(f"[cache] replayed a known-good answer ({len(written)} file(s)) — 0 tokens")
                return {**base_result, "ok": True, "check_passed": True, "cached": True,
                        "winner": (entry.get("meta") or {}).get("winner"),
                        "applied_files": written, "undo": undo,
                        "detail": "replayed from cache; no model was started"}
            # Stale entry: the code moved under it. Undo and do it for real.
            store.restore(undo)
            store.cache_discard(key)
            log("[cache] stale entry discarded")

    # 3) Actually spend models.
    results = race(models, str(repo_path), task, check, max_attempts, logger=logger,
                   on_event=on_event, mode=mode, failure_output=failure_output,
                   cancel=cancel, on_result=on_result)
    winner = pick_winner(results)

    applied: List[str] = []
    undo_id = None
    warning = ""
    if winner and apply and winner.workdir:
        changed = _diff_files(Path(winner.workdir), repo_path)
        if len(changed) > store.MAX_APPLY_FILES:
            warning = (f"{winner.spec} changed {len(changed)} files (limit "
                       f"{store.MAX_APPLY_FILES}) — nothing was applied. Raise "
                       "MAESTRO_MAX_APPLY_FILES if this is expected.")
            log(f"[apply] {warning}")
        else:
            undo_id = store.snapshot(repo_path, list(changed),
                                     {"winner": winner.spec, "task": task[:200]})
            applied = _write_files(changed, repo_path)
            if key:
                store.cache_put(key, changed, {"winner": winner.spec, "check": check})
            # The answer now lives in the repo (and in the cache), so the racer
            # copies are dead weight — on a real project that is one full copy
            # of the source per model. Losers are kept when nothing was applied,
            # because then they are the only evidence of what went wrong.
            store.remove_workdirs([r.workdir for r in results])

    out = {
        "ok": bool(winner), "winner": winner.spec if winner else None,
        "check_passed": bool(winner), "applied_files": applied,
        "results": _summarise(results), "mode": mode, "undo": undo_id,
        "tokens": sum(r.tokens for r in results),
        "cost": round(sum(r.cost for r in results), 5),
    }
    if warning:
        out["warning"] = warning
    if mode == "cascade":
        unused = sorted({r.tier for r in results if r.reason == "skipped"})
        out["escalated"] = len({r.tier for r in results if r.reason != "skipped"}) > 1
        if unused:
            out["unused_tiers"] = unused
    return out
