from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime_db import RuntimeDatabase
from pregame_player_capture import capture


def sample():
    return dict(league="KBO", home_team="KIA", away_team="LG",
                game_datetime="2026-09-27T17:00:00", game_id="naver-kbo",
                source="네이버 스포츠", updated_at="2026-09-27T00:00:00Z",
                starters={"home": {"name": "H"}, "away": {"name": "A"}})


def test_known_kbo_local_time_projects_and_captures(tmp_path):
    row = sample()
    db = RuntimeDatabase(tmp_path / "db.sqlite3")
    db.put_document("player_info", {"games": [row]})
    doc = db.player_fixture_document("KBO", "KIA", "LG", "2026-09-27T08:00:00Z")
    assert doc["games"] == [row]
    result = capture(dict(league="KBO", home="KIA", away="LG"), doc,
                     observed_at="2026-09-27T01:00:00Z", kickoff="2026-09-27T08:00:00Z")
    assert result["status"] == "captured"
    assert result["training_eligible"] is False


def test_batch_uses_same_kbo_timezone_and_ambiguity_guard(tmp_path):
    db = RuntimeDatabase(tmp_path / "batch.sqlite3")
    row = sample()
    key = ("KBO", "KIA", "LG", "2026-09-27T08:00:00Z")
    db.put_document("player_info", {"games": [row]})
    assert db.player_fixtures_document([key]) == db.player_fixture_document(*key)
    duplicate = dict(row, game_datetime="2026-09-27T08:00:00Z")
    db.put_document("player_info", {"games": [row, duplicate, duplicate]})
    assert len(db.player_fixtures_document([key])["games"]) == 2
    db.put_document("player_info", {"games": [dict(row, source="unknown")]})
    assert db.player_fixtures_document([key]) == {"games": []}


def test_curated_team_aliases_connect_without_fuzzy_dates(tmp_path):
    db = RuntimeDatabase(tmp_path / 'aliases.sqlite3')
    row = dict(sample(), league='MLB', home_team='클리블랜드', away_team='탬파베이',
               game_datetime='2026-09-27T08:00:00Z', source='MLB Stats API')
    key = ('MLB', '클리가디', '탬파레이', '2026-09-27T08:00:00Z')
    db.put_document('player_info', {'games': [row]})
    document = db.player_fixtures_document([key])
    assert document['games'] == [row]
    assert document == db.player_fixture_document(*key)
    game = dict(league=key[0], home=key[1], away=key[2])
    assert capture(game, document, observed_at='2026-09-27T01:00:00Z',
                   kickoff=key[3])['status'] == 'captured'
    assert db.player_fixtures_document([(*key[:3], '2026-09-27T09:00:00Z')])['games'] == []
    db.put_document('player_info', {'games': [row, dict(row, home_team='클리가디')]})
    assert capture(game, db.player_fixtures_document([key]), observed_at='2026-09-27T01:00:00Z',
                   kickoff=key[3])['status'] == 'unavailable'


def test_known_local_time_does_not_relax_freshness_or_ambiguity(tmp_path):
    db = RuntimeDatabase(tmp_path / "db.sqlite3")
    row = sample()
    row["updated_at"] = "2026-09-26T11:34:13Z"
    game = dict(league="KBO", home="KIA", away="LG")
    args = dict(observed_at="2026-09-26T23:04:36Z", kickoff="2026-09-27T08:00:00Z")
    assert capture(game, {"games": [row]}, **args)["reason"] == "stale_player_context"
    duplicate = deepcopy(row)
    duplicate["game_datetime"] = "2026-09-27T17:00:00+09:00"
    db.put_document("player_info", {"games": [row, duplicate]})
    doc = db.player_fixture_document("KBO", "KIA", "LG", args["kickoff"])
    assert len(doc["games"]) == 2
    assert capture(game, doc, **args)["reason"] == "missing_or_ambiguous_fixture"


