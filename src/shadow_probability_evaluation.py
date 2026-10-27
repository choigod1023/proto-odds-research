"""Descriptive paired evaluation only; no fitting, selection search or promotion."""
from collections import Counter
from datetime import datetime, timedelta
import math


def parse(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result


def evaluate(records):
    latest, settlements = {}, {}
    excluded = Counter()
    for row in records:
        if row.get("record_type") == "prediction":
            key = row["event_id"]
            order = (parse(row["as_of"]), row.get("ledger_sequence", 0))
            if key not in latest or order > latest[key][0]:
                latest[key] = order, row
        elif row.get("record_type") == "settlement":
            settlements.setdefault(row["snapshot_id"], []).append(row)
    paired = []
    for _, row in latest.values():
        p = row.get("predictions") or {}
        shadow = p.get("validation_shadow") or {}
        if shadow.get("status") != "shadow":
            excluded[shadow.get("reason") or "no_shadow_capture"] += 1
            continue
        matches = settlements.get(row["snapshot_id"], [])
        if not matches:
            excluded["pending"] += 1
            continue
        outcomes = {(r.get("outcome") or {}).get("result") for r in matches}
        if len(outcomes) != 1 or not outcomes <= {"hit", "miss"}:
            excluded["void_or_conflicting_outcomes"] += 1
            continue
        if (shadow.get("selection_id") != p.get("selection_id")
                or shadow.get("offer_id") != p.get("offer_id")
                or any((r.get("outcome") or {}).get("selection_id") != p.get("selection_id") for r in matches)):
            excluded["identity_mismatch"] += 1
            continue
        try:
            cutoff = parse(row["kickoff"]) - timedelta(minutes=30)
            observed, captured = parse(shadow["observed_at"]), parse(row["captured_at"])
            if not parse(row["as_of"]) <= observed <= captured < cutoff:
                raise ValueError("late capture")
            if any(not r.get("source") or not str(r.get("settlement_version", "")).startswith("official-")
                   or not parse(row["kickoff"]) <= parse(r["settled_at"]) <= parse(r["captured_at"])
                   for r in matches):
                raise ValueError("unproven settlement")
        except (ValueError, KeyError, TypeError, AttributeError):
            excluded["invalid_capture_time"] += 1
            continue
        market, candidate = shadow.get("market_probability"), shadow.get("probability")
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not 0 < v < 1
               for v in (market, candidate)):
            excluded["invalid_probability"] += 1
            continue
        paired.append((shadow.get("version"), market, candidate, int(outcomes == {"hit"})))
    by_version = {}
    for version in sorted({r[0] for r in paired}, key=str):
        rows = [r for r in paired if r[0] == version]
        metrics = {"n": len(rows), "hits": sum(r[3] for r in rows)}
        for label, index in (("market", 1), ("candidate", 2)):
            metrics[label + "_brier"] = sum((r[index] - r[3]) ** 2 for r in rows) / len(rows)
            metrics[label + "_log_loss"] = -sum(r[3] * math.log(r[index]) + (1-r[3]) * math.log(1-r[index]) for r in rows) / len(rows)
        by_version[version] = metrics
    return {"selected_events": len(latest), "paired": len(paired), "excluded": dict(excluded),
            "by_version": by_version, "promotion_allowed": False, "roi": None,
            "status": "descriptive_only" if paired else "no_settled_pairs",
            "limitations": ["No ledger/source authentication; use verified copies.",
                            "Not a temporal holdout or combination backtest.",
                            "Same recorded selections; no strategy ROI inferred."]}
