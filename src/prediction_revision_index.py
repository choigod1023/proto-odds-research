"""Latest frozen revision rule without score-model or dataframe imports."""
from datetime import datetime, timezone
from typing import Any


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

