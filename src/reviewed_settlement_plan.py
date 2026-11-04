"""Apply an already reviewed, hash-pinned result plan using the standard ledger."""
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from prediction_revision_index import _latest_predictions


def moment(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def read_plan(raw, approved_sha256):
    if hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise ValueError("approved plan hash differs")
    doc = json.loads(raw)
    if doc.get("version") != "reviewed-official-settlements-v1" or not isinstance(doc.get("entries"), list):
        raise ValueError("unsupported reviewed plan")
    return doc


def apply_reviewed_plan(ledger, plan, *, apply=False):
    entries = plan["entries"]
    if len({e["snapshot_id"] for e in entries}) != len(entries):
        raise ValueError("duplicate target snapshot")
    with ledger.verified_session():
        records = ledger.records()
        latest = _latest_predictions(records)
        settled = {}
        for record in records:
            if record.get("record_type") == "settlement":
                settled.setdefault(record["snapshot_id"], []).append(record)
        append = []
        for entry in entries:
            pred = latest.get(entry["event_id"])
            if (pred is None or pred["snapshot_id"] != entry["snapshot_id"]
                    or pred.get("record_hash") != entry["expected_prediction_hash"]):
                raise ValueError("target prediction changed; nothing appended")
            outcome = entry["outcome"]
            if (pred.get("predictions", {}).get("action") != "market_reference"
                    or outcome.get("selection_id") != pred["predictions"].get("selection_id")
                    or outcome.get("result") not in {"hit", "miss", "void"}
                    or moment(entry["settled_at"]) < moment(pred["kickoff"])):
                raise ValueError("invalid selected outcome")
            source = entry["source"]
            if source.get("mode") == "official_score_with_frozen_terms":
                if (source.get("original_market") != pred["predictions"].get("market")
                        or source.get("original_label") != (pred["predictions"].get("label") or "")):
                    raise ValueError("frozen result terms changed")
                evidence_rows = [r.get("official_source") or {} for r in source.get("official_scores", [])]
            else:
                evidence_rows = [source.get("official_source") or {}]
            if not evidence_rows:
                raise ValueError("missing reviewed official evidence")
            for evidence in evidence_rows:
                url = urlsplit(evidence.get("url", ""))
                if (url.scheme != "https" or url.netloc != "www.betman.co.kr"
                        or url.path != "/main/mainPage/gamebuy/closedGameSlipIFR.do"
                        or not re.fullmatch(r"[0-9a-f]{64}", evidence.get("evidence_sha256", ""))
                        or moment(evidence["received_at"]) > moment(entry["settled_at"])):
                    raise ValueError("missing reviewed official evidence")
            canonical = json.dumps(outcome, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False)
            version = "official-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]
            if entry["settlement_version"] != version:
                raise ValueError("settlement version differs from outcome")
            previous = settled.get(entry["snapshot_id"], [])
            if previous:
                same = [r for r in previous if r.get("settlement_version") == version
                        and r.get("outcome") == outcome and r.get("source") == entry["source"]
                        and moment(r["settled_at"]) == moment(entry["settled_at"])]
                if len(same) != len(previous):
                    raise ValueError("target already has a different settlement")
            append.append({k: entry[k] for k in (
                "snapshot_id", "outcome", "settled_at", "source", "settlement_version")})
        if not apply:
            return dict(validated=len(entries), appended=0, dry_run=True)
        results = ledger.append_settlements(append)
        return dict(validated=len(entries), appended=sum(r.appended for r in results), dry_run=False)
