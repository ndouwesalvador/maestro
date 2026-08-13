# Using Maestro to save your tokens — for Claude Code, Codex, or any orchestrator

You are an **expensive orchestrator**. Maestro lets you **delegate verifiable work
to free agents running in parallel**, so you spend ~95% fewer of your own tokens:
you write a short task + a check command, the free agents do all the file reading
and editing, and you read back **one line** of result.

## The rule of thumb

If a task (or sub-task) has an **objective check** — tests pass, build succeeds,
lint is clean, a script exits 0 — **don't do it yourself. Delegate it.**

```bash
python -m maestro delegate --json \
  --task "Add a /health endpoint returning 200 and a test for it" \
  --repo "C:\path\to\project" \
  --check "python -m pytest -q" \
  --models "opencode:opencode/deepseek-v4-flash-free,ollama:gpt-oss:120b-cloud"
```

You get back ONE compact JSON line, e.g.:

```json
{"ok": true, "winner": "ollama:gpt-oss:120b-cloud", "check_passed": true,
 "applied_files": ["app/api.py", "tests/test_health.py"], "tokens": 4120,
 "undo": "20260813-033406-691e", "results": [...]}
```

The winning agent's changes are **already written into `--repo`**. Your token cost
was just the task string + this JSON — not reading files or generating code.

## Three ways a delegation can cost you nothing

Before any model is started, Maestro runs your `--check` once against the real
repo and looks the job up in its cache. Read the `skipped` / `cached` fields
first — they answer the question for free:

| JSON | Meaning | What you should do |
|---|---|---|
| `"skipped": "already-green"` | The check already passes. There was nothing to do. | Move on. Don't re-issue it. |
| `"skipped": "invalid-check"` | The check can't run here (missing command, wrong script name). `detail` says which. | **Fix the check**, then retry. Every agent would have failed on it identically. |
| `"cached": true` | This exact task, on this exact code, was already solved. The patch was replayed and re-verified. | Move on. |

An `invalid-check` reply is the single most valuable one: it turns a wasted
N-agent run into an instant, free correction.

## Driving a complex, multi-step job (the 95% pattern)

1. **Decompose** the job into sub-tasks that each have a check.
2. **Delegate** each one: `maestro delegate --json ...`. Run models in parallel by
   listing several in `--models`; run sub-tasks in parallel by firing multiple
   `delegate` calls at once.
3. **Read only the compact results.** Re-delegate the ones that failed (the JSON
   `results` array tells you why: `failed`, `runaway`, `stalled`, `timeout`).
4. **Step in yourself only** for the parts needing real judgment, or that no agent
   could pass. Then do a final review of the applied diffs.

## When NOT to delegate

- There is no objective check (write one first, or do it yourself).
- The task needs your specific judgment/taste (architecture, security review, API
  design). Delegate the mechanical parts, keep the judgment.

## Spend the cheap tier first — `--mode cascade`

`--mode race` (default) starts every model at once: fastest, but you pay all of
them. `--mode cascade` climbs the price ladder instead — free models first, and
your Claude/Codex subscription only if they fail:

```bash
python -m maestro delegate --json --mode cascade \
  --models "opencode:opencode/deepseek-v4-flash-free,claude-code" ...
```

The reply then tells you what you didn't have to spend:

```json
{"ok": true, "winner": "opencode:...", "escalated": false,
 "unused_tiers": ["subscription"]}
```

Use cascade when a subscription model is in the list. Use race when every model
is free and you only care about wall-clock.

## Undo

Every applied change is snapshotted first. If a winner passed the check but did
the wrong thing, roll it back — no git required:

```bash
python -m maestro undo --id 20260813-033406-691e   # or just: maestro undo
```

## Check what's usable before you plan

```bash
python -m maestro doctor      # add --json for machine-readable output
```

Reports every backend, whether it works **on this machine right now**, and why
not if it doesn't (`ollama serve` not running, CLI missing, API key unset). Cheap
insurance against planning a run around a model that isn't there.

## Notes

- Free, no API key, work from any terminal: `opencode:opencode/...-free`,
  `ollama:gpt-oss:120b-cloud` (Ollama cloud), local `ollama:*`.
- Subscription agents `claude-code` and `codex` edit autonomously but need their
  own terminal login (they can't authenticate from inside another agent's session
  — `doctor` warns you when it detects this).
- Every agent runs in an **isolated copy** under a **watchdog** (stops on
  `runaway` / `stalled` / `timeout`), so a misbehaving agent can't touch your repo
  or hang you. Tune with `MAESTRO_AGENT_TIMEOUT` / `MAESTRO_AGENT_STALL` /
  `MAESTRO_AGENT_MAX_FILES`.
- Only source files are written back — build output, caches and dependencies
  created by your check (`.next/`, `dist/`, `__pycache__`, …) stay in the racer's
  copy. A winner that changed more than `MAESTRO_MAX_APPLY_FILES` (200) files is
  refused entirely and reported as a `warning`.
- Use `--no-apply` to inspect results without writing changes back, and
  `--no-cache` to force a real run.
- `maestro runs` lists past runs; they persist on disk, unlike the dashboard's
  live state.
