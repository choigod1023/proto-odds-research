"""Runtime glue for score forecasts and the immutable pregame ledger.

The low-level modules deliberately stay reusable. This module defines the
operational revision rule: a cron refresh only appends when the observable
inputs or published decision changed, and the last revision before kickoff is
the one shown and settled.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from prediction_ledger import AppendResult, PredictionLedger
from score_scenarios import ScoreForecastError, forecast_from_lambdas


KST = ZoneInfo("Asia/Seoul")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _safe_json(value: Any) -> Any:
    """Convert known data-frame/numpy scalars without admitting NaN."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=KST).astimezone(timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _safe_json(item())
        except (TypeError, ValueError):
            pass
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return str(isoformat())
        except (TypeError, ValueError):
            pass
    return str(value)


def kickoff_utc(value: Any) -> str:
    """Treat a naive generator timestamp as KST and return UTC ISO-8601."""

    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise ValueError("kickoff must be a datetime-like value")
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=KST)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def attach_score_forecast(game: dict[str, Any]) -> dict[str, Any]:
    """Attach a compact, JSON-safe shadow forecast without changing the pick."""

    home = game.get("lam_home")
    away = game.get("lam_away")
    if home is None or away is None:
        payload = {
            "status": "unavailable",
            "affects_probability": False,
            "reason": "score_model_unavailable",
        }
        game["score_forecast"] = payload
        return payload
    try:
        forecast = forecast_from_lambdas(
            str(game.get("sport") or ""),
            float(home),
            float(away),
            top_n=3,
        )
    except (ScoreForecastError, TypeError, ValueError) as exc:
        payload = {
            "status": "unavailable",
            "affects_probability": False,
            "reason": "unsupported_or_invalid_score_model",
            "detail": str(exc),
        }
        game["score_forecast"] = payload
        return payload

    payload = {
        "status": "shadow",
        "affects_probability": False,
        "source": game.get("lam_src"),
        "scenario_status": "baseline_only",
        "scenario_note": (
            "선발·결장 영향량을 검증하기 전이라 현재는 기본 스코어 분포만 표시합니다."
        ),
        **_safe_json(forecast.to_dict(include_matrix=False)),
    }
    game["score_forecast"] = payload
    return payload


