"""The workspace: a sandboxed view of the project the Executor edits.

It can read a compact file tree (cheap context for prompts) and apply the
Executor's SEARCH/REPLACE edits, always staying inside the workspace root.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .protocol import Edit

_SKIP_DIRS = {".git", "__pycache__", ".maestro", ".venv", "venv", "node_modules"}
_TEXT_EXT = {".py", ".txt", ".md", ".json", ".toml", ".cfg", ".ini", ".js", ".ts"}


@dataclass
class EditOutcome:
    diff: str
    files_changed: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)  # edits whose SEARCH didn't match

    @property
    def ok(self) -> bool:
        return not self.failed


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)

    # -- reading ----------------------------------------------------------- #
    def files(self, limit: int = 80) -> List[str]:
        out: List[str] = []
        for p in sorted(self.root.rglob("*")):
            if p.is_dir():
                continue
            rel = p.relative_to(self.root)
            # Check ignore list against the workspace-relative parts only — an
            # ancestor directory named like one of these (e.g. a workspace under
            # .maestro/) must not hide the whole tree.
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            out.append(str(rel).replace("\\", "/"))
            if len(out) >= limit:
                break
        return out

    def tree(self, limit: int = 80) -> str:
        return "\n".join(self.files(limit)) or "(empty)"

    def read(self, rel: str) -> str:
        return self._resolve(rel).read_text(encoding="utf-8")

    def context(self, max_chars_per_file: int = 4000, limit: int = 12) -> str:
        """Full contents of text files — the Executor needs this; the
        Supervisor never receives it."""
        chunks: List[str] = []
        for rel in self.files(limit):
            if Path(rel).suffix not in _TEXT_EXT:
                continue
            try:
                body = self.read(rel)
            except (OSError, UnicodeDecodeError):
                continue
            if len(body) > max_chars_per_file:
                body = body[:max_chars_per_file] + "\n... (truncated)"
            chunks.append(f"=== {rel} ===\n{body}")
        return "\n\n".join(chunks)

    # -- writing ----------------------------------------------------------- #
    def apply_edits(self, edits: List[Edit]) -> EditOutcome:
        outcome = EditOutcome(diff="")
        diffs: List[str] = []
        for edit in edits:
            path = self._match_path(edit.path)
            if path is None:
                outcome.failed.append(f"{edit.path}: file not found in workspace")
                continue

            before = path.read_text(encoding="utf-8")
            if edit.search not in before:
                outcome.failed.append(f"{edit.path}: SEARCH text did not match the file")
                continue

            after = before.replace(edit.search, edit.replace, 1)
            if after == before:
                continue
            path.write_text(after, encoding="utf-8")
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            outcome.files_changed.append(rel)
            diffs.append(_unified(before, after, rel))

        outcome.diff = "\n".join(diffs)
        return outcome

    # -- safety ------------------------------------------------------------ #
    def _match_path(self, rel: str):
        """Resolve an edit's path. Falls back to a unique basename match so a
        model that prefixes or shortens the path still hits the right file."""
        rel = rel.strip().strip("`").strip()
        try:
            p = self._resolve(rel)
            if p.is_file():
                return p
        except (ValueError, OSError):
            pass
        base = Path(rel).name
        matches = [f for f in self.files(500) if Path(f).name == base]
        if len(matches) == 1:
            return self._resolve(matches[0])
        return None

    def _resolve(self, rel: str) -> Path:
        rel = rel.strip().lstrip("/").replace("\\", "/")
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"path escapes workspace: {rel}")
        return p


def _unified(before: str, after: str, rel: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
