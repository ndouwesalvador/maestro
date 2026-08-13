"""Maestro's on-disk state: what to ignore, what happened, how to undo it, and
what we already know the answer to.

Four things live here because they share one rule — the canonical set of
directories that are *not source*:

  IGNORE_DIRS  the single ignore list (copy, apply, hash all use it)
  journal      every run, persisted, so a finished run is still inspectable
  undo         the previous bytes of anything a winner overwrote
  cache        a content-addressed answer to a delegation we already solved

Keeping one ignore list matters: when the copy list and the apply list drifted
apart, a check that ran `npm run build` inside the racer's copy shipped the
whole of `.next/` back into the user's repo.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

# Not source: version control, dependencies, build output, caches, and
# Maestro's own scratch space. Never copied into a racer's workspace, never
# applied back out of one, never part of a cache key.
IGNORE_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "vendor", ".tox",
    "dist", "build", "out", ".next", ".nuxt", ".svelte-kit", ".turbo",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    ".gradle", "target", "bin", "obj",
    ".maestro", ".opencode", ".idea", ".vscode",
})

MAX_APPLY_FILES = int(os.environ.get("MAESTRO_MAX_APPLY_FILES", "200"))


def is_ignored(rel: str) -> bool:
    return any(part in IGNORE_DIRS for part in Path(rel).parts)


def state_dir() -> Path:
    """Where Maestro keeps its state (override with MAESTRO_HOME)."""
    p = Path(os.environ.get("MAESTRO_HOME") or ".maestro")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sub(name: str) -> Path:
    p = state_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_json(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# run journal — a finished run must stay readable after the dashboard closes
# --------------------------------------------------------------------------- #
def record_run(run: dict) -> str:
    # A second-resolution hash would collide for two runs in the same second.
    rid = str(run.get("id") or uuid.uuid4().hex[:8])
    payload = {**run, "id": rid, "saved_at": _now()}
    try:
        (_sub("runs") / f"{rid}.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass
    return rid


def list_runs(limit: int = 50) -> List[dict]:
    out = []
    for p in sorted(_sub("runs").glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]:
        d = _read_json(p)
        if d:
            out.append({"id": d.get("id"), "mode": d.get("mode"), "saved_at": d.get("saved_at"),
                        "ok": d.get("ok"), "winner": d.get("winner"), "goal": d.get("goal"),
                        "task": (d.get("task") or "")[:120]})
    return out


def get_run(rid: str) -> Optional[dict]:
    return _read_json(_sub("runs") / f"{rid}.json")


# --------------------------------------------------------------------------- #
# undo — never overwrite a user's file without keeping the old bytes
# --------------------------------------------------------------------------- #
def snapshot(repo: Path, rel_paths: List[str], meta: Optional[dict] = None) -> str:
    """Record the CURRENT bytes of `rel_paths` before they are overwritten."""
    repo = Path(repo)
    files = []
    for rel in rel_paths:
        target = repo / rel
        if target.is_file():
            try:
                files.append({"path": rel, "existed": True,
                              "b64": base64.b64encode(target.read_bytes()).decode("ascii")})
            except OSError:
                continue
        else:
            files.append({"path": rel, "existed": False, "b64": ""})

    sid = f"{time.strftime('%Y%m%d-%H%M%S')}-{hashlib.sha1(str(repo).encode()).hexdigest()[:4]}"
    try:
        (_sub("undo") / f"{sid}.json").write_text(
            json.dumps({"id": sid, "repo": str(repo), "created": _now(),
                        "meta": meta or {}, "files": files}, indent=2), encoding="utf-8")
    except OSError:
        pass
    return sid


def list_snapshots(limit: int = 20) -> List[dict]:
    out = []
    for p in sorted(_sub("undo").glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]:
        d = _read_json(p)
        if d:
            out.append({"id": d["id"], "repo": d["repo"], "created": d["created"],
                        "files": len(d.get("files", [])), "meta": d.get("meta", {})})
    return out


def restore(sid: Optional[str] = None) -> dict:
    """Roll back a snapshot (the most recent one by default)."""
    snaps = sorted(_sub("undo").glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if sid:
        snaps = [p for p in snaps if p.stem == sid]
    if not snaps:
        return {"ok": False, "error": "no snapshot to restore"}

    data = _read_json(snaps[0])
    if not data:
        return {"ok": False, "error": f"unreadable snapshot: {snaps[0].name}"}

    repo = Path(data["repo"])
    restored, removed, failed = [], [], []
    for f in data.get("files", []):
        target = repo / f["path"]
        try:
            if f.get("existed"):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(f["b64"]))
                restored.append(f["path"])
            elif target.exists():
                target.unlink()
                removed.append(f["path"])
        except OSError as exc:
            failed.append(f"{f['path']}: {exc}")

    return {"ok": not failed, "id": data["id"], "repo": str(repo),
            "restored": restored, "removed": removed, "failed": failed}


# --------------------------------------------------------------------------- #
# cache — the same delegation, on the same code, has the same answer
# --------------------------------------------------------------------------- #
def _repo_fingerprint(repo: Path, max_files: int = 4000) -> str:
    h = hashlib.sha256()
    for p in sorted(Path(repo).rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(repo).as_posix()
        if is_ignored(rel):
            continue
        try:
            h.update(rel.encode("utf-8"))
            h.update(hashlib.sha256(p.read_bytes()).digest())
        except OSError:
            continue
        max_files -= 1
        if max_files <= 0:
            break
    return h.hexdigest()


def cache_key(repo: Path, task: str, check: str) -> str:
    """Identify a delegation by what it asks and the code it asks it about."""
    h = hashlib.sha256()
    for part in (task.strip(), check.strip(), _repo_fingerprint(Path(repo))):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:32]


def cache_get(key: str) -> Optional[dict]:
    return _read_json(_sub("cache") / f"{key}.json")


def cache_put(key: str, files: Dict[str, bytes], meta: Optional[dict] = None) -> None:
    if not files:
        return
    payload = {
        "key": key, "created": _now(), "meta": meta or {},
        "files": [{"path": rel, "b64": base64.b64encode(data).decode("ascii")}
                  for rel, data in files.items()],
    }
    try:
        (_sub("cache") / f"{key}.json").write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def cache_files(entry: dict) -> Dict[str, bytes]:
    return {f["path"]: base64.b64decode(f["b64"]) for f in entry.get("files", [])}


def cache_discard(key: str) -> None:
    """Drop an entry that replayed but no longer satisfies its check."""
    try:
        (_sub("cache") / f"{key}.json").unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# workspace hygiene — a full source copy per racer per run adds up fast
# --------------------------------------------------------------------------- #
def remove_workdirs(paths: List[str]) -> int:
    """Delete specific racer copies now — used once a run's outcome is applied,
    so a large repo doesn't leave one full copy per racer sitting on disk."""
    removed = 0
    race_dir = (state_dir() / "race").resolve()
    for p in paths:
        if not p:
            continue
        target = Path(p).resolve()
        # Only ever delete inside our own scratch space.
        if race_dir not in target.parents:
            continue
        shutil.rmtree(target, ignore_errors=True)
        removed += 1
    return removed


def purge_workdirs(keep: Optional[List[str]] = None, max_age_hours: float = 12.0) -> int:
    """Delete stale racer copies under .maestro/race, keeping `keep`."""
    keep_resolved = {str(Path(k).resolve()) for k in (keep or [])}
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    race_dir = state_dir() / "race"
    if not race_dir.is_dir():
        return 0
    for child in race_dir.iterdir():
        if not child.is_dir() or str(child.resolve()) in keep_resolved:
            continue
        try:
            if child.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    return removed


def disk_usage() -> dict:
    """How much space Maestro's scratch space is using."""
    out = {}
    for name in ("race", "cache", "undo", "runs"):
        d = state_dir() / name
        total = 0
        if d.is_dir():
            for p in d.rglob("*"):
                try:
                    if p.is_file():
                        total += p.stat().st_size
                except OSError:
                    pass
        out[name] = total
    out["total"] = sum(out.values())
    return out
