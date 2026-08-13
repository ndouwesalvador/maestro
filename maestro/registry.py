"""The backend registry — one source of truth for every model Maestro can drive.

`config.py` builds agents from it, `preflight.py` probes what is actually usable
on this machine, and the dashboard renders its chips from it. Adding a backend
in one place makes it appear everywhere, correctly labelled.

Deliberately dependency-free (no other maestro import) so anything can use it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

# How you pay for a backend. This is what drives the cascade ladder: Maestro
# spends the cheap tiers first and only escalates to your subscription if it has
# to, so a Pro plan's quota is the LAST resort instead of the first.
SUBSCRIPTION = "subscription"   # your Claude Pro / ChatGPT plan — no API key
FREE = "free"                   # free cloud or local open-source models
API_KEY = "api-key"             # billed per token, needs a key in the env


@dataclass(frozen=True)
class Backend:
    kind: str
    label: str
    tier: str
    autonomous: bool = False      # edits files itself vs. returns SEARCH/REPLACE
    binary: str = ""              # CLI that must be on PATH
    env_key: str = ""             # API key variable
    default_model: str = ""
    completion_kind: str = ""     # completion-capable twin, for planning roles
    note: str = ""

    @property
    def spec(self) -> str:
        """The string a user would type into --models."""
        return f"{self.kind}:{self.default_model}" if self.default_model else self.kind

    @property
    def can_plan(self) -> bool:
        """Usable as an orchestrator (directly, or via its completion twin)."""
        return (not self.autonomous) or bool(self.completion_kind)


_SUB_NOTE = (
    "Uses your subscription — no API key. Must be launched from a terminal or "
    "app where that CLI is already signed in."
)

BACKENDS: Dict[str, Backend] = {
    b.kind: b
    for b in (
        Backend("claude-code", "Claude Code (Pro/Max) — agent", SUBSCRIPTION,
                autonomous=True, binary="claude", completion_kind="claude-cli",
                note=_SUB_NOTE + " Edits files itself, under the watchdog."),
        Backend("claude-cli", "Claude (Pro/Max) — planner", SUBSCRIPTION,
                binary="claude",
                note=_SUB_NOTE + " Returns edits for Maestro to apply; can orchestrate."),
        Backend("codex", "Codex (ChatGPT plan) — agent", SUBSCRIPTION,
                autonomous=True, binary="codex", completion_kind="codex-cli",
                note=_SUB_NOTE + " Edits files itself, under the watchdog."),
        Backend("codex-cli", "Codex (ChatGPT plan) — planner", SUBSCRIPTION,
                binary="codex",
                note=_SUB_NOTE + " Returns edits for Maestro to apply; can orchestrate."),
        Backend("opencode", "opencode — agent (free models)", FREE,
                autonomous=True, binary="opencode",
                default_model="opencode/deepseek-v4-flash-free",
                note="Free gateway models. Edits files itself, under the watchdog."),
        Backend("ollama", "Ollama — local / free cloud", FREE,
                default_model="gpt-oss:120b-cloud",
                note="Local models, or free cloud open-source ones like gpt-oss."),
        Backend("deepseek", "DeepSeek API", API_KEY, env_key="DEEPSEEK_API_KEY",
                default_model="deepseek-chat", note="Cheap, but billed per token."),
        Backend("gemini", "Gemini API", API_KEY, env_key="GEMINI_API_KEY",
                default_model="gemini-2.0-flash", note="Has a free tier."),
        Backend("openrouter", "OpenRouter", API_KEY, env_key="OPENROUTER_API_KEY",
                default_model="deepseek/deepseek-chat-v3-0324:free",
                note="Gateway; many :free models."),
        Backend("anthropic", "Anthropic API", API_KEY, env_key="ANTHROPIC_API_KEY",
                default_model="claude-opus-4-8",
                note="Pay-per-token. Prefer claude-code / claude-cli on a subscription."),
        Backend("openai", "OpenAI-compatible endpoint", API_KEY, env_key="OPENAI_API_KEY",
                note="Any OpenAI-shaped server (LM Studio, vLLM, ...)."),
    )
}

# Bare names users naturally type. "claude" and "codex" must resolve to
# something real instead of blowing up deep inside a worker thread.
ALIASES: Dict[str, str] = {
    "claude": "claude-cli",
    "claude-pro": "claude-cli",
    "claude-code-pro": "claude-code",
    "chatgpt": "codex-cli",
    "gpt": "codex-cli",
    "local": "ollama",
}


def resolve(kind: str) -> str:
    """Normalise a backend name (lowercase + alias)."""
    kind = (kind or "").strip().lower()
    return ALIASES.get(kind, kind)


def get(kind: str) -> Optional[Backend]:
    return BACKENDS.get(resolve(kind))


def completion_kind(kind: str) -> str:
    """The completion-capable form of `kind`.

    An autonomous agent (claude-code, codex, opencode) cannot be used where a
    plain completion model is required — planning, for instance. Rather than
    rejecting the user's choice we swap in its twin, so picking "claude-code" as
    orchestrator quietly does the right thing via claude-cli.
    """
    b = get(kind)
    if b and b.autonomous and b.completion_kind:
        return b.completion_kind
    return resolve(kind)


def autonomous_kinds() -> set:
    return {k for k, b in BACKENDS.items() if b.autonomous}


def known_kinds() -> List[str]:
    return sorted(BACKENDS)
