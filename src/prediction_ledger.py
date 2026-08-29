"""Append-only pregame prediction ledger.

The ledger is deliberately independent of the prediction implementation.  It
persists the already validated :mod:`ai_decision` snapshot together with the
exact feature cutoff and model inputs that were available before kickoff.
JSONL keeps the artifact easy to inspect while an exclusive lock file and a
hash chain make concurrent single-host refreshes safe and detectable.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from ai_decision import event_id as decision_event_id
from ai_decision import validate_decision_snapshot


SCHEMA_VERSION = "pregame-prediction-ledger-v1"


class PredictionLedgerError(ValueError):
    """Base class for ledger validation failures."""


class LedgerConflictError(PredictionLedgerError):
    """An existing immutable identity was presented with different content."""


class LedgerCorruptionError(PredictionLedgerError):
    """The JSONL file or its hash chain is invalid."""


class LedgerLockTimeout(TimeoutError):
    """Another refresh held the ledger lock longer than allowed."""


@dataclass(frozen=True)
class AppendResult:
    """Result of an idempotent append operation."""

    record: dict[str, Any]
    appended: bool


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PredictionLedgerError(f"ledger value is not canonical JSON: {exc}") from exc


def _json_copy(value: Any) -> Any:
    """Detach caller-owned dictionaries and reject non-JSON values."""
    return json.loads(_canonical_json(value))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _parse_time(value: str | datetime, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise PredictionLedgerError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PredictionLedgerError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _format_time(value: str | datetime, field: str) -> str:
    parsed = _parse_time(value, field)
    text = parsed.isoformat(timespec="microseconds")
    return text.replace("+00:00", "Z")


def _identity(record: Mapping[str, Any]) -> tuple[str, str]:
    kind = record.get("record_type")
    if kind == "prediction":
        identity = record.get("snapshot_id")
    elif kind == "settlement":
        identity = record.get("settlement_id")
    else:
        raise LedgerCorruptionError(f"unknown ledger record_type: {kind!r}")
    if not isinstance(identity, str) or not identity:
        raise LedgerCorruptionError(f"missing identity for {kind!r} record")
    return kind, identity


def _immutable_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """Content used to decide whether a retry is identical.

    Append metadata is assigned only by the first successful writer, so it is
    intentionally excluded from idempotency comparisons.
    """
    ignored = {
        "captured_at",
        "ledger_sequence",
        "previous_record_hash",
        "record_hash",
    }
    return {key: value for key, value in record.items() if key not in ignored}


class PredictionLedger:
    """Single-host append-only JSONL ledger with idempotent identities."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        lock_timeout: float = 5.0,
        stale_lock_after: float = 120.0,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock_timeout = float(lock_timeout)
        self.stale_lock_after = float(stale_lock_after)
        if self.lock_timeout < 0 or self.stale_lock_after <= 0:
            raise ValueError("lock timeouts must be positive")
        self._database = None
        if os.environ.get("PROODD_DB_PATH"):
            from runtime_db import RuntimeDatabase
            self._database = RuntimeDatabase()
            database_records = self._database.prediction_records()
            if database_records:
                # 운영 DB가 원본이다. checkout 교체나 중단된 파일 쓰기 뒤에도
                # 기존 분석 코드가 읽는 JSONL export를 자동 복원한다.
                payload = "".join(_canonical_json(row) + "\n" for row in database_records)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(self.path.suffix + ".db-export.tmp")
                temporary.write_text(payload, encoding="utf-8")
                os.replace(temporary, self.path)
            elif self.path.exists():
                self._database.mirror_prediction_records(self._read_verified())

    def append_prediction(
        self,
        game: Mapping[str, Any],
        decision_snapshot: Mapping[str, Any],
        *,
        kickoff: str | datetime,
        market_observed_at: str | datetime,
        features: Mapping[str, Any],
        predictions: Mapping[str, Any] | None = None,
        model: Mapping[str, Any] | None = None,
        as_of: str | datetime | None = None,
        deduplication_key: str | None = None,
    ) -> AppendResult:
        """Append one immutable pregame prediction.

        ``snapshot_id`` is the existing ``ai_decision.decision_id``.  A retry
        with exactly the same prediction is a no-op; presenting different
        content under that ID raises :class:`LedgerConflictError`.
        """
        snapshot = _json_copy(decision_snapshot)
        validate_decision_snapshot(snapshot)
        expected_event_id = decision_event_id(dict(game))
        actual_event_id = snapshot.get("event_id")
        if actual_event_id != expected_event_id:
            raise PredictionLedgerError(
                "decision snapshot event_id does not match ai_decision.event_id(game)"
            )
        snapshot_id = snapshot.get("decision_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise PredictionLedgerError("decision snapshot must include decision_id")

        snapshot_as_of = snapshot.get("as_of")
        chosen_as_of = as_of if as_of is not None else snapshot_as_of
        if chosen_as_of is None:
            raise PredictionLedgerError("as_of is required")
        as_of_text = _format_time(chosen_as_of, "as_of")
        if snapshot_as_of is not None and _format_time(snapshot_as_of, "snapshot.as_of") != as_of_text:
            raise PredictionLedgerError("as_of does not match decision snapshot")
        kickoff_text = _format_time(kickoff, "kickoff")
        market_text = _format_time(market_observed_at, "market_observed_at")
        as_of_time = _parse_time(as_of_text, "as_of")
        kickoff_time = _parse_time(kickoff_text, "kickoff")
        market_time = _parse_time(market_text, "market_observed_at")
        if as_of_time >= kickoff_time:
            raise PredictionLedgerError("prediction as_of must be before kickoff")
        if market_time > as_of_time:
            raise PredictionLedgerError("market observation must not be after as_of")

        chosen_model = _json_copy(model if model is not None else snapshot.get("model") or {})
        chosen_predictions = _json_copy(
            predictions if predictions is not None else self._prediction_payload(snapshot)
        )
        evidence_payload = {
            "evidence": snapshot.get("evidence") or [],
            "sources": snapshot.get("sources") or [],
        }
        versions = {
            "decision_schema": snapshot.get("schema_version"),
            "operating_model": chosen_model.get("operating_version"),
            "residual_model": chosen_model.get("residual_version"),
        }
        record = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "prediction",
            "snapshot_id": snapshot_id,
            "decision_id": snapshot_id,
            "event_id": actual_event_id,
            "as_of": as_of_text,
            "kickoff": kickoff_text,
            "market_observed_at": market_text,
            "features": _json_copy(features),
            "model": chosen_model,
            "versions": versions,
            "predictions": chosen_predictions,
            "input_revision_hash": snapshot.get("input_revision_hash"),
            "decision_snapshot_hash": _sha256(snapshot),
            "evidence_hash": _sha256(evidence_payload),
        }
        if deduplication_key is not None:
            if not isinstance(deduplication_key, str) or not deduplication_key:
                raise PredictionLedgerError("deduplication_key must be a non-empty string")
            record["deduplication_key"] = deduplication_key
        return self._append(record, must_capture_before=kickoff_time)

    def append_settlement(
        self,
        snapshot_id: str,
        *,
        outcome: Mapping[str, Any],
        settled_at: str | datetime,
        source: Mapping[str, Any] | str,
        settlement_version: str = "official-v1",
    ) -> AppendResult:
        """Append a result record without altering its prediction record.

        Corrections use a new ``settlement_version`` and therefore form a new
        append-only record; rewriting an existing version is rejected.
        """
        if not snapshot_id:
            raise PredictionLedgerError("snapshot_id is required")
        if not settlement_version:
            raise PredictionLedgerError("settlement_version is required")
        settlement_id = "stl_" + _sha256([snapshot_id, settlement_version])[:20]
        record = {
            "schema_version": SCHEMA_VERSION,
            "record_type": "settlement",
            "settlement_id": settlement_id,
            "settlement_version": settlement_version,
            "snapshot_id": snapshot_id,
            "settled_at": _format_time(settled_at, "settled_at"),
            "outcome": _json_copy(outcome),
            "source": _json_copy(source),
        }
        return self._append(record, required_prediction_id=snapshot_id)

    def records(self) -> list[dict[str, Any]]:
        """Read and verify the complete ledger hash chain."""
        with self._lock():
            return self._read_verified()

    @staticmethod
    def _prediction_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "action": snapshot.get("action"),
            "selection_id": snapshot.get("selection_id"),
            "offer_id": snapshot.get("offer_id"),
            "probability": snapshot.get("probability") or {},
            "gate_codes": snapshot.get("gate_codes") or [],
        }

    def _append(
        self,
        core: dict[str, Any],
        *,
        must_capture_before: datetime | None = None,
        required_prediction_id: str | None = None,
    ) -> AppendResult:
        target_identity = _identity(core)
        with self._lock():
            records = self._read_verified()
            existing = next((row for row in records if _identity(row) == target_identity), None)
            if existing is not None:
                if _immutable_payload(existing) != _immutable_payload(core):
                    raise LedgerConflictError(
                        f"conflicting rewrite for {target_identity[0]} {target_identity[1]}"
                    )
                return AppendResult(record=_json_copy(existing), appended=False)

            deduplication_key = core.get("deduplication_key")
            if core.get("record_type") == "prediction" and deduplication_key:
                duplicate_revision = next((
                    row for row in records
                    if row.get("record_type") == "prediction"
                    and row.get("event_id") == core.get("event_id")
                    and row.get("deduplication_key") == deduplication_key
                ), None)
                if duplicate_revision is not None:
                    return AppendResult(
                        record=_json_copy(duplicate_revision),
                        appended=False,
                    )

            required_prediction = None
            if required_prediction_id is not None:
                required_prediction = next((
                    row for row in records
                    if row.get("record_type") == "prediction"
                    and row.get("snapshot_id") == required_prediction_id
                ), None)
                if required_prediction is None:
                    raise PredictionLedgerError(
                        f"cannot settle unknown prediction snapshot: {required_prediction_id}"
                    )

            captured = self._clock()
            captured_time = _parse_time(captured, "clock")
            if must_capture_before is not None and captured_time >= must_capture_before:
                raise PredictionLedgerError("prediction cannot be captured at or after kickoff")
            if core.get("record_type") == "prediction":
                as_of_time = _parse_time(core["as_of"], "as_of")
                if as_of_time > captured_time:
                    raise PredictionLedgerError("prediction as_of cannot be after capture time")
            if core.get("record_type") == "settlement":
                settled_time = _parse_time(core["settled_at"], "settled_at")
                if settled_time > captured_time:
                    raise PredictionLedgerError("settled_at cannot be after capture time")
                kickoff_time = _parse_time(required_prediction["kickoff"], "kickoff")
                if settled_time < kickoff_time:
                    raise PredictionLedgerError("settled_at cannot be before kickoff")
            record = {
                **core,
                "captured_at": _format_time(captured_time, "captured_at"),
                "ledger_sequence": len(records) + 1,
                "previous_record_hash": records[-1]["record_hash"] if records else None,
            }
            record["record_hash"] = _sha256(record)
            self._write_line(record)
            return AppendResult(record=_json_copy(record), appended=True)

    def _read_verified(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        previous_hash: str | None = None
        seen_identities: set[tuple[str, str]] = set()
        predictions: dict[str, dict[str, Any]] = {}
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    raise LedgerCorruptionError(f"blank JSONL record at line {line_number}")
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise LedgerCorruptionError(
                        f"invalid JSONL record at line {line_number}"
                    ) from exc
                if not isinstance(record, dict):
                    raise LedgerCorruptionError(f"non-object record at line {line_number}")
                if record.get("schema_version") != SCHEMA_VERSION:
                    raise LedgerCorruptionError(
                        f"unsupported schema at line {line_number}"
                    )
                if record.get("ledger_sequence") != line_number:
                    raise LedgerCorruptionError(f"invalid sequence at line {line_number}")
                if record.get("previous_record_hash") != previous_hash:
                    raise LedgerCorruptionError(f"broken hash chain at line {line_number}")
                claimed_hash = record.get("record_hash")
                unsigned = {key: value for key, value in record.items() if key != "record_hash"}
                if claimed_hash != _sha256(unsigned):
                    raise LedgerCorruptionError(f"invalid record hash at line {line_number}")
                identity = _identity(record)
                if identity in seen_identities:
                    raise LedgerCorruptionError(
                        f"duplicate identity at line {line_number}"
                    )
                seen_identities.add(identity)
                if record.get("record_type") == "prediction":
                    predictions[record["snapshot_id"]] = record
                else:
                    prediction = predictions.get(record.get("snapshot_id"))
                    if prediction is None:
                        raise LedgerCorruptionError(
                            f"settlement precedes prediction at line {line_number}"
                        )
                    if _parse_time(record["settled_at"], "settled_at") < _parse_time(
                        prediction["kickoff"], "kickoff"
                    ):
                        raise LedgerCorruptionError(
                            f"settlement predates kickoff at line {line_number}"
                        )
                records.append(record)
                previous_hash = claimed_hash
        return records

    def _write_line(self, record: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 볼륨 DB를 먼저 확정하고 JSONL은 호환 export로 기록한다.
        if self._database is not None:
            self._database.mirror_prediction_records([record])
        payload = (_canonical_json(record) + "\n").encode("utf-8")
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            written = 0
            while written < len(payload):
                count = os.write(descriptor, payload[written:])
                if count <= 0:
                    raise OSError("failed to append prediction ledger record")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}:{uuid.uuid4().hex}"
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
                    0o600,
                )
                try:
                    os.write(descriptor, token.encode("ascii"))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                break
            except (FileExistsError, PermissionError):
                # Windows can report a sharing violation as PermissionError
                # while another thread still owns the exclusive lock file.
                self._remove_stale_lock()
                if time.monotonic() >= deadline:
                    raise LedgerLockTimeout(f"timed out locking {self.path}")
                time.sleep(0.01)
        try:
            yield
        finally:
            try:
                if self.lock_path.read_text(encoding="ascii") == token:
                    self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def _remove_stale_lock(self) -> None:
        try:
            age = time.time() - self.lock_path.stat().st_mtime
            if age > self.stale_lock_after:
                self.lock_path.unlink()
        except FileNotFoundError:
            pass
