"""Paper-only paired event comparison; never changes operational selections."""
from collections import Counter
from datetime import timedelta, timezone
import hashlib

from recommendation_history import number, probability, selection_key, stamp, settle_history

POLICY = "event-value-hit-first-v1"
FIELDS = ("home", "away", "sport", "league", "date", "round", "game_no", "market",
          "market_label", "sel", "odds", "kickoff_at", "n_way", "predicted_hit_prob",
          "market_prob", "probability_source", "probability_lower_bound", "decision_id",
          "decision_model", "decision_artifact_hash", "validated_uncertainty_available")


def event_key(row):
    kickoff = stamp(row.get("kickoff_at"))
    if not kickoff or not row.get("home") or not row.get("away"):
        return None
    raw = [kickoff.astimezone(timezone.utc).isoformat()]
    raw += [str(row.get(k) or "") for k in ("sport", "league", "home", "away")]
    return hashlib.sha256("|".join(raw).encode()).hexdigest()[:24]


def eligibility(row):
    p = number(row.get("predicted_hit_prob"))
    low = number(row.get("probability_lower_bound"))
    if not (row.get("has_validated_edge") is True
            and row.get("decision_pipeline_applied") is True
            and row.get("validated_uncertainty_available") is True
            and row.get("decision_id") and row.get("decision_artifact_hash")):
        return "unvalidated_probability"
    if not 0 < low <= p < 1 or number(row.get("odds")) <= 1:
        return "invalid_probability_or_price"
    return "eligible" if low * number(row["odds"]) > 1 else "insufficient_conservative_value"


def compact(row):
    return {k: row.get(k) for k in FIELDS} if row else None


def proposals(pool, baseline):
    groups = {}
    for row in pool:
        key = event_key(row)
        if key:
            groups.setdefault(key, []).append(row)
    old = {event_key(r): r for r in baseline}
    output = []
    for key, rows in sorted(groups.items()):
        eligible = [r for r in rows if eligibility(r) == "eligible"]
        ranked = sorted(eligible, key=lambda r: (-probability(r),
                        -number(r.get("probability_lower_bound")), selection_key(r)))
        output.append({"id": key, "kickoff_at": rows[0]["kickoff_at"],
                       "baseline": compact(old.get(key)),
                       "challenger": compact(ranked[0]) if ranked else None,
                       "candidate_count": len(rows),
                       "reason_counts": dict(Counter(eligibility(r) for r in rows)),
                       "status": "candidate" if ranked else "abstain"})
    return output


def capture(payload, previous, odds, now):
    # Only database-owned previous state is trusted; incoming archives are ignored.
    state = dict((previous or {}).get("per_event_shadow") or {})
    if state.get("policy") and state.get("policy") != POLICY:
        return state
    if not state.get("policy"):
        state = {}
    times = [stamp(payload.get(k)) for k in ("generated_at", "source_generated_at", "live_odds_at")]
    fresh = (all(t and timedelta(0) <= now-t <= timedelta(minutes=15) for t in times)
             and not payload.get("partial") and not payload.get("error"))
    if not state:
        if not fresh:
            return {"status": "awaiting_fresh_source"}
        state = {"policy": POLICY, "started_at": now.isoformat(),
                 "ends_at": (now+timedelta(days=28)).isoformat(), "records": {}}
    records = dict(state.get("records") or {})
    accepting = fresh and now < stamp(state["ends_at"])
    if accepting:
        for proposal in payload.get("per_event_shadow_proposals") or []:
            kickoff = stamp(proposal.get("kickoff_at"))
            key = proposal.get("id")
            if (not key or key in records or not kickoff or now >= kickoff-timedelta(minutes=30)
                    or len(records) >= 2000):
                continue
            records[key] = {**proposal, "recorded_at": now.isoformat(),
                            "generated_at": payload["generated_at"],
                            "source_generated_at": payload["source_generated_at"],
                            "live_odds_at": payload["live_odds_at"]}
    for record in records.values():
        for arm in ("baseline", "challenger"):
            if record.get(arm):
                settle_history({"pick": record[arm]}, odds, now)
    summary = {}
    for arm in ("baseline", "challenger"):
        picks = [r[arm] for r in records.values() if r.get(arm)]
        settled = [r for r in picks if r.get("result") in ("hit", "miss", "void")]
        profit = sum(number(r["odds"])-1 if r["result"] == "hit" else
                     0 if r["result"] == "void" else -1 for r in settled)
        summary[arm] = {"selected": len(picks), "settled": len(settled),
                        "pending": len(picks)-len(settled), "profit_units": profit,
                        "roi": profit/len(settled) if settled else None,
                        "hits": sum(r["result"] == "hit" for r in settled),
                        "misses": sum(r["result"] == "miss" for r in settled),
                        "void": sum(r["result"] == "void" for r in settled)}
    paired = [r for r in records.values() if all(
        r.get(arm) and r[arm].get("result") in ("hit", "miss", "void")
        for arm in ("baseline", "challenger"))]
    def pnl(row):
        return number(row["odds"])-1 if row["result"] == "hit" else 0 if row["result"] == "void" else -1
    summary["paired"] = {"settled_events": len(paired),
                         "profit_difference_units": sum(pnl(r["challenger"])-pnl(r["baseline"]) for r in paired),
                         "different_selections": sum(selection_key(r["baseline"]) != selection_key(r["challenger"])
                                                     for r in paired)}
    state.update(records=records, summary=summary, observed_events=len(records),
                 abstained_events=sum(not r.get("challenger") for r in records.values()),
                 status="recording" if now < stamp(state["ends_at"]) else "closed_settling",
                 source_status="fresh" if fresh else "stale_or_partial",
                 capacity_reached=len(records) >= 2000)
    return state
