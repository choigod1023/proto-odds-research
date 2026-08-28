import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_decision import build_decision_snapshot, event_id  # noqa: E402
from prediction_ledger import (  # noqa: E402
    LedgerConflictError,
    LedgerCorruptionError,
    PredictionLedger,
    PredictionLedgerError,
)


UTC = timezone.utc
PREGAME_NOW = datetime(2026, 8, 28, 9, 30, tzinfo=UTC)
KICKOFF = "2026-08-28T19:00:00+09:00"
AS_OF = "2026-08-28T18:00:00+09:00"
MARKET_AT = "2026-08-28T17:59:00+09:00"


def _game(source_id: str = "kbo-20260828-01") -> dict:
    return {
        "source_event_id": source_id,
        "year": 2026,
        "round": 92,
        "date": "08.28(금) 19:00",
        "league": "KBO",
        "sport": "bs",
        "home": "서울",
        "away": "부산",
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
            },
        ],
    }


def _snapshot(game: dict, as_of: str = AS_OF) -> dict:
    return build_decision_snapshot(game, as_of=as_of, built_at=as_of)


def _features(source_id: str = "kbo-20260828-01") -> dict:
    return {
        "source_event_id": source_id,
        "elo_delta": 18.4,
        "lineup_status": "confirmed",
        "rest_days": {"home": 1, "away": 0},
    }


def _append(ledger: PredictionLedger, game: dict, snapshot: dict):
    return ledger.append_prediction(
        game,
        snapshot,
        kickoff=KICKOFF,
        market_observed_at=MARKET_AT,
        features=_features(game["source_event_id"]),
    )


def test_prediction_record_uses_ai_ids_and_captures_complete_pregame_contract(tmp_path):
    game = _game()
    snapshot = _snapshot(game)
    ledger = PredictionLedger(tmp_path / "predictions.jsonl", clock=lambda: PREGAME_NOW)

    result = _append(ledger, game, snapshot)

    assert result.appended is True
    record = result.record
    assert record["record_type"] == "prediction"
    assert record["event_id"] == event_id(game)
    assert record["snapshot_id"] == snapshot["decision_id"]
    assert record["decision_id"] == snapshot["decision_id"]
    assert record["as_of"] == "2026-08-28T09:00:00.000000Z"
    assert record["kickoff"] == "2026-08-28T10:00:00.000000Z"
    assert record["market_observed_at"] == "2026-08-28T08:59:00.000000Z"
    assert record["features"]["lineup_status"] == "confirmed"
    assert record["model"] == snapshot["model"]
    assert record["versions"]["operating_model"] == snapshot["model"]["operating_version"]
    assert record["predictions"]["probability"] == snapshot["probability"]
    assert len(record["evidence_hash"]) == 64
    assert len(record["record_hash"]) == 64
    assert ledger.records() == [record]


def test_identical_retry_is_idempotent_but_same_snapshot_with_new_content_conflicts(tmp_path):
    game = _game()
    snapshot = _snapshot(game)
    ledger = PredictionLedger(tmp_path / "predictions.jsonl", clock=lambda: PREGAME_NOW)

    first = _append(ledger, game, snapshot)
    retry = _append(ledger, game, snapshot)

    assert retry.appended is False
    assert retry.record == first.record
    assert len((tmp_path / "predictions.jsonl").read_text(encoding="utf-8").splitlines()) == 1

    changed = dict(ledger._prediction_payload(snapshot))
    changed["action"] = "withhold"
    with pytest.raises(LedgerConflictError, match="conflicting rewrite"):
        ledger.append_prediction(
            game,
            snapshot,
            kickoff=KICKOFF,
            market_observed_at=MARKET_AT,
            features=_features(),
            predictions=changed,
        )


def test_rejects_late_capture_and_invalid_information_times(tmp_path):
    game = _game()
    snapshot = _snapshot(game)
    after_kickoff = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
    ledger = PredictionLedger(tmp_path / "late.jsonl", clock=lambda: after_kickoff)

    with pytest.raises(PredictionLedgerError, match="at or after kickoff"):
        _append(ledger, game, snapshot)
    assert not (tmp_path / "late.jsonl").exists()

    valid_clock = PredictionLedger(tmp_path / "invalid-time.jsonl", clock=lambda: PREGAME_NOW)
    with pytest.raises(PredictionLedgerError, match="before kickoff"):
        valid_clock.append_prediction(
            game,
            snapshot,
            kickoff=AS_OF,
            market_observed_at=MARKET_AT,
            features=_features(),
        )
    with pytest.raises(PredictionLedgerError, match="market observation"):
        valid_clock.append_prediction(
            game,
            snapshot,
            kickoff=KICKOFF,
            market_observed_at="2026-08-28T18:01:00+09:00",
            features=_features(),
        )


