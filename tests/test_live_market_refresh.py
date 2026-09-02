from datetime import UTC, datetime

import pytest

from src.live_market_refresh import (record_live_market_revisions,
                                     refresh_document)
from src.prediction_runtime import PredictionRuntime
from prediction_ledger import PredictionLedgerError


def _live_odds():
    return {
        "generated_at": "2026-08-30T01:00:00+00:00",
        "markets": {"102": {
            "7100": {
                "game_no": "7100", "date": "08.30(일) 18:00",
                "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
                "market": "승패", "label": "", "n_way": 2,
                "odds": [1.55, 2.05], "result": "경기전",
            },
            "7101": {
                "game_no": "7101", "date": "08.30(일) 18:00",
                "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
                "market": "언더오버", "label": "U 10.5", "n_way": 2,
                "odds": [1.70, 1.82], "result": "경기전",
            },
        }},
    }


def test_refresh_hydrates_unpriced_game_and_updates_document_clock():
    document = {"generated_at": "2026-08-28T00:00:00+00:00", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}

    refreshed, changed = refresh_document(document, _live_odds())

    game = refreshed["live"][0]
    assert changed == 1
    assert refreshed["generated_at"] == _live_odds()["generated_at"]
    assert game["status"] == "경기전"
    assert len(game["options"]) == 4
    assert game["decision_snapshot"]["action"] == "market_reference"
    assert game["decision_snapshot"]["probability"]["market"] is not None


def test_changed_live_decision_is_recorded_before_publish(tmp_path):
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    refreshed, changed = refresh_document(document, _live_odds())
    assert changed == 1

    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl",
        clock=lambda: datetime(2026, 8, 30, 1, 5, tzinfo=UTC),
    )
    counts = record_live_market_revisions(
        refreshed,
        _live_odds()["generated_at"],
        runtime,
    )

    game = refreshed["live"][0]
    records = runtime.records()
    assert counts == {"predictions": 1, "skipped": 0, "withheld": 0}
    assert len(records) == 1
    assert records[0]["record_type"] == "prediction"
    assert records[0]["predictions"]["probability_detail"]["basis"] == "shin_market"
    assert game["prediction_status"] == "recorded_pregame"
    assert game["prediction_record"]["prediction_snapshot_id"] == records[0]["snapshot_id"]


def test_withhold_revision_is_recorded_without_ui_prediction(tmp_path):
    live_odds = _live_odds()
    live_odds["markets"]["102"] = {
        "7100": {**live_odds["markets"]["102"]["7100"], "odds": [2.20, 2.20]},
    }
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    refreshed, _ = refresh_document(document, live_odds)
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl",
        clock=lambda: datetime(2026, 8, 30, 1, 5, tzinfo=UTC),
    )

    counts = record_live_market_revisions(
        refreshed, live_odds["generated_at"], runtime
    )

    game = refreshed["live"][0]
    assert counts == {"predictions": 1, "skipped": 0, "withheld": 0}
    assert runtime.records()[0]["predictions"]["action"] == "withhold"
    assert game["prediction_status"] == "recorded_withhold"
    assert game["prediction_revision_id"] == runtime.records()[0]["snapshot_id"]
    assert "prediction_record" not in game


def test_older_live_feed_cannot_replace_newer_ledger_revision(tmp_path):
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl",
        clock=lambda: datetime(2026, 8, 30, 2, 5, tzinfo=UTC),
    )
    base = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    newer_odds = _live_odds()
    newer_odds["generated_at"] = "2026-08-30T02:00:00+00:00"
    newer, _ = refresh_document(base, newer_odds)
    record_live_market_revisions(newer, newer_odds["generated_at"], runtime)

    older_odds = _live_odds()
    older_odds["generated_at"] = "2026-08-30T01:00:00+00:00"
    older_odds["markets"]["102"]["7100"]["odds"] = [1.60, 2.00]
    older_base = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    older, _ = refresh_document(older_base, older_odds)

    with pytest.raises(PredictionLedgerError, match="timestamp regressed"):
        record_live_market_revisions(older, older_odds["generated_at"], runtime)
    assert len(runtime.records()) == 1


