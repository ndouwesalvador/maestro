"""Cascade: spend the cheap tier first, and only escalate if it fails."""

import pytest

from maestro import race as race_mod
from maestro.race import RacerResult, waves

FREE = "ollama:gpt-oss:120b-cloud"
PAID_API = "deepseek:deepseek-chat"
SUBSCRIPTION = "claude-code"


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "state"))


def test_waves_are_ordered_cheapest_first():
    assert waves([SUBSCRIPTION, FREE, PAID_API]) == [[FREE], [PAID_API], [SUBSCRIPTION]]


def test_models_of_the_same_price_share_one_wave():
    assert waves([FREE, "opencode", SUBSCRIPTION]) == [[FREE, "opencode"], [SUBSCRIPTION]]


def _fake_solo(winners):
    def solo(spec, *_a, **_k):
        solo.seen.append(spec)
        won = spec in winners
        return RacerResult(spec, won, 1, 100, 50, 0.0, spec=spec,
                           reason="passed" if won else "failed")
    solo.seen = []
    return solo


def test_a_free_win_never_touches_the_subscription(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    solo = _fake_solo({FREE})
    monkeypatch.setattr(race_mod, "_solo_run", solo)

    results = race_mod.race([SUBSCRIPTION, FREE], str(repo), "t", "c", mode="cascade")

    assert solo.seen == [FREE], "the subscription tier must not have been started"
    skipped = [r for r in results if r.reason == "skipped"]
    assert [r.spec for r in skipped] == [SUBSCRIPTION]


def test_it_escalates_when_the_cheap_tier_fails(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    solo = _fake_solo({SUBSCRIPTION})
    monkeypatch.setattr(race_mod, "_solo_run", solo)

    results = race_mod.race([SUBSCRIPTION, FREE], str(repo), "t", "c", mode="cascade")

    assert solo.seen == [FREE, SUBSCRIPTION]
    assert race_mod.pick_winner(results).spec == SUBSCRIPTION
    assert not [r for r in results if r.reason == "skipped"]


def test_race_mode_still_starts_everyone_at_once(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    solo = _fake_solo({FREE})
    monkeypatch.setattr(race_mod, "_solo_run", solo)

    race_mod.race([SUBSCRIPTION, FREE], str(repo), "t", "c", mode="race")

    assert set(solo.seen) == {SUBSCRIPTION, FREE}


def test_delegate_reports_which_tiers_it_did_not_need(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(race_mod, "_solo_run", _fake_solo({FREE}))

    res = race_mod.delegate([SUBSCRIPTION, FREE], str(repo), "t", "c",
                            mode="cascade", apply=False, use_baseline=False,
                            use_cache=False)

    assert res["ok"] is True
    assert res["escalated"] is False
    assert res["unused_tiers"] == ["subscription"]
