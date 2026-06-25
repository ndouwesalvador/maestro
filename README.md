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
git clone https://github.com/ndouwesalvador/maestro
cd maestro
python -m maestro demo
```

You'll see the local Executor guess wrong, the check fail, the Supervisor step
in with a one-line correction, and the second attempt pass — then a ledger of
the tokens spent on each side.

## Real usage

```bash
# Option A — use your SUBSCRIPTIONS, no API key (recommended):
#   just have `claude` and/or `codex` installed and logged in.
maestro run --task examples/task.md --repo examples/broken_math \
  --supervisor claude-cli --executor ollama --copy

# Option B — API keys (Anthropic, or any OpenAI-compatible: DeepSeek, OpenRouter…):
pip install -e ".[anthropic]"
export ANTHROPIC_API_KEY=sk-ant-...
maestro run --task examples/task.md --repo examples/broken_math \
  --supervisor anthropic --executor ollama --copy
```

## Proof — a real, verified run

Maestro fixing a real bug with **real models**: a 120B cloud model
(`gpt-oss:120b`) as Supervisor directing a small local model (`qwen3.5`) as
Executor. The Supervisor planned one step, the local model edited the file, and
`pytest` went green on the first attempt:

```text
Supervisor: ollama:gpt-oss:120b-cloud   Executor: ollama:qwen3.5:latest

[SUPERVISOR] planning: Make the failing unit tests pass.
[SUPERVISOR] 1 step(s) planned.

--- Step s1: Fix math_utils implementations ---
[EXECUTOR] attempt 1: 1 edit(s), 1 file(s) changed.
[CHECK] PASS (python -m pytest -q)

>> PAID-TOKEN SAVINGS: 45.3%
RESULT: SUCCESS (1/1 steps passed)
```

The paid Supervisor processed ~1k tokens of planning; the free local Executor
did the file reading and editing. On larger, multi-file tasks the Executor's
share — and the savings — grow.

## Multi-provider: paid subscriptions + free open-source

Maestro talks to models through swappable backends — and crucially it can use
your **paid subscriptions without any API key** by driving the official CLIs
headless:

| Backend | Access | API key? |
|---|---|---|
| `claude-cli` | your Claude Pro/Max plan (drives `claude -p`) | ✅ none — subscription |
| `codex-cli` | your ChatGPT/Codex plan (drives `codex exec`) | ✅ none — subscription |
| `ollama` | local models **or** Ollama cloud open-source (`gpt-oss`, `glm`) | ✅ none — free |
| `deepseek` | DeepSeek API (`deepseek-chat` / `reasoner`) | cheap key |
| `gemini` | Google Gemini (OpenAI-compatible endpoint) | free-tier key |
| `openrouter` | OpenRouter — many **free** open-source models | free key |
| `anthropic` / `openai` | Anthropic API, or any OpenAI-compatible endpoint (vLLM, LM Studio…) | needs key |

So you can pair a **paid brain** with a **free open-source worker** and burn far
less of your paid quota:

```bash
maestro run --task task.md --repo ./project --supervisor claude-cli --executor ollama
```

> The subscription CLIs authenticate from **your own terminal**. They can't be
> driven from *inside* another Claude Code session (the nested process can't see
> the brokered login), but a normal terminal works.

## Run several models in parallel — `race` (best-of-N)

Race multiple models on the **same task** at once. Each works on its own private
copy, and the cheapest one that **passes the check** wins:

```bash
maestro race \
  --task "Fix the failing tests" \
  --repo ./examples/broken_math \
  --check "python -m pytest -q" \
  --models "claude-cli,ollama:gpt-oss:120b-cloud,ollama:llama3"
```

Real output — two open-source models (one cloud, one local) racing in parallel:

```text
Racing 2 model(s) in parallel: ollama:gpt-oss:120b-cloud, ollama:llama3:latest
  [ollama:gpt-oss:120b-cloud] PASS  attempts=1  tokens=752   cost=$0.0000
  [ollama:llama3:latest]      PASS  attempts=3  tokens=1981  cost=$0.0000
