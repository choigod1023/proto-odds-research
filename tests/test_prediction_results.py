import json

from src.generate_v2 import _attach_prediction_record, _recorded_predictions


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


def test_no_pregame_snapshot_does_not_invent_past_pick(tmp_path):
    path = tmp_path / "picks_v2.json"
    path.write_text(json.dumps({"live": [_game("정산", True)], "past": []},
                               ensure_ascii=False), encoding="utf-8")
    assert _recorded_predictions(path) == {}
