"""Production collectors/consumers must work without their legacy exports."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import baseball_context_watch
import baseball_live_features
import commentary_llm
import court_info
import free_context
import info_watch
import japan_info
import live_scores
import overseas_watch
import pickster_eval
import pickster_watch
import player_info
import player_name_localizer
import recommendation_context
import soccer_info
import weather_features
import weather_watch
import xg_watch
from runtime_db import RuntimeDatabase


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "runtime.sqlite3"))
    return RuntimeDatabase()


def forbid_files(monkeypatch):
    original_open = Path.open
    def fail(*args, **kwargs):
        raise AssertionError("production attempted legacy file access")
    def guarded_open(path, *args, **kwargs):
        # Windows may lazily load standard-library timezone resources.
        if "tzdata" in path.parts:
            return original_open(path, *args, **kwargs)
        return fail(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(Path, "read_text", fail)
    monkeypatch.setattr(Path, "write_text", fail)


def seed_venues(db):
    row = {"league": "KBO", "team": "LG", "venue_id": "jamsil",
           "venue_name": "잠실", "latitude": "37.512", "longitude": "127.072",
           "roof": "open", "coordinate_quality": "exact", "timezone": "Asia/Seoul"}
    db.replace_dataset_rows("static_venues", [row], list(row))
    return row


@pytest.mark.parametrize("loader,sport,league", [
    (soccer_info._load_proto_games, "sc", "K리그1"),
    (court_info._load_games, "bk", "NBA"),
    (japan_info._load_npb_proto_games, "bs", "NPB"),
])
def test_fixture_collectors_read_db_and_ignore_stale_file(db, tmp_path, monkeypatch,
                                                         loader, sport, league):
    path = tmp_path / "picks_v2.json"
    path.write_text(json.dumps({"live": [{"home": "stale"}]}), encoding="utf-8")
    game = {"sport": sport, "league": league, "home": "home", "away": "away",
            "date": "09.05(토) 18:00"}
    db.store_artifact("picks_v2", {"live": [game, {**game, "date": "invalid"}],
                                    "past": [game]})
    forbid_files(monkeypatch)
    games = loader(path, datetime(2026, 9, 5, tzinfo=timezone.utc))
    assert len(games) == 1
    assert games[0]["home"] == "home"


def test_missing_documents_and_events_never_reenter_files(db, tmp_path, monkeypatch):
    forbid_files(monkeypatch)
    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    for loader in (soccer_info._load_proto_games, court_info._load_games,
                   japan_info._load_npb_proto_games):
        assert loader(tmp_path / "stale.json", now) == []
    assert player_info.announcement_games() == []
    assert player_info.kbo_pitcher_stats() == {}
    assert player_info.game_index() == {}
    assert live_scores._aliases() == {}
    assert weather_features.load_snapshots() == []
    assert baseball_live_features._latest_context() == []
    assert baseball_live_features._latest_crowd() == []
    assert recommendation_context.ContextStore().rows == []
    assert pickster_eval._latest_picks() == {}
    assert commentary_llm._load() == {}
    assert commentary_llm._budget_load()["used"] == 0
    assert player_name_localizer._load() == {}
    with pytest.raises(KeyError, match="static_venues"):
        weather_watch.load_venues()
    with pytest.raises(KeyError, match="static_venues"):
        baseball_live_features._venue_map()


def test_announcements_use_observation_order_aliases_and_db_pitcher_stats(db, monkeypatch):
    db.put_document("processed_team_map", {"KBO": {"LG트윈스": "LG"}})
    db.put_document("detail_kbo_baseball", {"finished": {
        "date": "2026-09-01", "data": {"home": [
            {"name": "새선발", "inn": "6.0", "er": 2, "bb": 1, "kk": 6, "hr": 0},
        ]},
    }})
    base = {"league": "KBO", "gameId": "20260905LGLT", "home": "LG트윈스",
            "away": "롯데", "game_datetime": "2026-09-05T18:00:00+09:00"}
    rows = [
        {**base, "field": "homeStarterName", "value": "새선발", "observed_at": "2026-09-05T02:00:00Z"},
        {**base, "field": "awayStarterName", "value": "원정선발", "observed_at": "2026-09-05T01:00:00Z"},
        {**base, "field": "homeStarterName", "value": "옛선발", "observed_at": "2026-09-05T00:00:00Z"},
    ]
    info_watch._append(rows)
    forbid_files(monkeypatch)
    game, = player_info.announcement_games()
    assert game["home_team"] == "LG"
    assert game["starters"]["home"]["name"] == "새선발"
    assert game["starters"]["home"]["stats"]["era"] == 3.0
    assert game["starters"]["away"]["name"] == "원정선발"
    assert game["updated_at"] == "2026-09-05T02:00:00Z"
    assert live_scores._aliases()["LG"] == ["LG트윈스"]


def test_player_collection_bootstraps_missing_cache_without_files(db, monkeypatch):
    monkeypatch.setattr(player_info, "_session", lambda: object())
    monkeypatch.setattr(player_info, "collect_npb_games", lambda *a, **kw: [])
    monkeypatch.setattr(player_info, "mlb_games", lambda *a: ([], {}, None))
    monkeypatch.setattr(player_info, "collect_soccer_info", lambda *a: ([], {}))
    monkeypatch.setattr(player_info, "collect_court_info", lambda *a: ([], {}))
    forbid_files(monkeypatch)
    result = player_info.collect()
    assert result["games"] == []
    assert db.get_document("player_info") == result


def test_context_store_keeps_pregame_date_and_alias_eligibility(db, monkeypatch):
    db.put_document("processed_team_map", {"KBO": {"LG트윈스": "LG"}})
    base = {"game_id": "today", "league": "KBO", "home": "LG", "away": "롯데",
            "game_datetime": "2026-09-05T18:00:00+09:00"}
    eligible = {**base, "observed_at": "2026-09-05T08:00:00Z",
                "home_features": {"starter": {"name": "경기전선발"}}}
    baseball_context_watch._append([
        {**base, "observed_at": "2026-09-05T10:00:00Z"},
        {**base, "game_id": "tomorrow", "game_datetime": "2026-09-06T18:00:00+09:00",
         "observed_at": "2026-09-05T08:30:00Z"},
        eligible,
    ])
    db.put_document("processed_live_baseball_features", {"coefficient_status": "not_fitted",
        "games": [{"game_id": "today", "pickster_crowd": {
            "observed_at": "2026-09-05T08:00:00Z", "independent_capper_count": 3,
            "home_capper_count": 2, "away_capper_count": 1}}]})
    forbid_files(monkeypatch)
    store = recommendation_context.ContextStore(year=2026)
    evidence = store.evidence_for({"sport": "bs", "league": "KBO", "home": "LG트윈스",
                                   "away": "롯데", "date": "09.05(토) 18:00"})
    assert evidence["source_game_id"] == "today"
    assert evidence["observed_at"] == eligible["observed_at"]
    assert "경기전선발" in evidence["protected_entities"]
    assert store.features["today"]["pickster_crowd"]["home_capper_count"] == 2


def test_weather_and_feature_consumers_use_db_and_preserve_cutoff(db, monkeypatch):
    seed_venues(db)
    def forecast(hour, temperature):
        return {"venue_id": "jamsil", "source": "test", "roof": "open", "timezone": "UTC",
                "observed_at": f"2026-09-05T{hour:02}:00:00Z", "hourly": {
                    "valid_at": ["2026-09-05T09:00:00"],
                    **{field: [temperature] for field in weather_features.FIELDS}}}
    # Deliberately insert newest first; retrieval and as-of selection must differ.
    free_context.append_jsonl(weather_watch.OUT, [forecast(10, 99), forecast(7, 20), forecast(8, 25)],
                              stream="weather_forecasts")
    pickster_watch._append_jsonl(pickster_watch.CROWD_LOG, [
        {"observed_at": "2026-09-05T08:00:00Z", "games": [{"id": "new"}]},
        {"observed_at": "2026-09-05T07:00:00Z", "games": [{"id": "old"}]},
    ])
    forbid_files(monkeypatch)
    assert weather_watch.load_venues("KBO", "LG")[0]["latitude"] == 37.512
    assert baseball_live_features._venue_map()[("KBO", "LG")]["venue_id"] == "jamsil"
    assert baseball_live_features._latest_crowd() == [{"id": "new"}]
    weather = weather_features.select_asof_forecast(weather_features.load_snapshots(),
        venue_id="jamsil", kickoff="2026-09-05T09:00:00Z", cutoff="2026-09-05T08:30:00Z")
    assert weather["temperature_2m"] == 25
    assert weather["temperature_2m_revision"] == 5
    assert weather["weather_asof_ok"]


def test_pickster_evaluation_keeps_initial_eligibility_and_current_slate(db, monkeypatch):
    base = {"identity_version": 2, "market_type": "moneyline", "american_odds": -110}
    pickster_watch._append_jsonl(pickster_watch.PICK_LOG, [
        {**base, "pick_id": "eligible", "event_type": "result", "result": "W",
         "eligible_pre_event": False, "observed_at": "2026-09-05T10:00:00Z"},
        {**base, "pick_id": "eligible", "event_type": "first_observed",
         "eligible_pre_event": True, "observed_at": "2026-09-05T08:00:00Z"},
        {**base, "pick_id": "baseline", "event_type": "baseline", "result": "W",
         "eligible_pre_event": False, "observed_at": "2026-09-05T07:00:00Z"},
    ])
    pickster_watch._save_state({"current_pick_ids": ["eligible"]})
    forbid_files(monkeypatch)
    result = pickster_eval.build()
    assert result["visible_slate_characteristics"]["n_visible_unique_picks"] == 1
    assert result["prospective_validation"]["n_eligible_pre_event"] == 1
    assert result["prospective_validation"]["wins"] == 1
    pickster_eval.write_outputs(result)
    assert db.get_document("processed_pickster_eval") == result
    assert "markdown" in db.get_document("pickster_eval_report")


def test_missing_pickster_state_does_not_label_history_as_visible(db, monkeypatch):
    forbid_files(monkeypatch)
    result = pickster_eval._slate_profiles({"old": {"market_type": "moneyline"}})
    assert result["n_visible_unique_picks"] == 0


def test_collectors_append_idempotently_without_any_export(db, monkeypatch, capsys):
    forbid_files(monkeypatch)
    event = {"observed_at": "2026-09-05T08:00:00Z", "is_baseline": 0, "hours_before_game": 2}
    writers = [
        (info_watch._append, "starter_announcements"),
        (info_watch._append_changes, "starter_changes"),
        (overseas_watch._append, "overseas_live_odds"),
        (baseball_context_watch._append, "baseball_context_events"),
        (lambda rows: pickster_watch._append_jsonl(pickster_watch.PICK_LOG, rows), "pickster_pick_events"),
        (lambda rows: pickster_watch._append_jsonl(pickster_watch.LEADERBOARD_LOG, rows), "pickster_leaderboard"),
        (lambda rows: pickster_watch._append_jsonl(pickster_watch.CROWD_LOG, rows), "pickster_crowd"),
        (lambda rows: free_context.append_jsonl(weather_watch.OUT, rows, stream="weather_forecasts"), "weather_forecasts"),
    ]
    for write, stream in writers:
        write([event])
        write([event])
        assert db.events(stream) == [event]
    info_watch._append([{**event, "is_baseline": 1, "hours_before_game": 99}])
    info_watch.summarise()
    output = capsys.readouterr().out
    assert "기준선 1 제외" in output
    assert "2.0h" in output
    info_watch._save_state({"seen": 1})
    baseball_context_watch._save({"games": {"one": 1}})
    pickster_watch._save_state({"picks": {"one": 1}})
    assert info_watch._load_state() == {"seen": 1}
    assert baseball_context_watch._state()["games"] == {"one": 1}
    assert pickster_watch._load_state()["picks"] == {"one": 1}


def test_xg_collector_uses_db_for_daily_dedup_and_no_export(db, monkeypatch):
    monkeypatch.setattr(xg_watch, "_session", lambda: object())
    monkeypatch.setattr(xg_watch, "fixture_links", lambda *a: ["/a-vs-b"])
    monkeypatch.setattr(xg_watch, "_get", lambda *a: "html")
    monkeypatch.setattr(xg_watch, "parse_match", lambda *a: {"home_team": "a", "away_team": "b"})
    forbid_files(monkeypatch)
    assert xg_watch._collect("kleague1", None) == 0
    assert xg_watch._collect("kleague1", None) == 0
    events = db.events("xg_snapshots")
    assert len(events) == 1
    assert db.events("xg_snapshots", through=events[0]["snapshot_at"]) == events


def test_llm_cache_documents_round_trip_without_files(db, monkeypatch):
    forbid_files(monkeypatch)
    budget = {"date": commentary_llm._today(), "used": 4}
    commentary_llm._budget_save(budget)
    db.put_document("llm_commentary_cache", {"key": {"text": "해설"}})
    player_name_localizer._save({"John Smith": "존 스미스"})
    assert commentary_llm._budget_load() == budget
    assert commentary_llm._load() == {"key": {"text": "해설"}}
    assert player_name_localizer._load() == {"John Smith": "존 스미스"}


def test_weather_poll_and_feature_publish_need_no_export_files(db, monkeypatch):
    seed_venues(db)
    monkeypatch.setattr(weather_watch.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["baseball_live_features.py"])
    baseball_context_watch._append([{
        "game_id": "today", "league": "KBO", "home": "LG", "away": "롯데",
        "observed_at": "2026-09-05T08:00:00Z",
        "game_datetime": "2026-09-05T18:00:00+09:00",
        "home_features": {"starter": {"season": {"era": 2.0}}},
        "away_features": {"starter": {"season": {"era": 4.0}}},
    }])
    forbid_files(monkeypatch)
    assert weather_watch.poll("KBO", session=weather_watch._FakeSession()) == 1
    assert len(weather_features.load_snapshots()) == 1
    assert baseball_live_features.main() == 0
    store = recommendation_context.ContextStore(year=2026)
    assert store.features["today"]["raw_features"]["home_starter_era_edge"] == 2.0
