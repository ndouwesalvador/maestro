"""Look before you leap.

Two cheap checks that run BEFORE any model is paid a single token:

  doctor()   — which backends actually work on this machine right now?
  baseline() — is the check command even valid, and is the work already done?

`baseline` is the biggest token saver in Maestro. Racing N agents against a
check that is already green, or against a check with a typo in it, costs a full
N-agent run and can only ever produce nothing. One subprocess call up front
turns both of those into an instant, free answer.
"""

from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

from .registry import BACKENDS, FREE, SUBSCRIPTION, Backend, resolve
from .verify import run_check

# Set by Claude Code for its own child processes. A nested `claude` / `codex`
# launched from inside an agent session cannot reach the subscription (the
# harness brokers that auth), so it 401s. Worth saying out loud instead of
# letting the user debug a mystery failure.
_NESTED = bool(os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_CHILD_SESSION"))


def _probe_ollama(timeout: float = 1.2) -> tuple:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    import json as _json
    import socket
    import urllib.error
    import urllib.parse
    import urllib.request

    # Ask the socket first. A port with nothing behind it should be diagnosed in
    # milliseconds; going straight to urllib can stall for seconds on a filtered
    # port, and the dashboard blocks on this to draw its model list.
    parsed = urllib.parse.urlparse(host if "//" in host else "http://" + host)
    try:
        socket.create_connection((parsed.hostname or "localhost", parsed.port or 11434),
                                 timeout=min(timeout, 0.8)).close()
    except OSError as exc:
        if shutil.which("ollama"):
            return False, (f"ollama is installed but nothing is listening on {host} "
                           f"({exc.strerror or exc}) — start it with `ollama serve`")
        return False, f"{host} unreachable ({exc.strerror or exc}) — install Ollama, or unset OLLAMA_HOST"

    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as r:
            data = _json.loads(r.read().decode("utf-8", "replace"))
        names = [m.get("name", "") for m in data.get("models", [])]
        n = len(names)
        return True, f"{host} — {n} model(s)" + (f": {', '.join(names[:3])}…" if n else "")
    except Exception as exc:
        # Installed but not serving is the common case, and the fix is one
        # command — say so instead of just reporting a connection error.
        why = getattr(exc, "reason", None) or type(exc).__name__
        if shutil.which("ollama"):
            return False, (f"ollama is installed but nothing is listening on {host} "
                           f"({why}) — start it with `ollama serve`")
        return False, f"{host} unreachable ({why}) — install Ollama, or unset OLLAMA_HOST"


def probe(kind: str) -> Dict:
    """Report whether one backend is usable, and why not if it isn't."""
    kind = resolve(kind)
    b: Backend = BACKENDS.get(kind)
    if b is None:
        return {"kind": kind, "label": kind, "tier": "?", "autonomous": False,
                "available": False, "detail": "unknown backend", "warn": ""}

    available, detail, warn = True, "ready", ""

    if b.binary:
        path = shutil.which(b.binary)
        if path:
            detail = f"`{b.binary}` found"
            # Only the subscription CLIs are affected: Claude Code brokers their
            # auth, so a nested copy gets a 401. Free gateways sign in on their own.
            if _NESTED and b.tier == SUBSCRIPTION:
                warn = (f"`{b.binary}` is installed, but this Maestro is running inside a "
                        "Claude Code session — a nested CLI cannot reach your subscription "
                        "(401). Launch Maestro.exe or `maestro serve` from your own terminal.")
        else:
            available = False
            detail = f"`{b.binary}` is not on PATH — install its CLI and sign in once"
    elif b.kind == "ollama":
        available, detail = _probe_ollama()
    elif b.env_key:
        val = (os.environ.get(b.env_key) or "").strip()
        if val and val != "not-needed":
            detail = f"{b.env_key} is set"
        else:
            available = False
            detail = f"{b.env_key} is not set"

    return {"kind": b.kind, "label": b.label, "tier": b.tier, "autonomous": b.autonomous,
            "can_plan": b.can_plan, "spec": b.spec, "note": b.note,
            "available": available, "detail": detail, "warn": warn}


_CACHE: Dict[str, object] = {"at": 0.0, "rows": None}


def doctor(max_age: float = 10.0) -> List[Dict]:
    """Probe every backend. Free and subscription tiers first — that is the
    order Maestro will spend them in.

    Probes run concurrently and the result is briefly cached: a single
    unreachable host would otherwise make the dashboard's model list sit on
    "detecting…" for as long as its socket takes to give up.
    """
    now = time.monotonic()
    rows = _CACHE.get("rows")
    if rows is not None and now - float(_CACHE["at"]) < max_age:
        return rows  # type: ignore[return-value]

    with ThreadPoolExecutor(max_workers=min(8, len(BACKENDS))) as pool:
        rows = list(pool.map(probe, list(BACKENDS)))
    order = {FREE: 0, SUBSCRIPTION: 1}
    rows.sort(key=lambda r: (order.get(r["tier"], 2), not r["available"], r["kind"]))
    _CACHE.update({"at": now, "rows": rows})
    return rows


def usable_specs() -> List[str]:
    """Ready-to-use --models entries for everything installed right now."""
    return [r["spec"] for r in doctor() if r["available"]]


# --------------------------------------------------------------------------- #
# baseline — is this job worth running at all?
# --------------------------------------------------------------------------- #
# Messages emitted by the *tool* rather than the OS shell. Unlike "command not
# found", these are not localised, so matching them is safe on any machine.
_BROKEN_MARKERS = (
    "no module named",
    "can't open file",
    "missing script:",
    "command not found",
    "is not recognized as the name of a cmdlet",
)

# `shutil.which` cannot see shell builtins, so asking it about these would
# wrongly declare a perfectly good check broken.
_SHELL_BUILTINS = {
    "echo", "cd", "set", "call", "exit", "rem", "if", "for", "cls", "type",
    "dir", "copy", "del", "move", "ren", "mkdir", "rmdir", "start", "pushd",
    "popd", "true", "false", "test", "export", "source", "eval", "unset", ":",
}

_SHELL_OPERATORS = ("&&", "||", "|", ";", ">", "<", "$(", "`", "&")


def _first_token(command: str) -> str:
    import shlex

    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    return parts[0].strip('"').strip("'") if parts else ""


def _executable_exists(command: str, repo: Path):
    """Can the check's program even be found? True / False / None (can't tell).

    This is the locale-independent half of the diagnosis. A shell reports an
    unknown command in the user's own language and, on Windows, with a plain
    exit code 1 — neither is safe to pattern-match, but asking PATH is.
    """
    if any(op in command for op in _SHELL_OPERATORS):
        return None  # a compound line can do anything; don't guess
    token = _first_token(command)
    if not token or token.lower() in _SHELL_BUILTINS:
        return None
    if shutil.which(token):
        return True
    candidate = Path(token)
    if candidate.exists() or (Path(repo) / token).exists():
        return True
    return False


def baseline(check: str, repo: str, timeout: int = 120) -> Dict:
    """Run `check` against the real repo before any agent is started.

    Returns a dict with `state`:
      green   — already passing; there is nothing to delegate (0 tokens)
      broken  — the check itself is invalid (missing command/script); every
                agent would fail identically, so fail fast and say why
      red     — the normal case: real work to do. `output` is reused as the
                failure context and to pick which files agents actually need.
    """
    if not str(check or "").strip():
        return {"state": "broken", "detail": "no check command given", "output": "",
                "returncode": None}

    report = run_check(check, Path(repo), "baseline", timeout=timeout)
    output = f"{report.stdout_tail}\n{report.stderr_tail}".strip()

    if report.passed:
        return {"state": "green", "detail": "the check already passes",
                "output": output, "returncode": report.returncode}

    # A check that cannot run will fail identically for every agent, so racing
    # it can only waste a full N-agent run. Three independent signals, most
    # reliable first.
    missing_exe = _executable_exists(check, Path(repo)) is False
    low = output.lower()
    # Marker text only counts when the command produced no real output — a test
    # suite is allowed to *print* "No module named" while working perfectly.
    tool_missing = (any(m in low for m in _BROKEN_MARKERS)
                    and not report.stdout_tail.strip())
    # 127 = POSIX "command not found"; 9009 = one of the cmd.exe equivalents.
    bad_code = report.returncode in (127, 9009)

    if missing_exe or tool_missing or bad_code:
        why = (f"`{_first_token(check)}` was not found on PATH" if missing_exe
               else (output[:200] or "no output"))
        return {"state": "broken",
                "detail": f"the check command cannot run here: {why}",
                "output": output, "returncode": report.returncode}

    return {"state": "red", "detail": "check fails — work to delegate",
            "output": output, "returncode": report.returncode}
