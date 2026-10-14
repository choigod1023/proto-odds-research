"""Paper-only paired event comparison; never changes operational selections."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta, timezone
import hashlib
import json

from recommendation_history import number, probability, selection_key, stamp, settle_history

POLICY = "event-value-hit-first-v1"
FIELDS = ("home", "away", "sport", "league", "date", "round", "game_no", "market",
          "market_label", "sel", "odds", "kickoff_at", "n_way", "predicted_hit_prob",
          "market_prob", "probability_source", "probability_lower_bound", "decision_id",
          "decision_model", "decision_artifact_hash", "validated_uncertainty_available",
          "decision_pipeline_applied", "has_validated_edge")
FIELDS += ("probability_method", "probability_version", "is_market_favorite", "price_source")
CAPTURE_VERSION = "bounded-roster-v2"

# Serialized research data budget, NOT a promise about process RSS.
# Reserve settlement fields too; never drop old outcomes to admit new ones.
ROSTER_BUDGET = 256 * 1024
MAX_EVENT_OPTIONS = 24


def roster_cost(rows):
    try:
        return len(json.dumps(rows, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()) + 256*len(rows)
    except (TypeError, ValueError):
        return ROSTER_BUDGET + 1


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
    remaining = ROSTER_BUDGET
    for key, rows in sorted(groups.items()):
        eligible = [r for r in rows if eligibility(r) == "eligible"]
        ranked = sorted(eligible, key=lambda r: (-probability(r),
                        -number(r.get("probability_lower_bound")), selection_key(r)))
        roster = [compact(r) for r in rows] if len(rows) <= MAX_EVENT_OPTIONS else []
        status = "complete" if roster else "too_many_options"
        cost = roster_cost(roster)
        if cost > remaining:
            roster, status = [], "batch_budget_exhausted"
        elif roster:
            remaining -= cost
        output.append({"id": key, "kickoff_at": rows[0]["kickoff_at"],
                       "baseline": compact(old.get(key)),
                       "challenger": compact(ranked[0]) if ranked else None,
                       "candidate_count": len(rows),
                       "reason_counts": dict(Counter(eligibility(r) for r in rows)),
                       "status": "candidate" if ranked else "abstain",
                       "candidate_roster": roster, "roster_status": status})
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
    # Settlement mutates picks; keep the caller's prior snapshot immutable.
    records = deepcopy(state.get("records") or {})
    used = sum(r.get("roster_reserved_bytes", 0) for r in records.values())
    accepting = fresh and now < stamp(state["ends_at"])
    if accepting:
        for proposal in payload.get("per_event_shadow_proposals") or []:
            kickoff = stamp(proposal.get("kickoff_at"))
            key = proposal.get("id")
            if (not key or key in records or not kickoff or now >= kickoff-timedelta(minutes=30)
                    or len(records) >= 2000):
                continue
            entry = dict(proposal)
            roster = entry.pop("candidate_roster", [])
            cost = roster_cost(roster)
            if (entry.get("roster_status") == "complete" and roster
                    and len(roster) <= MAX_EVENT_OPTIONS and cost + used <= ROSTER_BUDGET
                    and len(roster) == entry.get("candidate_count")
                    and len({selection_key(r) for r in roster}) == len(roster)
                    and all(event_key(r) == key for r in roster)):
                entry.update(candidate_roster=deepcopy(roster), roster_reserved_bytes=cost)
                used += cost
            else:
                entry["roster_status"] = (("archive_budget_exhausted" if cost+used > ROSTER_BUDGET
                                           else "invalid_roster") if roster else
                                          entry.get("roster_status", "legacy_missing"))
            # Identifies a jointly available batch; not separate final T30 offers.
            batch = [payload[k] for k in ("generated_at", "source_generated_at", "live_odds_at")]
            batch.append(now.isoformat())
            entry["observation_batch_id"] = hashlib.sha256("|".join(batch).encode()).hexdigest()[:24]
            entry.update(capture_version=CAPTURE_VERSION,
                         roster_scope="supported_research_markets_after_source_filters",
                         recommendation_policy=payload.get("recommendation_policy"))
            records[key] = {**entry, "recorded_at": now.isoformat(),
                            "generated_at": payload["generated_at"],
                            "source_generated_at": payload["source_generated_at"],
                            "live_odds_at": payload["live_odds_at"]}
    for record in records.values():
        for arm in ("baseline", "challenger"):
            if record.get(arm):
                settle_history({"pick": record[arm]}, odds, now)
        for option in record.get("candidate_roster") or []:
            settle_history({"pick": option}, odds, now)
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
    state.update(roster_reserved_bytes=used, roster_budget_bytes=ROSTER_BUDGET,
                 roster_complete_events=sum(r.get("roster_status") == "complete" for r in records.values()),
                 roster_status_counts=dict(Counter(r.get("roster_status", "legacy_missing")
                                                    for r in records.values())))
    return state
