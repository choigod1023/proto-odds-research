import json
from types import SimpleNamespace

import pytest

from src.generate_v2 import (
    _attach_prediction_record,
    _recorded_predictions,
    _sync_prediction_runtime,
)
from src.prediction_ledger import PredictionLedgerError


def _game(status="경기전", hit=None):
    return {
        "event_id": "evt_1", "status": status, "date": "08.28(금) 18:00",
        "options": [{
            "selection_id": "sel_home", "offer_id": "off_home",
            "market": "승패", "label": "", "선택": "홈", "배당": 1.7,
            "적중": hit,
        }],
    }


def test_pregame_recommendation_is_preserved_and_settled(tmp_path):
    game = _game()
    game["추천"] = game["options"][0]
    game["decision_snapshot"] = {
        "action": "market_reference", "as_of": "2026-08-28T08:00:00+00:00",
        "probability": {"final": .61},
    }
    path = tmp_path / "picks_v2.json"
    path.write_text(json.dumps({"generated_at": "2026-08-28T08:00:00+00:00",
                                "live": [game], "past": []}, ensure_ascii=False),
                    encoding="utf-8")

    records = _recorded_predictions(path)
    settled = _game("정산", True)
    _attach_prediction_record(settled, records)

    assert settled["prediction_record"]["result"] == "hit"
    assert settled["prediction_record"]["selection"] == "홈"
    assert settled["prediction_record"]["probability"] == .61


def test_existing_record_survives_later_generation(tmp_path):
    game = _game("정산", False)
    game["prediction_record"] = {
        "selection_id": "sel_home", "selection": "홈", "result": "miss",
        "captured_at": "2026-08-27T08:00:00+00:00",
    }
    path = tmp_path / "picks_v2.json"
    path.write_text(json.dumps({"live": [], "past": [game]}, ensure_ascii=False),
                    encoding="utf-8")

    assert _recorded_predictions(path)["evt_1"]["result"] == "miss"


def test_ledger_settlement_is_not_overwritten_by_current_reconstruction():
    settled = _game("정산", True)
    records = {
        "evt_1": {
            "prediction_snapshot_id": "dec_1",
            "selection_id": "sel_home",
            "selection": "홈",
            "result": "miss",
            "settled_at": "2026-08-28T12:00:00Z",
        },
    }

    _attach_prediction_record(settled, records)

    assert settled["prediction_record"]["result"] == "miss"
    assert settled["prediction_record"]["settled_at"] == "2026-08-28T12:00:00Z"


def test_ledger_settlement_survives_result_confirmation_state():
    confirming = _game("결과확인", True)
    records = {
        "evt_1": {
            "prediction_snapshot_id": "dec_1",
            "selection_id": "sel_home",
            "selection": "홈",
            "result": "hit",
            "settled_at": "2026-08-28T12:00:00Z",
        },
    }

    _attach_prediction_record(confirming, records)

    assert confirming["prediction_record"]["result"] == "hit"
    assert confirming["prediction_record"]["settled_at"] == "2026-08-28T12:00:00Z"


def test_result_confirmation_with_official_hit_value_is_settled():
    class Runtime:
        def __init__(self):
            self.calls = []

        def ui_records(self):
            return {"evt_1": {
                "prediction_snapshot_id": "dec_1",
                "selection_id": "sel_home",
                "result": "pending",
            }}

        def settle_latest(self, event_id, **kwargs):
            self.calls.append((event_id, kwargs))
            return SimpleNamespace(appended=True)

    runtime = Runtime()
    game = _game("결과확인", True)

    counts = _sync_prediction_runtime(
        runtime, [game], observed_at="2026-08-28T12:00:00+00:00",
    )

    assert counts["settlements"] == 1
    assert runtime.calls[0][0] == "evt_1"
    assert runtime.calls[0][1]["outcome"]["result"] == "hit"


def test_no_pregame_snapshot_does_not_invent_past_pick(tmp_path):
    path = tmp_path / "picks_v2.json"
    path.write_text(json.dumps({"live": [_game("정산", True)], "past": []},
                               ensure_ascii=False), encoding="utf-8")
    assert _recorded_predictions(path) == {}


def test_live_record_must_match_exact_decision_revision():
    game = _game("경기전")
    game["추천"] = game["options"][0]
    game["decision_snapshot"] = {
        "action": "market_reference", "decision_id": "dec-new",
    }
    records = {"evt_1": {
        "prediction_snapshot_id": "dec-old", "selection_id": "sel_home",
        "result": "pending",
    }}

    _attach_prediction_record(game, records)

    assert game["prediction_status"] == "prediction_ledger_required"
    assert game["prediction_ledger_reason"] == "prediction_revision_mismatch"
    assert game["추천"] is None
    assert "decision_snapshot" not in game
    assert "prediction_record" not in game


def test_full_generation_aborts_when_pregame_ledger_append_fails():
    class BrokenRuntime:
        def record_pregame(self, *args, **kwargs):
            raise PredictionLedgerError("write failed")

    game = _game("경기전")
    game.update({
        "year": 2026, "round": 90, "league": "KBO",
        "home": "홈", "away": "원정",
        "decision_snapshot": {"action": "market_reference", "decision_id": "dec-new"},
    })

    with pytest.raises(PredictionLedgerError, match="write failed"):
        _sync_prediction_runtime(
            BrokenRuntime(), [game], observed_at="2026-08-28T08:00:00+00:00",
        )