WINNER: ollama:gpt-oss:120b-cloud  (cheapest passing, $0.0000)
```

A failing or unauthorized model never breaks the race — it's reported and the
others carry on.

**Two kinds of racers can compete side by side:**

- **Completion** (`ollama`, `deepseek`, `gemini`, `openrouter`, …) — the model
  returns edits and Maestro applies them.
- **Autonomous agents** (`claude-code`, `codex`, `opencode`) — the full agent CLI
  edits its own copy of the repo directly. This lets a **paid subscription agent**
  and a **free open-source agent** race head-to-head:

```bash
maestro race --task "Fix the failing tests" --repo ./examples/broken_math \
  --check "python -m pytest -q" \
  --models "claude-code,opencode:opencode/deepseek-v4-flash-free"
```

*Verified: `opencode` driving a **free** DeepSeek model fixed the bug
autonomously and `pytest` went green — at $0.*

### Guardrails — stop an agent when it goes off the rails

Autonomous agents are powerful, so they run under a **watchdog** that watches the
workspace live and kills the whole process tree the moment the agent:

- ✅ **passes the check** — done, stop early (no waiting for slow agents to exit)
- 🛑 **`runaway`** — edits too many files (rampaging off-task)
- 🛑 **`stalled`** — goes quiet after editing (looping / talking, not progressing)
- 🛑 **`timeout`** — exceeds the time budget

Tunable via `MAESTRO_AGENT_TIMEOUT`, `MAESTRO_AGENT_STALL`, `MAESTRO_AGENT_MAX_FILES`.
And since each racer works on an **isolated copy**, a misbehaving agent can never
touch your real repo. The stop reason is reported per racer.

## Delegate from your AI agent — save ~95% of your tokens

The whole point: if you drive an **expensive** agent (Claude Code, Codex), have it
**delegate verifiable work to free agents** instead of doing it itself. One cheap
command does the work and applies the winner back to your repo:

```bash
python -m maestro delegate --json \
  --task "Fix the failing tests" \
  --repo ./my-project \
  --check "python -m pytest -q" \
  --models "opencode:opencode/deepseek-v4-flash-free,ollama:gpt-oss:120b-cloud"
# -> {"ok": true, "winner": "...", "check_passed": true, "applied_files": ["..."], ...}
```

The orchestrator spends tokens only on the task string and the one-line JSON — the
free agents do all the file reading and editing, in parallel, under the watchdog.
See **[AGENTS.md](AGENTS.md)** for the full playbook an orchestrator should follow.

## Web control room — `maestro serve`

Prefer a UI? `maestro serve` opens a local dashboard (zero dependencies — just the
standard library) where you configure a race and **watch every model work live**:
status, attempts, tokens and cost updating in real time, with the winner
highlighted.

```bash
maestro serve            # opens http://127.0.0.1:8765
```

Pick the working folder with a **built-in file browser** (no path typing),
**Stop** any model mid-run with one click, and watch each racer's live badge —
including the watchdog reason (`runaway` / `stalled` / `timeout`).

### Desktop app (double-click `.exe`)

Build a standalone Windows executable that opens the control room on launch (no
Python needed on the target machine):

```bash
pip install pyinstaller
pyinstaller --onefile --name Maestro app.py
# -> dist/Maestro.exe   (double-click; the dashboard opens in your browser)
```

## How the savings are measured (honestly)

Maestro never hides the math. The ledger computes:

```
actual_cost   = supervisor_tokens @ supervisor_price
              + executor_tokens   @ executor_price      (0 if local)

baseline_cost = ALL tokens         @ supervisor_price   (a frontier-only agent)

savings       = 1 - actual_cost / baseline_cost
```

So "savings" means **paid-token cost shifted to the free model** — *not* "90%
less compute". The exact figure depends on the task:

- Tiny jobs save less: a one-function fix where the Supervisor writes a verbose
  plan can land around **45%** (the live run above).
- The realistic `demo --pro` (a ~150-line module, two supervised steps) lands
  around **~78%**.
- On a **large, multi-file repo** where the Executor repeatedly reads big files
  the Supervisor never sees, the share climbs toward **80–95%**. The bigger the
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
