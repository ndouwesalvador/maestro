"""The delegate path: what it refuses to spend models on, what it applies back,
and what it can undo."""

import shutil
import sys

import pytest

from maestro import race as race_mod
from maestro import store
from maestro.race import RacerResult, delegate


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "state"))


@pytest.fixture
def repo(tmp_path):
    """A repo whose check passes only once a.py says `v = 2`."""
    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.py").write_text("v = 1\n", encoding="utf-8")
    (r / "check.py").write_text(
        "import pathlib, sys\n"
        "sys.exit(0 if 'v = 2' in pathlib.Path('a.py').read_text() else 1)\n",
        encoding="utf-8",
    )
    return r


CHECK = f'"{sys.executable}" check.py'


def _never_race(*_a, **_k):
    raise AssertionError("a model was started when it should not have been")


def _winner_fixing(repo, tmp_path, extra_junk=True) -> RacerResult:
    """A racer copy that fixed a.py — and, like a real build, left artifacts."""
    wd = tmp_path / "winner"
    shutil.copytree(repo, wd)
    (wd / "a.py").write_text("v = 2\n", encoding="utf-8")
    if extra_junk:
        (wd / "dist").mkdir()
        (wd / "dist" / "bundle.js").write_text("build output", encoding="utf-8")
        (wd / "node_modules").mkdir()
        (wd / "node_modules" / "dep.js").write_text("dependency", encoding="utf-8")
    return RacerResult("fake", True, 1, 10, 5, 0.0, workdir=str(wd),
                       reason="passed", spec="fake", tier="free")


# --- refusing to spend ------------------------------------------------------
def test_already_green_costs_nothing_and_starts_no_model(repo, monkeypatch):
    (repo / "a.py").write_text("v = 2\n", encoding="utf-8")  # already done
    monkeypatch.setattr(race_mod, "race", _never_race)

    res = delegate(["ollama"], str(repo), "set v to 2", CHECK)

    assert res["ok"] is True
    assert res["skipped"] == "already-green"
    assert res["tokens"] == 0
    assert res["applied_files"] == []


def test_an_invalid_check_fails_fast_instead_of_racing(repo, monkeypatch):
    monkeypatch.setattr(race_mod, "race", _never_race)

    res = delegate(["ollama"], str(repo), "do something",
                   "maestro-no-such-command-xyz")

    assert res["ok"] is False
    assert res["skipped"] == "invalid-check"
    assert res["tokens"] == 0


# --- applying a winner ------------------------------------------------------
def test_applies_only_source_files_never_build_output(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(race_mod, "race", lambda *a, **k: [_winner_fixing(repo, tmp_path)])

    res = delegate(["fake"], str(repo), "set v to 2", CHECK)

    assert res["ok"] is True
    assert res["applied_files"] == ["a.py"]
    assert (repo / "a.py").read_text(encoding="utf-8") == "v = 2\n"
    assert not (repo / "dist").exists()
    assert not (repo / "node_modules").exists()


def test_the_applied_change_can_be_undone(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(race_mod, "race", lambda *a, **k: [_winner_fixing(repo, tmp_path)])

    res = delegate(["fake"], str(repo), "set v to 2", CHECK)
    assert res["undo"]

    store.restore(res["undo"])
    assert (repo / "a.py").read_text(encoding="utf-8") == "v = 1\n"


def test_a_runaway_winner_is_not_applied(repo, tmp_path, monkeypatch):
    wd = tmp_path / "winner"
    shutil.copytree(repo, wd)
    (wd / "a.py").write_text("v = 2\n", encoding="utf-8")
    for i in range(12):
        (wd / f"spam{i}.py").write_text("x", encoding="utf-8")
    winner = RacerResult("fake", True, 1, 0, 0, 0.0, workdir=str(wd),
                         reason="passed", spec="fake", tier="free")
    monkeypatch.setattr(race_mod, "race", lambda *a, **k: [winner])
    monkeypatch.setattr(store, "MAX_APPLY_FILES", 5)

    res = delegate(["fake"], str(repo), "set v to 2", CHECK)

    assert res["applied_files"] == []
    assert "13 files" in res["warning"]
    assert (repo / "a.py").read_text(encoding="utf-8") == "v = 1\n"


# --- the cache --------------------------------------------------------------
def test_the_same_job_on_the_same_code_replays_for_free(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(race_mod, "race", lambda *a, **k: [_winner_fixing(repo, tmp_path)])
    first = delegate(["fake"], str(repo), "set v to 2", CHECK)
    assert first["ok"] and not first.get("cached")

    store.restore(first["undo"])  # back to the original code
    monkeypatch.setattr(race_mod, "race", _never_race)

    second = delegate(["fake"], str(repo), "set v to 2", CHECK)

    assert second["ok"] is True
    assert second["cached"] is True
    assert second["tokens"] == 0
    assert (repo / "a.py").read_text(encoding="utf-8") == "v = 2\n"


def test_a_cache_entry_that_no_longer_satisfies_the_check_is_discarded(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(race_mod, "race", lambda *a, **k: [_winner_fixing(repo, tmp_path)])
    first = delegate(["fake"], str(repo), "set v to 2", CHECK)
    store.restore(first["undo"])

    # The cached patch is now wrong: the check demands v = 3.
    (repo / "check.py").write_text(
        "import pathlib, sys\n"
        "sys.exit(0 if 'v = 3' in pathlib.Path('a.py').read_text() else 1)\n",
        encoding="utf-8",
    )
    key = store.cache_key(repo, "set v to 2", CHECK)
    store.cache_put(key, {"a.py": b"v = 2\n"}, {"winner": "stale"})

    calls = []
    monkeypatch.setattr(race_mod, "race", lambda *a, **k: calls.append(1) or [])

    res = delegate(["fake"], str(repo), "set v to 2", CHECK)

    assert calls, "a stale cache entry must fall through to a real race"
    assert res["ok"] is False
    assert store.cache_get(key) is None
    assert (repo / "a.py").read_text(encoding="utf-8") == "v = 1\n"  # replay rolled back
