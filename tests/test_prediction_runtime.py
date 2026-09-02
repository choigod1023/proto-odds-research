from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_decision import build_decision_snapshot  # noqa: E402
from prediction_ledger import LedgerConflictError  # noqa: E402
from prediction_runtime import (  # noqa: E402
    PredictionRuntime,
    attach_score_forecast,
    kickoff_utc,
    ledger_features,
    tally_prediction_records,
)


UTC = timezone.utc


def game() -> dict:
    return {
        "year": 2026,
        "round": 92,
        "date": "08.28(금) 19:00",
        "league": "KBO",
        "sport": "bs",
        "home": "서울",
        "away": "부산",
        "lam_home": 4.7,
        "lam_away": 3.9,
        "lam_src": "league/team blend",
        "form_home": {"last10": "7승 3패"},
        "form_away": {"last10": "4승 6패"},
        "선발": {
            "home": "김선발",
            "away": "박선발",
            "updated_at": "2026-08-28T17:00:00+09:00",
            "unavailable": {"home": [{"name": "이부상"}], "away": []},
        },
        "options": [
            {
                "market": "승패",
                "label": "",
                "line": None,
                "게임번호": "10",
                "선택": "승",
                "배당": 1.65,
                "시장확률": 0.61,
                "모델확률": 0.64,
                "적중": None,
            },
            {
                "market": "승패",
                "label": "",
                "line": None,
                "게임번호": "10",
                "선택": "패",
                "배당": 2.05,
                "시장확률": 0.39,
                "모델확률": 0.36,
                "적중": None,
            },
        ],
    }


def snapshot(value: dict, as_of: str) -> None:
    value["decision_snapshot"] = build_decision_snapshot(
        value, as_of=as_of, built_at=as_of
    )


def test_naive_generator_kickoff_is_localized_as_kst():
    naive = pd.Timestamp("2026-08-28 19:00")
    assert kickoff_utc(naive) == "2026-08-28T10:00:00+00:00"


def test_score_forecast_is_compact_json_and_never_affects_probability():
    value = game()
    payload = attach_score_forecast(value)

    assert payload["status"] == "shadow"
    assert payload["affects_probability"] is False
    assert payload["expected_scores"]["unit"] == "runs"
    assert len(payload["top_scorelines"]) == 3
    assert "probability_matrix" not in payload
    json.dumps(payload, allow_nan=False)

    features = ledger_features(value)
    assert features["player_context"]["starters_and_availability"]["home"] == "김선발"
    json.dumps(features, allow_nan=False)


def test_unknown_sport_makes_only_score_forecast_unavailable():
    value = game()
    value["sport"] = "unknown"
    payload = attach_score_forecast(value)
    assert payload["status"] == "unavailable"
    assert payload["affects_probability"] is False


def test_cron_deduplicates_same_revision_but_appends_changed_odds(tmp_path):
    current = [datetime(2026, 8, 28, 9, 5, tzinfo=UTC)]
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl", clock=lambda: current[0]
    )
    value = game()
    attach_score_forecast(value)
    snapshot(value, "2026-08-28T09:00:00+00:00")
    first = runtime.record_pregame(
        value,
        kickoff="2026-08-28T10:00:00+00:00",
        market_observed_at="2026-08-28T09:00:00+00:00",
    )

    current[0] = datetime(2026, 8, 28, 9, 25, tzinfo=UTC)
    snapshot(value, "2026-08-28T09:20:00+00:00")
    duplicate = runtime.record_pregame(
        value,
        kickoff="2026-08-28T10:00:00+00:00",
        market_observed_at="2026-08-28T09:20:00+00:00",
    )

    value["options"][0]["배당"] = 1.60
    value["options"][0]["시장확률"] = 0.63
    current[0] = datetime(2026, 8, 28, 9, 35, tzinfo=UTC)
    snapshot(value, "2026-08-28T09:30:00+00:00")
    changed = runtime.record_pregame(
        value,
        kickoff="2026-08-28T10:00:00+00:00",
        market_observed_at="2026-08-28T09:30:00+00:00",
    )

    assert first is not None and first.appended
    assert duplicate is None
    assert changed is not None and changed.appended
    assert len([r for r in runtime.records() if r["record_type"] == "prediction"]) == 2


