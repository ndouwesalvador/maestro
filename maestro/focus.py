"""Send the agent the files it needs — not the first twelve in the directory.

A failing check already names the files that matter: a traceback, a `tsc`
diagnostic and an eslint line all carry the path of the offending file. Reading
that output costs nothing (we ran the check anyway), and it replaces a blind
"here are 12 whole files" context with a precise one.

On a repo of any size this is the difference between a ~12k-token prompt per
attempt per racer and a ~1k-token one.
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence

# Extensions worth pulling into a prompt as source context.
CODE_EXT = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".swift",
    ".kt", ".scala", ".sh", ".sql", ".json", ".toml", ".yaml", ".yml", ".md",
    ".css", ".scss", ".less", ".vue", ".svelte", ".cfg", ".ini", ".txt",
}

# Anything shaped like a path with an extension. Deliberately loose — every
# candidate is then confirmed against the real file list, so false positives
# cost nothing.
_CANDIDATE = re.compile(r"[A-Za-z0-9_.\-/\\]+\.[A-Za-z0-9]{1,6}")


def _norm(p: str) -> str:
    return p.replace("\\", "/").lstrip("./").lower()


def mentioned_files(output: str, repo_files: Sequence[str], limit: int = 8) -> List[str]:
    """Repo files named in a check's output, most-referenced first.

    `repo_files` are workspace-relative posix paths. Matching accepts an exact
    relative path, a suffix of one (absolute paths in tracebacks), or a unique
    basename.
    """
    if not output or not repo_files:
        return []

    by_norm: Dict[str, str] = {_norm(f): f for f in repo_files}
    by_base: Dict[str, List[str]] = {}
    for f in repo_files:
        by_base.setdefault(f.rsplit("/", 1)[-1].lower(), []).append(f)

    hits: Dict[str, int] = {}
    first: Dict[str, int] = {}
    for m in _CANDIDATE.finditer(output):
        raw = m.group(0)
        if "." + raw.rsplit(".", 1)[-1].lower() not in CODE_EXT:
            continue
        cand = _norm(raw)
        match = by_norm.get(cand)
        if match is None:
            # An absolute path from a traceback: match on the tail.
            tail = [v for k, v in by_norm.items() if k.endswith("/" + cand) or cand.endswith("/" + k)]
            if len(tail) == 1:
                match = tail[0]
        if match is None:
            same = by_base.get(cand.rsplit("/", 1)[-1], [])
            if len(same) == 1:
                match = same[0]
        if match is None:
            continue
        hits[match] = hits.get(match, 0) + 1
        first.setdefault(match, m.start())

    ranked = sorted(hits, key=lambda f: (-hits[f], first[f]))
    return ranked[:limit]


def savings(full_chars: int, focused_chars: int) -> Dict:
    """How much prompt the focused context avoided (~4 chars per token)."""
    saved = max(0, full_chars - focused_chars)
    return {
        "full_tokens": full_chars // 4,
        "focused_tokens": focused_chars // 4,
        "saved_tokens": saved // 4,
        "pct": round(100.0 * saved / full_chars, 1) if full_chars else 0.0,
    }
