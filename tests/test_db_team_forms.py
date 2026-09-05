"""Consumer contract tests for RuntimeDatabase.match_history (implemented separately)."""
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matches
import runtime_db
import team_form
from src import live_market_refresh as refresh
from src.prediction_runtime import PredictionRuntime, ledger_features


NOW = datetime(2026, 8, 30, 1, tzinfo=UTC)


def result(kickoff="2026-08-29T18:00:00", home="KIA", away="SSG",
           league="KBO", sport="bs", scores=(3, 0)):
    stamp = pd.Timestamp(kickoff)
    return dict(kickoff=kickoff, date=stamp.normalize().isoformat(), year=stamp.year,
                league=league, sport=sport, home_team=home, away_team=away,
                home_score=scores[0], away_score=scores[1],
                outcome=1.0 if scores[0] > scores[1] else 0.0 if scores[0] < scores[1] else 0.5)


@pytest.fixture
def history_db(monkeypatch):
    rows, calls = [], []

    def query(*, sports=None, before=None):
        calls.append(dict(sports=sports, before=before))
        return [deepcopy(row) for row in rows
                if (not sports or row["sport"] in sports)
                and (before is None or pd.Timestamp(row["kickoff"]) < pd.Timestamp(before))]

    monkeypatch.setenv("PROODD_DB_PATH", "unused-contract-db")
    monkeypatch.setattr(runtime_db, "RuntimeDatabase", lambda: SimpleNamespace(match_history=query))
    monkeypatch.setattr(pd, "read_csv", lambda *a, **kw: pytest.fail("CSV read in DB consumer"))
    return rows, calls


def live_odds(*, dates=("08.30(일) 18:00",), observed=NOW.isoformat()):
    markets = {}
    for index, date in enumerate(dates):
        number = str(7100 + index)
        markets[number] = dict(game_no=number, date=date, sport="bs", league="KBO",
                               home="KIA", away="SSG", market="승패", label="",
                               odds=[1.55, 2.05], result="경기전")
    return dict(generated_at=observed, markets={"102": markets})


def document():
    return dict(live=[], past=[], rounds=[])


def test_db_matches_ignores_explicit_csv_and_preserves_dataframe_contract(history_db):
    rows, calls = history_db
    rows.extend([result("2026-08-29T18:00:00"), result("2026-08-29T14:00:00"),
                 result("2026-08-30T10:00:00")])
    frame = matches.load_matches(("bs",), path=Path("must-not-read.csv"),
                                 before="2026-08-30T01:00:00+00:00")
    assert calls == [dict(sports=("bs",), before="2026-08-30T10:00:00")]
    assert len(frame) == 2
    assert list(frame.kickoff.dt.hour) == [14, 18]
    assert list(frame.date.dt.hour) == [0, 0]
    assert frame.year.tolist() == [2026, 2026]
    assert frame.kickoff.dt.tz is None
    assert frame.outcome.tolist() == [1.0, 1.0]


def test_empty_db_returns_typed_history_and_truthful_status(history_db):
    frame = matches.load_matches()
    assert frame.empty
    assert pd.api.types.is_datetime64_any_dtype(frame.kickoff)
    assert team_form.build_forms(frame) == ({}, {})
    provider = team_form.DatabaseTeamForms(NOW, NOW)
    data = provider.for_game(dict(league="KBO", home="KIA", away="SSG"), "2026-08-30T18:00")
    assert data["form_home"] is None
    assert data["form_home_status"] == data["form_away_status"] == "missing_db_history"