def ledger_features(game: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze model inputs and explanatory player/team context seen pregame."""

    options = [
        {
            "selection_id": row.get("selection_id"),
            "offer_id": row.get("offer_id"),
            "market": row.get("market"),
            "label": row.get("label"),
            "line": row.get("line"),
            "selection": row.get("선택"),
            "odds": row.get("배당"),
            "market_probability": row.get("시장확률"),
            "score_model_probability": row.get("모델확률"),
            "residual_model_probability": row.get("잔차모델확률"),
            "final_probability": row.get("최종확률"),
        }
        for row in game.get("options", [])
    ]
    return _safe_json({
        "score_inputs": {
            "home": game.get("lam_home"),
            "away": game.get("lam_away"),
            "source": game.get("lam_src"),
        },
        "score_forecast": game.get("score_forecast"),
        "team_context": {
            "home_form": game.get("form_home"),
            "away_form": game.get("form_away"),
            "head_to_head": game.get("h2h"),
            "market_context": game.get("시장문맥"),
        },
        "player_context": {
            "starters_and_availability": game.get("선발"),
            "lineup_profile": game.get("라인업"),
            "lineup_note": game.get("라인업메모"),
        },
        "options": options,
    })


def prediction_payload(game: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = game.get("decision_snapshot") or {}
    selected = next((
        row for row in game.get("options", [])
        if row.get("selection_id") == snapshot.get("selection_id")
    ), None)
    return _safe_json({
        "action": snapshot.get("action"),
        "selection_id": snapshot.get("selection_id"),
        "offer_id": snapshot.get("offer_id"),
        "market": selected.get("market") if selected else None,
        "label": selected.get("label") if selected else None,
        "selection": selected.get("선택") if selected else None,
        "odds": selected.get("배당") if selected else None,
        "probability": (snapshot.get("probability") or {}).get("final"),
        "probability_detail": snapshot.get("probability") or {},
        "gate_codes": snapshot.get("gate_codes") or [],
        "score_forecast": game.get("score_forecast"),
    })


def _latest_predictions(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    def instant(value: object) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("record_type") == "prediction":
            current = latest.get(record["event_id"])
            order = (instant(record.get("as_of")), int(record.get("ledger_sequence") or 0))
            current_order = (
                instant(current.get("as_of")),
                int(current.get("ledger_sequence") or 0),
            ) if current else (datetime.min.replace(tzinfo=timezone.utc), 0)
            if current is None or order >= current_order:
                latest[record["event_id"]] = record
    return latest


def _revision_signature(
    *,
    event_id: str,
    input_revision_hash: str | None,
    predictions: Mapping[str, Any],
    model: Mapping[str, Any],
    features: Mapping[str, Any],
) -> str:
    probability = predictions.get("probability_detail") or predictions.get("probability") or {}
    if not isinstance(probability, Mapping):
        probability = {"final": probability}
    payload = {
        "event_id": event_id,
        "input_revision_hash": input_revision_hash,
        "action": predictions.get("action"),
        "selection_id": predictions.get("selection_id"),
        "offer_id": predictions.get("offer_id"),
        "final_probability": probability.get("final"),
        "artifact_hash": model.get("artifact_hash"),
        "features_hash": hashlib.sha256(
            _canonical_json(features).encode("utf-8")
        ).hexdigest(),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class PredictionRuntime:
    """Revision-aware facade over PredictionLedger."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ledger = PredictionLedger(path, clock=clock)
        # A refresh can evaluate hundreds of markets. Reading and verifying the
        # complete JSONL hash chain for every game made that loop quadratic and
        # kept picks_v2.json stale for many minutes. A runtime is single-use in
        # the generators, so load the immutable ledger once and extend the
        # in-memory view after successful appends.
        self._record_cache: list[dict[str, Any]] | None = None
        self._latest_cache: dict[str, dict[str, Any]] | None = None

    def _cached_records(self) -> list[dict[str, Any]]:
        if self._record_cache is None:
            self._record_cache = self.ledger.records()
        return self._record_cache

    def _cached_latest(self) -> dict[str, dict[str, Any]]:
        if self._latest_cache is None:
            self._latest_cache = _latest_predictions(self._cached_records())
        return self._latest_cache

    def _remember(self, result: AppendResult) -> None:
        if not result.appended:
            return
        self._cached_records().append(result.record)
        if result.record.get("record_type") == "prediction":
            self._cached_latest()[result.record["event_id"]] = result.record

    def records(self) -> list[dict[str, Any]]:
        return list(self._cached_records())

    def record_pregame(
        self,
        game: Mapping[str, Any],
        *,
        kickoff: str | datetime,
        market_observed_at: str | datetime,
    ) -> AppendResult | None:
        snapshot = game.get("decision_snapshot") or {}
        predictions = prediction_payload(game)
        features = ledger_features(game)
        existing = self._cached_latest().get(snapshot.get("event_id"))
        new_signature = _revision_signature(
            event_id=str(snapshot.get("event_id") or ""),
            input_revision_hash=snapshot.get("input_revision_hash"),
            predictions=predictions,
            model=snapshot.get("model") or {},
            features=features,
        )
        if existing is not None:
            old_signature = _revision_signature(
                event_id=existing["event_id"],
                input_revision_hash=existing.get("input_revision_hash"),
                predictions=existing.get("predictions") or {},
                model=existing.get("model") or {},
                features=existing.get("features") or {},
            )
            if old_signature == new_signature:
                return None
        result = self.ledger.append_prediction(
            game,
            snapshot,
            kickoff=kickoff,
            market_observed_at=market_observed_at,
            features=features,
            predictions=predictions,
            deduplication_key=new_signature,
        )
        self._remember(result)
        return result

    def record_pregame_batch(
        self,
        entries: list[Mapping[str, Any]],
    ) -> list[AppendResult | None]:
        """Record a live refresh batch with one ledger verification/write.

        Results are aligned with ``entries``; an unchanged observable revision
        remains ``None`` just like :meth:`record_pregame`.
        """
        results: list[AppendResult | None] = [None] * len(entries)
        pending: list[dict[str, Any]] = []
        indexes: list[int] = []
        latest = self._cached_latest()
        for index, entry in enumerate(entries):
            game = entry["game"]
            snapshot = game.get("decision_snapshot") or {}
            predictions = prediction_payload(game)
            features = ledger_features(game)
            new_signature = _revision_signature(
                event_id=str(snapshot.get("event_id") or ""),
                input_revision_hash=snapshot.get("input_revision_hash"),
                predictions=predictions,
                model=snapshot.get("model") or {},
                features=features,
            )
            existing = latest.get(snapshot.get("event_id"))
            if existing is not None:
                old_signature = _revision_signature(
                    event_id=existing["event_id"],
                    input_revision_hash=existing.get("input_revision_hash"),
                    predictions=existing.get("predictions") or {},
                    model=existing.get("model") or {},
                    features=existing.get("features") or {},
                )
                if old_signature == new_signature:
                    continue
            pending.append({
                "game": game,
                "decision_snapshot": snapshot,
                "kickoff": entry["kickoff"],
                "market_observed_at": entry["market_observed_at"],
                "features": features,
                "predictions": predictions,
                "deduplication_key": new_signature,
            })
            indexes.append(index)

        appended = self.ledger.append_predictions(pending)
        for index, result in zip(indexes, appended):
            results[index] = result
            self._remember(result)
        return results

    def settle_latest(
        self,
        event_id: str,
        *,
        outcome: Mapping[str, Any],
        settled_at: str | datetime,
        source: Mapping[str, Any] | str,
    ) -> AppendResult | None:
        records = self._cached_records()
        prediction = self._cached_latest().get(event_id)
        if prediction is None:
            return None
        normalized_outcome = _safe_json(outcome)
        version_hash = hashlib.sha256(
            _canonical_json(normalized_outcome).encode("utf-8")
        ).hexdigest()[:16]
        settlement_version = f"official-{version_hash}"
        if any(
            row.get("record_type") == "settlement"
            and row.get("snapshot_id") == prediction["snapshot_id"]
            and row.get("settlement_version") == settlement_version
            for row in records
        ):
            return None
        result = self.ledger.append_settlement(
            prediction["snapshot_id"],
            outcome=normalized_outcome,
            settled_at=settled_at,
            source=_safe_json(source),
            settlement_version=settlement_version,
        )
        self._remember(result)
        return result

    def ui_records(self) -> dict[str, dict[str, Any]]:
        records = self.records()
        latest = _latest_predictions(records)
        settlements: dict[str, dict[str, Any]] = {}
        for row in records:
            if row.get("record_type") == "settlement":
                settlements[row["snapshot_id"]] = row
        result: dict[str, dict[str, Any]] = {}
        for event_id, prediction in latest.items():
            payload = prediction.get("predictions") or {}
            if payload.get("action") != "market_reference":
                continue
            settlement = settlements.get(prediction["snapshot_id"])
            outcome = (settlement or {}).get("outcome") or {}
            result[event_id] = {
                "prediction_snapshot_id": prediction["snapshot_id"],
                "selection_id": payload.get("selection_id"),
                "offer_id": payload.get("offer_id"),
                "market": payload.get("market"),
                "label": payload.get("label") or "",
                "selection": payload.get("selection"),
                "odds": payload.get("odds"),
                "probability": payload.get("probability"),
                "captured_at": prediction.get("captured_at"),
                "result": outcome.get("result") or "pending",
                "score_forecast": payload.get("score_forecast"),
                **({"settled_at": settlement.get("settled_at")} if settlement else {}),
            }
        return result


def tally_prediction_records(records: Mapping[str, Mapping[str, Any]]) -> dict | None:
    settled = [row for row in records.values() if row.get("result") in {"hit", "miss"}]
    if not settled:
        return None
    wins = sum(row.get("result") == "hit" for row in settled)
    priced = [
        row for row in settled
        if isinstance(row.get("odds"), (int, float))
        and math.isfinite(float(row["odds"]))
        and float(row["odds"]) > 1.0
    ]
    profits = [
        float(row["odds"]) - 1.0 if row["result"] == "hit" else -1.0
        for row in priced
    ]
    return {
        "n": len(settled),
        "wins": wins,
        "hit_rate": wins / len(settled),
        "priced_n": len(priced),
        "average_odds": (
            sum(float(row["odds"]) for row in priced) / len(priced)
            if priced else None
        ),
        "roi": sum(profits) / len(profits) if profits else None,
    }


__all__ = [
    "PredictionRuntime",
    "attach_score_forecast",
    "kickoff_utc",
    "ledger_features",
    "prediction_payload",
    "tally_prediction_records",
]