def test_post_kickoff_live_row_is_withheld_without_blocking_publish(tmp_path):
    live_odds = _live_odds()
    live_odds["generated_at"] = "2026-08-30T10:00:00+00:00"
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    refreshed, _ = refresh_document(document, live_odds)
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl",
        clock=lambda: datetime(2026, 8, 30, 10, 5, tzinfo=UTC),
    )

    counts = record_live_market_revisions(
        refreshed, live_odds["generated_at"], runtime
    )

    game = refreshed["live"][0]
    assert counts == {"predictions": 0, "skipped": 0, "withheld": 1}
    assert runtime.records() == []
    assert game["prediction_status"] == "withheld_unrecorded_live_revision"
    assert game["_liveOddsChanged"] is True
    assert "decision_snapshot" not in game


def test_new_game_year_uses_kst_at_utc_new_year_boundary():
    live_odds = _live_odds()
    live_odds["generated_at"] = "2025-12-31T15:10:00+00:00"
    for value in live_odds["markets"]["102"].values():
        value["date"] = "01.01(목) 18:00"
    document = {"generated_at": "old", "rounds": [], "live": [], "past": []}

    refreshed, changed = refresh_document(document, live_odds)

    assert changed == 1
    assert refreshed["live"][0]["year"] == 2026


def test_refresh_adds_new_round_game_without_structure_model():
    document = {"generated_at": "old", "rounds": [], "live": [], "past": []}

    refreshed, changed = refresh_document(document, _live_odds())

    assert changed == 1
    assert refreshed["rounds"] == [102]
    assert refreshed["live"][0]["no_model"] is True
    assert refreshed["live"][0]["추천"] is not None


def test_refresh_recovers_mlb_odds_after_result_without_backfilling_prediction():
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 02:05",
        "sport": "bs", "league": "MLB", "home": "뉴욕양키", "away": "보스레드",
        "status": "배당대기", "options": [],
        "decision_snapshot": {"action": "old"},
    }], "past": []}
    live_odds = {
        "generated_at": "2026-08-30T03:00:00+00:00",
        "markets": {"102": {"7583": {
            "game_no": "7583", "date": "08.30(일) 02:05",
            "sport": "bs", "league": "MLB", "home": "뉴욕양키", "away": "보스레드",
            "market": "승패", "label": "", "odds": [1.63, 1.91], "result": "홈패",
        }}},
    }

    refreshed, changed = refresh_document(document, live_odds)

    game = refreshed["live"][0]
    assert changed == 1
    assert game["status"] == "결과확인"
    assert [option["배당"] for option in game["options"]] == [1.63, 1.91]
    assert game["odds_recovered_after_start"] is True
    assert game["prediction_status"] == "prediction_ledger_required"
    assert "decision_snapshot" not in game


def test_refresh_does_not_add_finished_game_missing_from_document():
    document = {"generated_at": "old", "rounds": [], "live": [], "past": []}
    live_odds = {
        "generated_at": "2026-08-30T03:00:00+00:00",
        "markets": {"102": {"7583": {
            "game_no": "7583", "date": "08.30(일) 02:05",
            "sport": "bs", "league": "MLB", "home": "뉴욕양키", "away": "보스레드",
            "market": "승패", "label": "", "odds": [1.63, 1.91], "result": "홈패",
        }}},
    }

    refreshed, changed = refresh_document(document, live_odds)

    assert changed == 0
    assert refreshed["live"] == []


def test_refresh_recalculates_when_total_line_changes_without_price_change():
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "경기전", "options": [
            {"market": "언더오버", "label": "U 10.5", "line": 10.5,
             "게임번호": "7101", "선택": "언더", "배당": 1.70},
            {"market": "언더오버", "label": "U 10.5", "line": 10.5,
             "게임번호": "7101", "선택": "오버", "배당": 1.82},
        ],
    }], "past": []}
    live_odds = _live_odds()
    live_odds["markets"]["102"] = {"7101": {
        **live_odds["markets"]["102"]["7101"], "label": "U 11.5",
    }}

    refreshed, changed = refresh_document(document, live_odds)

    game = refreshed["live"][0]
    assert changed == 1
    assert {option["line"] for option in game["options"]} == {11.5}
    assert game["market_line_changed"] is True
    assert game["market_line_revision"]["before"][0]["line"] == 10.5
    assert game["market_line_revision"]["after"][0]["line"] == 11.5
    assert game["decision_snapshot"]["action"] == "market_reference"
