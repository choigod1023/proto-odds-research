"""Prospective, paper-only daily cohorts; no recommendation or betting action."""
from copy import deepcopy
from datetime import timedelta, timezone
import hashlib
import math

from recommendation_history import KST, highlights, number, probability, selection_key, settle_history, stamp

POLICY = "shadow-daily-two-p60-o150-v1"
MAX_AGE = timedelta(minutes=15)
FIELDS = ("home", "away", "sport", "league", "date", "round", "game_no", "market",
          "market_label", "sel", "odds", "kickoff_at", "n_way", "probability_source",
          "decision_model", "decision_id", "decision_pipeline_applied", "decision_artifact_hash")


def event_key(row):
    # Different markets/round aliases of one fixture must not become two legs.
    return "|".join(str(row.get(k) or "") for k in ("sport", "league", "home", "away")) + "|" + stamp(row["kickoff_at"]).astimezone(timezone.utc).isoformat()


def update_experiment(payload, previous, odds, now):
    state = deepcopy(previous or {"policy": POLICY, "paper_only": True, "days": {}})
    if state.get("policy") != POLICY:
        return state  # Never silently reinterpret an older experiment.
    days = state["days"]
    for day in days.values():
        if day["status"] == "draft" and now >= stamp(day["cutoff_at"]):
            fresh = all(timedelta(0) <= stamp(day["cutoff_at"]) - stamp(day[k]) <= MAX_AGE
                        for k in ("recorded_at", "generated_at", "source_generated_at", "live_odds_at"))
            day["status"] = "frozen" if fresh else "skipped_stale"
            day["frozen_at"] = now.isoformat()
        if day["status"] == "frozen":
            settle_history(day["singles"], odds, now)
            legs = [day["singles"][k] for k in day["legs"]]
            if len(legs) == 2 and all(r.get("result") in ("hit", "miss", "void") for r in legs):
                day["return_units"] = math.prod(0 if r["result"] == "miss" else
                                                1 if r["result"] == "void" else r["odds"] for r in legs)

    times = {k: stamp(payload.get(k)) for k in ("generated_at", "source_generated_at", "live_odds_at")}
    if (not payload.get("partial") and not payload.get("error")
            and all(t and timedelta(0) <= now - t <= MAX_AGE for t in times.values())):
        rows = payload.get("candidates") or []
        chosen = highlights(rows)
        groups = {}
        for row in rows:
            kickoff = stamp(row.get("kickoff_at"))
            if (not kickoff or kickoff - timedelta(minutes=30) <= now or not row.get("home")
                    or not row.get("away") or selection_key(row) not in chosen or not 0 < probability(row) < 1):
                continue
            groups.setdefault(kickoff.astimezone(KST).date().isoformat(), []).append(row)
        for date, candidates in groups.items():
            old = days.get(date)
            if old and old["status"] != "draft":
                continue
            cutoff = stamp(old["cutoff_at"]) if old else min(stamp(r["kickoff_at"]) for r in candidates) - timedelta(minutes=30)
            # Deadline can move earlier, never later to cherry-pick a new cohort.
            cutoff = min(cutoff, min(stamp(r["kickoff_at"]) for r in candidates) - timedelta(minutes=30))
            singles = {}
            ranked = sorted(candidates, key=lambda r: (-probability(r), number(r["odds"]), event_key(r), selection_key(r)))
            for row in ranked:
                key = hashlib.sha256(event_key(row).encode()).hexdigest()[:24]
                if key in singles:
                    continue
                singles[key] = {**{k: row.get(k) for k in FIELDS}, "id": key,
                                "probability": probability(row), "odds": number(row["odds"]),
                                "probability_source": row.get("probability_source") or "unknown",
                                "filtered": probability(row) >= .60 and number(row["odds"]) >= 1.50}
            eligible = [k for k, r in singles.items() if r["filtered"]]
            legs = eligible[:2] if len(eligible) >= 2 else []
            days[date] = {"status": "draft", "cutoff_at": cutoff.isoformat(), "recorded_at": now.isoformat(),
                          **{k: t.isoformat() for k, t in times.items()}, "singles": singles, "legs": legs,
                          "combined_odds": math.prod(singles[k]["odds"] for k in legs) if legs else None,
                          "independence_assumed_ev": math.prod(singles[k]["probability"] * singles[k]["odds"] for k in legs) - 1 if legs else None}
    # Bounded rolling evaluation; retain pending and losing days by the same rule.
    state["days"] = {k: d for k, d in days.items() if stamp(d["cutoff_at"]) >= now - timedelta(days=90)}
    state["summary"] = summarize(state["days"])
    return state


def summarize(days):
    cohorts = {k: [] for k in ("recommended_singles", "filtered_singles", "two_leg")}
    for _, day in sorted(days.items()):
        if day["status"] != "frozen":
            continue
        for row in day["singles"].values():
            ret = {"hit": row["odds"], "miss": 0, "void": 1}.get(row.get("result"))
            cohorts["recommended_singles"].append(ret)
            if row["filtered"]:
                cohorts["filtered_singles"].append(ret)
        if day["legs"]:
            cohorts["two_leg"].append(day.get("return_units"))
    result = {}
    for name, returns in cohorts.items():
        settled = [r for r in returns if r is not None]
        balance = peak = drawdown = 0
        for ret in settled:
            balance += ret - 1
            peak = max(peak, balance)
            drawdown = max(drawdown, peak - balance)
        result[name] = {"settled": len(settled), "pending": len(returns) - len(settled),
                        "stake_units": len(settled), "profit_units": balance,
                        "roi": balance / len(settled) if settled else None,
                        "max_drawdown_units_cohort_order": drawdown}
    return result