def test_refresh_reads_complete_ledger_only_once(tmp_path, monkeypatch):
    current = [datetime(2026, 8, 28, 9, 5, tzinfo=UTC)]
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl", clock=lambda: current[0]
    )
    reads = 0
    original_records = runtime.ledger.records

    def counted_records():
        nonlocal reads
        reads += 1
        return original_records()

    monkeypatch.setattr(runtime.ledger, "records", counted_records)
    value = game()
    attach_score_forecast(value)
    snapshot(value, "2026-08-28T09:00:00+00:00")

    runtime.record_pregame(
        value,
        kickoff="2026-08-28T10:00:00+00:00",
        market_observed_at="2026-08-28T09:00:00+00:00",
    )
    current[0] = datetime(2026, 8, 28, 9, 25, tzinfo=UTC)
    snapshot(value, "2026-08-28T09:20:00+00:00")
    runtime.record_pregame(
        value,
        kickoff="2026-08-28T10:00:00+00:00",
        market_observed_at="2026-08-28T09:20:00+00:00",
    )
    runtime.ui_records()

    assert reads == 1


def test_latest_pregame_revision_is_settled_idempotently_and_tallied(tmp_path):
    current = [datetime(2026, 8, 28, 9, 5, tzinfo=UTC)]
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl", clock=lambda: current[0]
    )
    value = game()
    attach_score_forecast(value)
    snapshot(value, "2026-08-28T09:00:00+00:00")
    runtime.record_pregame(
        value,
        kickoff="2026-08-28T10:00:00+00:00",
        market_observed_at="2026-08-28T09:00:00+00:00",
    )
    event_id = value["event_id"]

    current[0] = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    first = runtime.settle_latest(
        event_id,
        outcome={"result": "hit", "score": [5, 2]},
        settled_at="2026-08-28T11:55:00+00:00",
        source={"name": "proto_official", "round": 92},
    )
    current[0] = datetime(2026, 8, 28, 12, 30, tzinfo=UTC)
    duplicate = runtime.settle_latest(
        event_id,
        outcome={"result": "hit", "score": [5, 2]},
        settled_at="2026-08-28T12:25:00+00:00",
        source={"name": "proto_official", "round": 92},
    )

    assert first is not None and first.appended
    assert duplicate is None
    records = runtime.ui_records()
    assert records[event_id]["result"] == "hit"
    assert records[event_id]["score_forecast"]["status"] == "shadow"
    tally = tally_prediction_records(records)
    assert tally["n"] == 1
    assert tally["wins"] == 1
    assert tally["roi"] == pytest.approx(0.65)

    correction = runtime.settle_latest(
        event_id,
        outcome={"result": "miss", "score": [2, 5]},
        settled_at="2026-08-28T12:25:00+00:00",
        source={"name": "proto_official", "round": 92},
    )
    assert correction is not None and correction.appended
    assert runtime.ui_records()[event_id]["result"] == "miss"


def test_older_job_is_rejected_if_it_finishes_after_a_newer_revision(tmp_path):
    current = [datetime(2026, 8, 28, 9, 25, tzinfo=UTC)]
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl", clock=lambda: current[0]
    )
    newer = game()
    newer["options"][0]["배당"] = 1.60
    newer["options"][0]["시장확률"] = 0.63
    attach_score_forecast(newer)
    snapshot(newer, "2026-08-28T09:20:00+00:00")
    runtime.record_pregame(
        newer,
        kickoff="2026-08-28T10:00:00+00:00",
        market_observed_at="2026-08-28T09:20:00+00:00",
    )

    older = copy.deepcopy(game())
    attach_score_forecast(older)
    snapshot(older, "2026-08-28T09:10:00+00:00")
    current[0] = datetime(2026, 8, 28, 9, 30, tzinfo=UTC)
    with pytest.raises(LedgerConflictError, match="as_of regressed"):
        runtime.record_pregame(
            older,
            kickoff="2026-08-28T10:00:00+00:00",
            market_observed_at="2026-08-28T09:10:00+00:00",
        )

    record = runtime.ui_records()[newer["event_id"]]
    assert record["odds"] == 1.60


def test_revision_guard_compares_timezone_offsets_by_utc_instant(tmp_path):
    current = [datetime(2026, 8, 28, 9, 25, tzinfo=UTC)]
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl", clock=lambda: current[0]
    )
    newer = game()
    newer["options"][0]["배당"] = 1.60
    newer["options"][0]["시장확률"] = 0.63
    attach_score_forecast(newer)
    snapshot(newer, "2026-08-28T09:20:00+00:00")
    runtime.record_pregame(
        newer,
        kickoff="2026-08-28T10:00:00+00:00",
        market_observed_at="2026-08-28T09:20:00+00:00",
    )

    older = copy.deepcopy(game())
    attach_score_forecast(older)
    snapshot(older, "2026-08-28T17:10:00+09:00")  # 08:10Z
    current[0] = datetime(2026, 8, 28, 9, 30, tzinfo=UTC)
    with pytest.raises(LedgerConflictError, match="as_of regressed"):
        runtime.record_pregame(
            older,
            kickoff="2026-08-28T19:00:00+09:00",
            market_observed_at="2026-08-28T17:10:00+09:00",
        )

    assert runtime.ui_records()[newer["event_id"]]["odds"] == 1.60
