import json
from types import SimpleNamespace

import pytest

from src import generate_v2
from src.generate_v2 import (
    _attach_prediction_record,
    _recorded_predictions,
    _sync_prediction_runtime,
)
from src.prediction_ledger import PredictionLedgerError


def test_empty_source_path_still_records_every_future_prediction(tmp_path, monkeypatch):
    future = {"event_id": "future", "year": 2099, "date": "09.02(수) 18:00", "status": "경기전"}
    started = {"event_id": "started", "year": 2000, "date": "09.02(수) 18:00", "status": "경기전"}
    (tmp_path / "picks_v2.json").write_text(
        json.dumps({"live": [future, started], "past": []}), encoding="utf-8",
    )
    seen = []
    monkeypatch.setattr(generate_v2, "OUT", tmp_path)
    monkeypatch.setattr(generate_v2, "database_enabled", lambda: False)
    monkeypatch.setattr(generate_v2, "enrich_existing", lambda doc, store: (doc, 0))
    monkeypatch.setattr(generate_v2, "_sync_prediction_runtime",
                        lambda runtime, games, observed_at: seen.extend(games) or {})
    monkeypatch.setattr(generate_v2, "_attach_prediction_record", lambda game, records: None)
    monkeypatch.setattr(generate_v2, "persist_artifact", lambda *args: None)
    monkeypatch.setattr(generate_v2.commentary_llm, "flush", lambda: None)
    runtime = SimpleNamespace(ui_records=lambda: {})

    assert generate_v2._enrich_published_only(object(), runtime) == 0
    assert [game["event_id"] for game in seen] == ["future"]


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
            # generate_v2 가 실제로 잡는 클래스로 던진다(테스트 환경 모듈 이중 로드 회피).
            raise generate_v2.PredictionLedgerError("write failed")

    game = _game("경기전")
    game.update({
        "year": 2026, "round": 90, "league": "KBO",
        "home": "홈", "away": "원정",
        "decision_snapshot": {"action": "market_reference", "decision_id": "dec-new"},
    })

    with pytest.raises(generate_v2.PredictionLedgerError, match="write failed"):
        _sync_prediction_runtime(
            BrokenRuntime(), [game], observed_at="2026-08-28T08:00:00+00:00",
        )


def test_ledger_conflict_withholds_only_that_game_and_keeps_publishing():
    """한 경기의 원장 충돌이 발행 전체를 중단시키지 않는다 (2026-09-04 장애)."""
    class Runtime:
        def record_pregame(self, game, *, kickoff, market_observed_at):
            if game["home"] == "충돌홈":
                # generate_v2 가 실제로 잡는 클래스 참조로 던진다(모듈 이중 로드 회피).
                raise generate_v2.LedgerConflictError(
                    "conflicting rewrite for prediction dec_685b5e08bd5466b9")
            return SimpleNamespace(appended=True, record={
                "snapshot_id": game["decision_snapshot"]["decision_id"]})

        def records(self):
            return [{"record_type": "prediction", "snapshot_id": "dec-ok"}]

        def ui_records(self):
            return {"evt_ok": {"prediction_snapshot_id": "dec-ok",
                               "selection_id": "sel_home", "result": "pending"}}

    bad = _game("경기전")
    bad.update({"event_id": "evt_bad", "year": 2026, "round": 90, "league": "KBO",
                "home": "충돌홈", "away": "원정",
                "decision_snapshot": {"action": "market_reference",
                                      "decision_id": "dec_685b5e08bd5466b9",
                                      "as_of": "2026-08-28T07:00:00+00:00"}})
    ok = _game("경기전")
    ok.update({"event_id": "evt_ok", "year": 2026, "round": 90, "league": "KBO",
               "home": "정상홈", "away": "정상원정",
               "decision_snapshot": {"action": "market_reference",
                                     "decision_id": "dec-ok",
                                     "as_of": "2026-08-28T07:00:00+00:00"}})

    counts = _sync_prediction_runtime(
        Runtime(), [bad, ok], observed_at="2026-08-28T08:00:00+00:00")

    # 충돌 경기는 가격만 남고 사전 픽은 보류된다.
    assert "decision_snapshot" not in bad
    assert bad["prediction_status"] == "prediction_ledger_required"
    assert bad["prediction_ledger_reason"] == "ledger_conflict"
    assert counts["withheld"] >= 1
    # 정상 경기는 그대로 기록되어 발행이 계속된다.
    assert ok["prediction_status"] == "recorded_pregame"


def test_preserved_prediction_uses_snapshot_time_for_market_observation():
    class Runtime:
        def __init__(self):
            self.market_observed_at = None

        def record_pregame(self, game, *, kickoff, market_observed_at):
            self.market_observed_at = market_observed_at
            return SimpleNamespace(appended=True, record={
                "snapshot_id": game["decision_snapshot"]["decision_id"],
            })

        def ui_records(self):
            return {"evt_1": {
                "prediction_snapshot_id": "dec-preserved",
                "selection_id": "sel_home",
                "result": "pending",
            }}

    runtime = Runtime()
    game = _game("경기전")
    game["decision_snapshot"] = {
        "action": "market_reference",
        "decision_id": "dec-preserved",
        "as_of": "2026-08-28T07:00:00+00:00",
    }

    _sync_prediction_runtime(
        runtime, [game], observed_at="2026-08-28T08:00:00+00:00",
    )

    assert runtime.market_observed_at == "2026-08-28T07:00:00+00:00"


def test_empty_decision_snapshot_withholds_only_that_game():
    """스냅샷 생성 실패로 빈 {} 가 들어와도 발행 전체가 멈추지 않는다 (2026-09-04)."""
    class Runtime:
        def record_pregame(self, game, *, kickoff, market_observed_at):
            return SimpleNamespace(appended=True, record={
                "snapshot_id": game["decision_snapshot"]["decision_id"]})

        def records(self):
            return [{"record_type": "prediction", "snapshot_id": "dec-ok"}]

        def ui_records(self):
            return {"evt_ok": {"prediction_snapshot_id": "dec-ok",
                               "selection_id": "sel_home", "result": "pending"}}

    broken = _game("경기전")
    broken.update({"event_id": "evt_broken", "year": 2026, "round": 90,
                   "league": "KBO", "home": "빈스냅홈", "away": "원정",
                   "decision_snapshot": {}})
    ok = _game("경기전")
    ok.update({"event_id": "evt_ok", "year": 2026, "round": 90, "league": "KBO",
               "home": "정상홈", "away": "정상원정",
               "decision_snapshot": {"action": "market_reference",
                                     "decision_id": "dec-ok",
                                     "as_of": "2026-08-28T07:00:00+00:00"}})

    counts = _sync_prediction_runtime(
        Runtime(), [broken, ok], observed_at="2026-08-28T08:00:00+00:00")

    assert broken["prediction_status"] == "prediction_ledger_required"
    assert broken["prediction_ledger_reason"] == "invalid_decision_snapshot"
    assert counts["withheld"] >= 1
    assert ok["prediction_status"] == "recorded_pregame"
