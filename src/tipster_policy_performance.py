"""Audit frozen vote selections separately from no-vote market fallbacks."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from prediction_runtime import _latest_predictions
from ai_decision import can_apply_decision_selection

POLICY = "tipster-vote-v1"


def moment(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def classify(record):
    model = record.get("model") or {}
    if model.get("selection_policy") != POLICY:
        return "other_policy"
    pick = record.get("predictions") or {}
    if pick.get("action") != "market_reference":
        return "withheld"
    info = (record.get("features") or {}).get("tipster_hybrid") or {}
    if (info.get("selection_source") == "market_fallback"
            or info.get("reason") == "market_fallback_no_votes"):
        return "market_fallback"
    votes = info.get("vote_counts") or {}
    authors = info.get("authors")
    if (info.get("version") != POLICY or info.get("status") != "applied"
            or type(authors) is not int or authors <= 0
            or not votes or any(type(n) is not int or n < 0 for n in votes.values())
            or sum(votes.values()) != authors):
        return "unverified_vote_selection"
    winners = [side for side, n in votes.items() if n == max(votes.values())]
    if (len(winners) != 1 or winners[0] != pick.get("selection")
            or info.get("selected_side") != pick.get("selection")
            or not can_apply_decision_selection(model)):
        return "unverified_vote_selection"
    return "actual_tipster_vote"


def _summary(rows, stake):
    counts = Counter(row["result"] for row in rows)
    decided = [r for r in rows if r["result"] in {"hit", "miss"}]
    net = sum((Decimal(str(r["odds"])) - 1) * stake if r["result"] == "hit"
              else -stake for r in decided)
    staked = stake * len(decided)
    return dict(events=len(rows), hit=counts["hit"], miss=counts["miss"],
                void=counts["void"], pending=counts["pending"], decided=len(decided),
                accuracy_percent=float(100 * Decimal(counts["hit"]) / len(decided)) if decided else None,
                equal_stake_roi_percent=float(net * 100 / staked) if staked else None,
                hypothetical_stake_won=stake, hypothetical_net_won=str(net),
                decided_staked_won=staked)


def audit(records, *, evaluated_at, stake=10000):
    if type(stake) is not int or stake <= 0:
        raise ValueError("positive integer stake required")
    evaluated = moment(evaluated_at)
    records = sorted(records, key=lambda r: int(r.get("ledger_sequence") or 0))
    latest = _latest_predictions(records)
    settlements = {}
    for row in records:
        if row.get("record_type") == "settlement":
            settlements[row["snapshot_id"]] = row
    cohorts, holds, classified = defaultdict(list), [], Counter()
    for event, row in latest.items():
        kind = classify(row)
        classified[kind] += 1
        if kind not in {"actual_tipster_vote", "market_fallback"}:
            continue
        try:
            start = moment(row["kickoff"])
            clocks = [moment(row[k]) for k in ("as_of", "captured_at", "market_observed_at")]
            if max(clocks) >= start - timedelta(minutes=30) or max(clocks) > evaluated:
                raise ValueError("not_before_t30")
            pick = row["predictions"]
            if isinstance(pick.get("odds"), bool) or not Decimal(str(pick["odds"])).is_finite() or Decimal(str(pick["odds"])) <= 1:
                raise ValueError("invalid_saved_odds")
            settlement = settlements.get(row["snapshot_id"])
            result = "pending"
            if settlement:
                outcome = settlement.get("outcome") or {}
                if (moment(settlement["settled_at"]) <= evaluated and
                        outcome.get("selection_id") == pick.get("selection_id") and
                        outcome.get("result") in {"hit", "miss", "void"}):
                    result = outcome["result"]
                else:
                    raise ValueError("invalid_or_future_settlement")
            cohorts[kind].append(dict(event_id=event, snapshot_id=row["snapshot_id"],
                kickoff=row["kickoff"], captured_at=row["captured_at"],
                fixture=(row.get("features") or {}).get("fixture") or {},
                selection=pick["selection"], odds=pick["odds"], result=result,
                authors=((row.get("features") or {}).get("tipster_hybrid") or {}).get("authors", 0)))
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            holds.append(dict(snapshot_id=row.get("snapshot_id"), cohort=kind, reason=str(exc)))
    summaries = {}
    for kind in ("actual_tipster_vote", "market_fallback"):
        rows = cohorts[kind]
        sports = sorted({r["fixture"].get("sport") or "unknown" for r in rows})
        summaries[kind] = {**_summary(rows, stake), "by_sport": {
            sport: _summary([r for r in rows if (r["fixture"].get("sport") or "unknown") == sport], stake)
            for sport in sports}, "records": rows}
    return dict(version="tipster-policy-performance-v1", evaluated_at=evaluated.isoformat(),
                policy=POLICY, classification_counts=dict(classified), cohorts=summaries, holds=holds,
                selection_rule="latest_stored_revision_per_event_then_policy_and_t30_checks",
                result_rule="latest_same_snapshot_settlement_only", void_excluded_from_roi=True,
                monthly_cap_applied=False, hypothetical_only=True, hash_chain_verified=False)
