# 🎼 Maestro

**One frontier model conducts an orchestra of free local models.**

Maestro pairs an expensive, smart **Supervisor** (Claude, GPT, …) with a free,
local **Executor** (any model via [Ollama](https://ollama.com)). The Supervisor
*plans* and *intervenes only when the Executor diverges*; the Executor does all
the heavy reading and writing. The result: most of the **paid-token cost is
shifted onto the free local model** — and a built-in ledger shows you exactly
how much you saved.

> Think of it as **speculative execution for agents**: the cheap model drafts,
> the expensive model verifies and corrects — but only when something actually
> goes wrong.

```
              ┌─────────────────────────────┐
  GOAL  ───▶  │  SUPERVISOR  (paid, smart)  │   plans • reviews • corrects
              │  sees: tree, diffs, test logs│   ← never sees full files
              └──────────────┬──────────────┘
                             │  PLAN / INTERVENTION   (tiny messages)
                             ▼
              ┌─────────────────────────────┐
              │  EXECUTOR   (free, local)   │   reads files • writes edits
              │  sees: full source code     │   ← does ~90% of the token work
              └──────────────┬──────────────┘
                             │  edits
                             ▼
                   ┌───────────────────┐
                   │  CHECK (tests/lint)│  objective signal — exit 0 = done
                   └─────────┬─────────┘
                      pass ✓ │ fail ✗  → escalate a COMPACT report upward
                             ▼
                   apply & move on / retry
```

## Quickstart — 30 seconds, fully offline

No API key, no GPU. The demo ships a tiny buggy project and two scripted mock
models so you can watch the whole loop (and the token ledger) run:

```bash
git clone https://github.com/your-username/maestro
cd maestro
python -m maestro demo
```

You'll see the local Executor guess wrong, the check fail, the Supervisor step
in with a one-line correction, and the second attempt pass — then a ledger of
the tokens spent on each side.

## Real usage

```bash
pip install -e ".[anthropic]"        # core has zero dependencies; this adds Claude
ollama pull qwen2.5-coder:7b         # your free local Executor
export ANTHROPIC_API_KEY=sk-ant-...  # your paid Supervisor

maestro run \
  --task examples/task.md \
  --repo examples/broken_math \
  --supervisor anthropic \
  --executor ollama \
  --copy                              # work on a copy under .maestro/ first
```

Mix and match any backend for either role (`anthropic`, `ollama`, `openai`).
`openai` means **any** OpenAI-compatible endpoint — LM Studio, vLLM,
llama.cpp, OpenRouter, etc.

## How the savings are measured (honestly)

Maestro never hides the math. The ledger computes:

```
actual_cost   = supervisor_tokens @ supervisor_price
              + executor_tokens   @ executor_price      (0 if local)

baseline_cost = ALL tokens         @ supervisor_price   (a frontier-only agent)

savings       = 1 - actual_cost / baseline_cost
```

So "savings" means **paid-token cost shifted to the free model** — *not* "90%
less compute". Two honest caveats:

- The bundled `demo` is a 20-line toy, so the Executor reads very little and
  savings land around **~50%**.
- On a **real repository**, the Executor repeatedly reads large files that the
  Supervisor never sees, so the share climbs toward **80–95%**. The bigger the
  codebase, the bigger the gap.

Set your real provider prices in [`maestro/config.py`](maestro/config.py).

## The three ideas that make it efficient

Most multi-agent setups *increase* cost because the agents chatter. Maestro
avoids that with:

1. **A structured A2A protocol** — typed messages (`PLAN`, edits,
   `TEST REPORT`, `INTERVENTION`), never free-form chat.
2. **Context compression** — the Supervisor sees the file *tree*, *diffs* and
   *compact failure reports*. It never receives raw file contents. That single
   rule is where the savings come from.
3. **Divergence-triggered supervision** — the Executor runs autonomously while
   the objective check passes; the Supervisor is only invoked to plan and to
   correct failures.

## Project layout

| File | Role |
|------|------|
| [`protocol.py`](maestro/protocol.py)       | Typed agent-to-agent messages + parsers |
| [`agents/`](maestro/agents)                | Pluggable backends (Anthropic, Ollama, OpenAI-compatible, Mock) |
| [`orchestrator.py`](maestro/orchestrator.py)| The plan → execute → verify → escalate loop |
| [`workspace.py`](maestro/workspace.py)     | Sandboxed search/replace edits |
| [`verify.py`](maestro/verify.py)           | Runs check commands, returns compact reports |
| [`ledger.py`](maestro/ledger.py)           | Transparent token accounting |

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Roadmap

- [ ] Confidence-based escalation (Executor can ask for help proactively)
- [ ] Parallel independent steps
- [ ] Git-aware workspace (auto-branch, auto-commit per step)
- [ ] Richer checks (type-checkers, linters, custom validators)
- [ ] Live web dashboard for the token ledger
- [ ] Per-provider price presets

## Related work — credit where it's due

The Supervisor/Executor pattern isn't brand new, and Maestro stands on good
ideas: [Aider](https://aider.chat)'s architect/editor mode, the orchestrator
modes in [Cline](https://github.com/cline/cline) / Roo Code, multi-agent
frameworks like AutoGen, CrewAI and LangGraph, model routing à la RouteLLM, and
*speculative decoding* at the token level.

**Maestro's niche:** a *free local* Executor driven by a *frontier* Supervisor,
a deliberately token-frugal protocol, and a transparent savings ledger — so the
economics are the headline feature, not an afterthought.

## License

[MIT](LICENSE)