def test_db_query_failure_has_no_file_fallback(history_db, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(runtime_db, "RuntimeDatabase", lambda: SimpleNamespace(match_history=broken))
    with pytest.raises(RuntimeError, match="database unavailable"):
        matches.load_matches()


def test_fixture_csv_and_cutoff_remain_available_only_with_db_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("PROODD_DB_PATH", raising=False)
    path = tmp_path / "fixture.csv"
    pd.DataFrame([
        dict(year=2026, round=102, date_text=f"08.29(토) {hour}:00", league="KBO", sport="bs",
             home="KIA 3", away="0 SSG", market_family="승패", result="홈승", is_void=False)
        for hour in (14, 18)
    ]).to_csv(path, index=False)
    frame = matches.load_matches(path=path, before="2026-08-29T16:00:00")
    assert frame.kickoff.dt.hour.tolist() == [14]


def test_status_separates_unmapped_team_from_no_season_sample(history_db):
    rows, _ = history_db
    rows.append(result("2025-08-29T18:00:00"))
    provider = team_form.DatabaseTeamForms(NOW, NOW)
    data = provider.for_game(dict(league="KBO", home="KIA", away="UNKNOWN"), "2026-08-30T18:00")
    assert data["form_home_status"] == "no_season_sample"
    assert data["form_away_status"] == "team_unmapped"
    assert data["form_home"] is data["form_away"] is None


def test_forms_share_query_and_build_but_keep_game_specific_rest(history_db, monkeypatch):
    rows, calls = history_db
    rows.extend([result(), result("2026-08-30T10:00:00"), result("2026-08-31T18:00:00")])
    builds = []
    original = team_form.build_forms

    def build(*args, **kwargs):
        builds.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(team_form, "build_forms", build)
    refreshed, changed = refresh.refresh_document(
        document(), live_odds(dates=("08.30(일) 18:00", "08.30(일) 20:00", "09.02(수) 18:00")), now=NOW)
    assert changed == 3
    assert len(calls) == len(builds) == 1
    assert [game["form_home"]["w"] for game in refreshed["live"]] == [1, 1, 1]
    assert [game["form_home"]["rest_days"] for game in refreshed["live"]] == [1, 1, 4]
    assert refreshed["live"][0]["form_away"]["avg_scored"] == 0.0
    assert refreshed["live"][0]["form_home"]["recent_games"][0]["date"] == "2026-08-29T18:00:00"


def test_wall_clock_caps_future_feed_history(history_db):
    rows, calls = history_db
    rows.extend([result(), result("2026-08-30T11:00:00")])
    provider = team_form.DatabaseTeamForms("2026-08-30T04:00:00+00:00", NOW)
    data = provider.for_game(dict(league="KBO", home="KIA", away="SSG"), "2026-08-30T18:00")
    assert calls[0]["before"] == "2026-08-30T10:00:00"
    assert data["form_home"]["w"] == 1


def test_distinct_past_kickoffs_use_strict_cutoffs(history_db):
    rows, _ = history_db
    rows.extend([result("2026-08-29T14:00:00"), result("2026-08-29T18:00:00")])
    provider = team_form.DatabaseTeamForms(NOW, NOW)
    game = dict(league="KBO", home="KIA", away="SSG")
    assert provider.for_game(game, "2026-08-29T14:00")["form_home"] is None
    assert provider.for_game(game, "2026-08-29T18:00")["form_home"]["w"] == 1


def test_target_season_rules_and_future_season_boundary(history_db):
    rows, _ = history_db
    rows.extend([result("2025-12-01T18:00", league="NBA", sport="bk"),
                 result("2026-01-01T18:00", league="NBA", sport="bk")])
    january = datetime(2026, 1, 15, tzinfo=UTC)
    provider = team_form.DatabaseTeamForms(january, january)
    game = dict(league="NBA", home="KIA", away="SSG")
    assert provider.for_game(game, "2026-01-16T18:00")["form_home"]["w"] == 2
    assert provider.for_game(game, "2026-10-01T18:00")["form_home_status"] == "no_season_sample"


def test_forms_exist_before_snapshot_and_are_frozen_in_ledger(history_db, monkeypatch, tmp_path):
    rows, _ = history_db
    rows.append(result())
    seen = []
    original = refresh.build_decision_snapshot

    def snapshot(game, **kwargs):
        seen.append(deepcopy(game["form_home"]))
        return original(game, **kwargs)

    monkeypatch.setattr(refresh, "build_decision_snapshot", snapshot)
    refreshed, _ = refresh.refresh_document(document(), live_odds(), now=NOW)
    game = refreshed["live"][0]
    assert seen == [game["form_home"]]
    assert all(option["모델확률"] is None for option in game["options"])
    assert game.get("lam_home") is None
    # Exercise append-only recording with a local test ledger, not a DB stub.
    monkeypatch.delenv("PROODD_DB_PATH")
    runtime = PredictionRuntime(tmp_path / "pregame.jsonl", clock=lambda: NOW)
    counts = refresh.record_live_market_revisions(refreshed, NOW.isoformat(), runtime)
    assert counts["predictions"] == 1
    record = runtime.records()[0]
    assert record["features"]["team_context"]["home_form"] == seen[0]


@pytest.mark.parametrize("pinned", [False, True])
def test_unchanged_prices_only_update_display_and_preserve_saved_inputs(history_db, pinned):
    rows, _ = history_db
    refreshed, _ = refresh.refresh_document(document(), live_odds(), now=NOW)
    game = refreshed["live"][0]
    if pinned:
        game["prediction_status"] = "recorded_pregame"
        game["prediction_record"] = {"selection": "홈", "prediction_snapshot_id": "saved"}
    inputs, snapshot = deepcopy(ledger_features(game)), deepcopy(game["decision_snapshot"])
    rows.append(result())
    refreshed, changed = refresh.refresh_document(refreshed, live_odds(), now=NOW)
    assert changed == 1
    assert game["team_form_display"]["form_home"]["w"] == 1
    assert game["form_home"] is None
    assert game["decision_snapshot"] == snapshot
    assert ledger_features(game) == inputs
    assert refresh.refresh_document(refreshed, live_odds(), now=NOW)[1] == 0


def test_changed_pinned_prices_do_not_replace_canonical_forms(history_db):
    rows, _ = history_db
    refreshed, _ = refresh.refresh_document(document(), live_odds(), now=NOW)
    game = refreshed["live"][0]
    game["prediction_status"] = "recorded_pregame"
    game["prediction_record"] = {"selection": "홈", "market": "승패", "label": ""}
    saved = deepcopy(game["decision_snapshot"])
    rows.append(result())
    feed = live_odds(observed="2026-08-30T02:00:00+00:00")
    feed["markets"]["102"]["7100"]["odds"] = [1.6, 2.0]
    refresh.refresh_document(refreshed, feed, now=datetime(2026, 8, 30, 2, tzinfo=UTC))
    assert game["form_home"] is None
    assert game["team_form_display"]["form_home"]["w"] == 1
    assert game["decision_snapshot"] == saved


@pytest.mark.parametrize("clock,observed", [
    ("2026-08-30T09:00:00+00:00", NOW.isoformat()),
    ("2026-08-30T10:00:00+00:00", "2026-08-30T10:00:00+00:00"),
])
def test_after_start_never_calls_snapshot_or_history(history_db, monkeypatch, clock, observed):
    _, calls = history_db
    monkeypatch.setattr(refresh, "build_decision_snapshot", lambda *a, **kw: pytest.fail("post-start prediction"))
    refreshed, _ = refresh.refresh_document(document(), live_odds(observed=observed),
                                           now=datetime.fromisoformat(clock))
    game = refreshed["live"][0]
    assert "decision_snapshot" not in game
    assert game["추천"] is None
    assert calls == []


def test_db_off_refresh_is_fileless_and_does_not_load_forms(monkeypatch):
    monkeypatch.delenv("PROODD_DB_PATH", raising=False)
    monkeypatch.setattr(team_form, "load_history", lambda **kw: pytest.fail("unexpected history read"))
    monkeypatch.setattr(pd, "read_csv", lambda *a, **kw: pytest.fail("unexpected CSV read"))
    refreshed, changed = refresh.refresh_document(document(), live_odds())
    assert changed == 1
    assert "decision_snapshot" in refreshed["live"][0]
    assert "form_home" not in refreshed["live"][0]


def test_real_database_dedup_conflicts_and_lightweight_consumer(monkeypatch, tmp_path):
    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setattr(pd, "read_csv", lambda *a, **kw: pytest.fail("CSV fallback"))
    db = runtime_db.RuntimeDatabase()
    base = dict(year=2026, round=100, game_no="1", date_text="08.29(토) 14:00",
                league="KBO", sport="bs", home="KIA 3", away="0 SSG",
                market_family="승패", result="홈승", is_void=False)
    db.record_match_rows([
        base, {**base, "round": 101},  # Reissue: one physical game.
        {**base, "game_no": "2", "date_text": "08.29(토) 18:00"},  # Doubleheader.
        {**base, "game_no": "3", "date_text": "08.28(금) 18:00"},
        {**base, "game_no": "4", "date_text": "08.28(금) 18:00", "home": "KIA 4"},
        {**base, "game_no": "5", "date_text": "08.27(목) 18:00", "market_family": "핸디캡"},
        {**base, "game_no": "6", "date_text": "08.26(수) 18:00", "is_void": True},
        {**base, "game_no": "7", "date_text": "08.30(일) 18:00"},  # Future final.
    ])
    frame = matches.load_matches(before="2026-08-30T10:00:00")
    assert len(frame) == 2
    assert frame.kickoff.dt.hour.tolist() == [14, 18]
    refreshed, changed = refresh.refresh_document(document(), live_odds(), now=NOW)
    assert changed == 1
    game = refreshed["live"][0]
    assert game["form_home"]["w"] == 2
    assert game["form_away"]["l"] == 2
    assert game["form_home_status"] == "available"
    assert game["form_home"]["recent_games"][0]["date"] == "2026-08-29T18:00:00"
    # Recording uses the same production DB path, including the enriched inputs.
    runtime = PredictionRuntime(tmp_path / "unused.jsonl", clock=lambda: NOW)
    counts = refresh.record_live_market_revisions(refreshed, NOW.isoformat(), runtime)
    assert counts["predictions"] == 1
    assert runtime.records()[0]["features"]["team_context"]["home_form"]["w"] == 2
    assert not (tmp_path / "unused.jsonl").exists()


def test_real_db_rollover_uses_actual_game_year(monkeypatch, tmp_path):
    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "runtime.sqlite3"))
    runtime_db.RuntimeDatabase().record_match_rows([
        dict(year=2026, round=1, game_no="1", date_text="12.31(수) 18:00",
             league="NBA", sport="bk", home="A 110", away="100 B",
             market_family="승패", result="홈승", is_void=False),
    ])
    frame = matches.load_matches(("bk",), before="2026-01-01T00:00:00")
    assert frame.year.tolist() == [2025]
    assert frame.kickoff.iloc[0] == pd.Timestamp("2025-12-31T18:00:00")


def test_after_start_preserves_existing_prediction_and_form_inputs(history_db):
    rows, calls = history_db
    rows.append(result())
    refreshed, _ = refresh.refresh_document(document(), live_odds(), now=NOW)
    game = refreshed["live"][0]
    saved = deepcopy(game)
    feed = live_odds(observed="2026-08-30T10:00:00+00:00")
    feed["markets"]["102"]["7100"]["odds"] = [2.0, 1.6]
    _, changed = refresh.refresh_document(refreshed, feed,
                                          now=datetime(2026, 8, 30, 10, tzinfo=UTC))
    assert changed == 0
    assert game == saved
    assert len(calls) == 1