def test_unknown_naive_time_is_not_guessed(tmp_path):
    db = RuntimeDatabase(tmp_path / "db.sqlite3")
    row = sample()
    row["source"] = "unknown"
    db.put_document("player_info", {"games": [row]})
    assert db.player_fixture_document("KBO", "KIA", "LG", "2026-09-27T08:00:00Z")["games"] == []
    assert capture(dict(league="KBO", home="KIA", away="LG"), {"games": [row]},
                   observed_at="2026-09-27T01:00:00Z", kickoff="2026-09-27T08:00:00Z")["status"] == "unavailable"


def test_project_capture_freeze_and_settle_end_to_end(tmp_path):
    from datetime import datetime, timezone
    from src.live_market_refresh import refresh_document, record_live_market_revisions, settle_live_market_results
    from src.prediction_runtime import PredictionRuntime
    from test_live_ledger_settlement import feed

    row = dict(sample(), away_team="SSG", game_datetime="2026-08-30T18:00:00",
               updated_at="2026-08-30T00:00:00Z")
    db = RuntimeDatabase(tmp_path / "players.db")
    db.put_document("player_info", {"games": [row]})
    players = db.player_fixture_document("KBO", "KIA", "SSG", "2026-08-30T09:00:00Z")
    odds = feed()
    doc, _ = refresh_document({"live": [], "past": []}, odds, player_document=players)
    clock = [datetime(2026, 8, 30, 1, 5, tzinfo=timezone.utc)]
    runtime = PredictionRuntime(tmp_path / "ledger.jsonl", clock=lambda: clock[0])
    assert record_live_market_revisions(doc, odds["generated_at"], runtime)["predictions"] == 1
    before = deepcopy(runtime.records()[0])
    assert before["features"]["research_player_inputs"]["status"] == "captured"
    assert not before["features"]["research_player_inputs"]["training_eligible"]
    players["games"][0]["starters"]["home"]["name"] = "changed later"
    # Current policy freezes at T-30, not at the first morning observation.
    odds["generated_at"] = "2026-08-30T08:40:00Z"
    odds["markets"]["102"]["7100"]["odds"] = [1.6, 2.0]
    refresh_document(doc, odds, player_document=players)
    assert doc["live"][0]["research_player_inputs"] == before["features"]["research_player_inputs"]
    clock[0] = datetime(2026, 8, 30, 14, tzinfo=timezone.utc)
    odds["generated_at"] = "2026-08-30T13:00:00Z"
    odds["markets"]["102"]["7100"]["result"] = "홈승"
    assert settle_live_market_results(odds, runtime) == 1
    assert settle_live_market_results(odds, runtime) == 0
    assert runtime.records()[0] == before
    assert runtime.records()[-1]["snapshot_id"] == before["snapshot_id"]


def test_player_only_artifact_revision_reaches_prepared_cache(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    import runtime_db
    from match_api import MatchViews, PreparedMatchResponses

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 27, tzinfo=timezone.utc)

    monkeypatch.setattr(runtime_db, "datetime", FrozenClock)

    db = RuntimeDatabase(tmp_path / "views.db")
    doc = {"live": [], "past": [], "generated_at": "2026-09-26T12:44:07Z",
           "player_info_at": "2026-09-23T00:00:00Z"}
    db.store_artifact("picks_v2", doc)
    cache = PreparedMatchResponses(MatchViews(db))
    cache.refresh()
    old = json.loads(cache.get_bytes(compressed=False))
    doc["player_info_at"] = "2026-09-26T12:56:00Z"
    db.store_artifact("picks_v2", doc)
    # Deferred warmer retains the genuine old revision, not a fabricated new one.
    assert json.loads(cache.get_bytes(compressed=False)) == old
    cache.refresh()
    new = json.loads(cache.get_bytes(compressed=False))
    assert new["player_info_at"] == doc["player_info_at"]
    assert new["generated_at"] == old["generated_at"]
    assert new["view"]["revision"] != old["view"]["revision"]
    # The import writer must not restore the frozen clock's old revision either.
    doc["generated_at"] = "2026-09-27T00:01:00Z"
    assert db.import_artifact("picks_v2", doc)
    cache.refresh()
    imported = json.loads(cache.get_bytes(compressed=False))
    assert imported["generated_at"] == doc["generated_at"]
    assert imported["view"]["revision"] > new["view"]["revision"]