def test_settlement_is_a_separate_idempotent_record_and_never_mutates_prediction(tmp_path):
    current = [PREGAME_NOW]
    game = _game()
    snapshot = _snapshot(game)
    ledger = PredictionLedger(tmp_path / "predictions.jsonl", clock=lambda: current[0])
    prediction = _append(ledger, game, snapshot).record
    current[0] = datetime(2026, 8, 28, 13, 0, tzinfo=UTC)

    settlement = ledger.append_settlement(
        snapshot["decision_id"],
        outcome={"winner": "home", "home_score": 5, "away_score": 2},
        settled_at="2026-08-28T21:45:00+09:00",
        source={"name": "official", "result_id": "result-01"},
    )
    retry = ledger.append_settlement(
        snapshot["decision_id"],
        outcome={"winner": "home", "home_score": 5, "away_score": 2},
        settled_at="2026-08-28T21:45:00+09:00",
        source={"name": "official", "result_id": "result-01"},
    )

    records = ledger.records()
    assert settlement.appended is True
    assert retry.appended is False
    assert [record["record_type"] for record in records] == ["prediction", "settlement"]
    assert records[0] == prediction
    assert records[1]["snapshot_id"] == prediction["snapshot_id"]
    assert records[1]["previous_record_hash"] == prediction["record_hash"]

    with pytest.raises(LedgerConflictError):
        ledger.append_settlement(
            snapshot["decision_id"],
            outcome={"winner": "away", "home_score": 2, "away_score": 5},
            settled_at="2026-08-28T21:45:00+09:00",
            source={"name": "official", "result_id": "result-01"},
        )


def test_settlement_cannot_be_recorded_before_prediction_kickoff(tmp_path):
    current = [PREGAME_NOW]
    game = _game()
    snapshot = _snapshot(game)
    ledger = PredictionLedger(tmp_path / "predictions.jsonl", clock=lambda: current[0])
    _append(ledger, game, snapshot)
    current[0] = datetime(2026, 8, 28, 11, 0, tzinfo=UTC)

    with pytest.raises(PredictionLedgerError, match="before kickoff"):
        ledger.append_settlement(
            snapshot["decision_id"],
            outcome={"winner": "home"},
            settled_at="2026-08-28T18:30:00+09:00",
            source={"name": "official"},
        )


def test_single_host_concurrent_refreshes_preserve_every_record_and_hash_chain(tmp_path):
    path = tmp_path / "concurrent.jsonl"
    inputs = []
    for index in range(12):
        game = _game(f"kbo-20260828-{index:02d}")
        inputs.append((game, _snapshot(game)))

    def capture(item):
        game, snapshot = item
        ledger = PredictionLedger(path, clock=lambda: PREGAME_NOW, lock_timeout=10)
        return _append(ledger, game, snapshot)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(capture, inputs))

    records = PredictionLedger(path).records()
    assert all(result.appended for result in results)
    assert len(records) == len(inputs)
    assert len({record["snapshot_id"] for record in records}) == len(inputs)
    assert [record["ledger_sequence"] for record in records] == list(range(1, 13))


def test_concurrent_same_revision_is_atomically_deduplicated(tmp_path):
    path = tmp_path / "same-revision.jsonl"
    game = _game()
    snapshots = [
        _snapshot(game, "2026-08-28T18:00:00+09:00"),
        _snapshot(game, "2026-08-28T18:01:00+09:00"),
    ]

    def capture(snapshot):
        ledger = PredictionLedger(path, clock=lambda: PREGAME_NOW, lock_timeout=10)
        return ledger.append_prediction(
            game,
            snapshot,
            kickoff=KICKOFF,
            market_observed_at=snapshot["as_of"],
            features=_features(),
            deduplication_key="same-observable-revision",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(capture, snapshots))

    assert sum(result.appended for result in results) == 1
    assert len(PredictionLedger(path).records()) == 1


def test_tampered_line_is_detected_before_another_append(tmp_path):
    game = _game()
    ledger = PredictionLedger(tmp_path / "predictions.jsonl", clock=lambda: PREGAME_NOW)
    _append(ledger, game, _snapshot(game))
    path = tmp_path / "predictions.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["features"]["elo_delta"] = 999
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(LedgerCorruptionError, match="record hash"):
        ledger.records()
