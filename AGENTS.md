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
 "applied_files": ["app/api.py", "tests/test_health.py"], "results": [...]}
```

The winning agent's changes are **already written into `--repo`**. Your token cost
was just the task string + this JSON — not reading files or generating code.

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

## Notes

- Free, no API key, work from any terminal: `opencode:opencode/...-free`,
  `ollama:gpt-oss:120b-cloud` (Ollama cloud), local `ollama:*`.
- Subscription agents `claude-code` and `codex` edit autonomously but need their
  own terminal login (they can't authenticate from inside another agent's session).
- Every agent runs in an **isolated copy** under a **watchdog** (stops on
  `runaway` / `stalled` / `timeout`), so a misbehaving agent can't touch your repo
  or hang you. Tune with `MAESTRO_AGENT_TIMEOUT` / `MAESTRO_AGENT_STALL` /
  `MAESTRO_AGENT_MAX_FILES`.
- Use `--no-apply` to inspect results without writing changes back.
