import pytest

from maestro import store


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "state"))


def test_ignores_dependency_and_build_directories():
    assert store.is_ignored("node_modules/react/index.js")
    assert store.is_ignored("dist/bundle.js")
    assert store.is_ignored(".next/server/page.js")
    assert store.is_ignored("api/__pycache__/x.pyc")
    assert not store.is_ignored("src/app.ts")
    assert not store.is_ignored("distance.py")  # substring must not match


def test_snapshot_then_restore_brings_back_the_original_bytes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.py").write_text("original\n", encoding="utf-8")

    sid = store.snapshot(repo, ["keep.py", "new.py"])
    (repo / "keep.py").write_text("clobbered\n", encoding="utf-8")
    (repo / "new.py").write_text("created\n", encoding="utf-8")

    res = store.restore(sid)

    assert res["ok"]
    assert (repo / "keep.py").read_text(encoding="utf-8") == "original\n"
    assert not (repo / "new.py").exists()  # did not exist before -> removed


def test_restore_reports_when_there_is_nothing_to_undo():
    assert store.restore()["ok"] is False


def test_cache_roundtrip_preserves_file_bytes():
    store.cache_put("k1", {"a.py": b"hello"}, {"winner": "ollama"})
    entry = store.cache_get("k1")

    assert entry["meta"]["winner"] == "ollama"
    assert store.cache_files(entry) == {"a.py": b"hello"}

    store.cache_discard("k1")
    assert store.cache_get("k1") is None


def test_cache_key_tracks_the_task_the_check_and_the_code(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("v = 1\n", encoding="utf-8")

    k = store.cache_key(repo, "task", "check")
    assert store.cache_key(repo, "task", "check") == k          # stable
    assert store.cache_key(repo, "other", "check") != k          # task matters
    assert store.cache_key(repo, "task", "other") != k           # check matters

    (repo / "a.py").write_text("v = 2\n", encoding="utf-8")
    assert store.cache_key(repo, "task", "check") != k           # code matters


def test_cache_key_ignores_build_output(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("v = 1\n", encoding="utf-8")
    k = store.cache_key(repo, "t", "c")

    (repo / "dist").mkdir()
    (repo / "dist" / "bundle.js").write_text("anything", encoding="utf-8")

    assert store.cache_key(repo, "t", "c") == k


def test_run_journal_survives_the_process_that_wrote_it():
    store.record_run({"id": "abc123", "mode": "race", "winner": "ollama", "ok": True})

    assert store.get_run("abc123")["winner"] == "ollama"
    assert [r["id"] for r in store.list_runs()] == ["abc123"]


def test_remove_workdirs_deletes_only_inside_the_scratch_space(tmp_path):
    mine = store.state_dir() / "race" / "racer-1"
    mine.mkdir(parents=True)
    (mine / "f.txt").write_text("x", encoding="utf-8")

    # A path outside .maestro/race must never be deleted, whatever is passed in.
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "work.py").write_text("do not delete me", encoding="utf-8")

    removed = store.remove_workdirs([str(mine), str(outside), ""])

    assert removed == 1
    assert not mine.exists()
    assert (outside / "work.py").exists()


def test_purge_removes_stale_workdirs_but_keeps_fresh_ones():
    import os
    import time

    race_dir = store.state_dir() / "race"
    old, fresh = race_dir / "old", race_dir / "fresh"
    for d in (old, fresh):
        d.mkdir(parents=True)
        (d / "f.txt").write_text("x", encoding="utf-8")
    stale = time.time() - 48 * 3600
    os.utime(old, (stale, stale))

    assert store.purge_workdirs(max_age_hours=12) == 1
    assert not old.exists()
    assert fresh.exists()
