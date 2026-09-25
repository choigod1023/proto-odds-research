"""Pre-registered paper-only cohorts; no production selection changes."""
from copy import deepcopy
from datetime import timedelta, timezone
import hashlib
import math
from recommendation_history import KST, highlights, number, probability, selection_key, settle_history, stamp

POLICY = "shadow-fixed-budget-28d-v2"
MAX_AGE = timedelta(minutes=15)
TIME_FIELDS = ("generated_at", "source_generated_at", "live_odds_at")
FIELDS = ("home", "away", "sport", "league", "date", "round", "game_no", "market",
          "market_label", "sel", "odds", "kickoff_at", "n_way", "probability_source",
          "decision_model", "decision_id", "decision_pipeline_applied", "decision_artifact_hash")
STRATEGIES = ("recommended_singles", "p58_singles", "p60_singles", "daily_two", "slot_two")


def event_key(row):
    identity = "|".join(str(row.get(k) or "") for k in ("sport", "league", "home", "away"))
    identity += "|" + stamp(row["kickoff_at"]).astimezone(timezone.utc).isoformat()
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def passes(row, threshold):
    return row["probability"] >= threshold and row["odds"] >= 1.50


def fresh(times, at):
    return all(t and timedelta(0) <= at - t <= MAX_AGE for t in times)


def ticket(ids, stake, rows):
    return {"legs": ids, "stake": stake,
            "combined_odds": math.prod(rows[k]["odds"] for k in ids),
            "independence_assumed_ev": math.prod(rows[k]["probability"] * rows[k]["odds"] for k in ids) - 1}


def freeze(batch, now):
    if batch["status"] != "draft" or now < stamp(batch["cutoff_at"]):
        return
    batch["frozen_at"] = now.isoformat()
    if not fresh([stamp(batch[k]) for k in (*TIME_FIELDS, "recorded_at")], stamp(batch["cutoff_at"])):
        batch["status"] = "skipped_stale"
        return
    batch["status"] = "frozen"
    rows = batch["rows"]
    ordered = sorted(rows, key=lambda k: (-rows[k]["probability"], rows[k]["odds"], k, selection_key(rows[k])))
    eligible = [k for k in ordered if passes(rows[k], .60)]
    strategies = {}
    if batch["kind"] == "daily":
        strategies["daily_two"] = [ticket(eligible[:2], 1.0, rows)] if len(eligible) >= 2 else []
    else:
        for name, threshold in (("recommended_singles", None), ("p58_singles", .58), ("p60_singles", .60)):
            ids = ordered if threshold is None else [k for k in ordered if passes(rows[k], threshold)]
            strategies[name] = [ticket([k], .25 / len(ids), rows) for k in ids]
        strategies["slot_two"] = [ticket(eligible[:2], .25, rows)] if len(eligible) >= 2 else []
    batch["tickets"] = strategies


def settle(batch, odds, now):
    if batch["status"] != "frozen":
        return
    settle_history(batch["rows"], odds, now)
    for tickets in batch["tickets"].values():
        for item in tickets:
            legs = [batch["rows"][k] for k in item["legs"]]
            if all(r.get("result") in ("hit", "miss", "void") for r in legs):
                item["return_units"] = item["stake"] * math.prod(
                    0 if r["result"] == "miss" else 1 if r["result"] == "void" else r["odds"] for r in legs)


def update_experiment(payload, previous, odds, now):
    state = deepcopy(previous or {})
    if state and state.get("policy") != POLICY:
        return state
    times = {k: stamp(payload.get(k)) for k in TIME_FIELDS}
    valid = not payload.get("partial") and not payload.get("error") and fresh(times.values(), now)
    if not state:
        state = {"policy": POLICY, "paper_only": True, "days": {}, "status": "awaiting_fresh_source"}
    if "start_at" not in state and valid:
        start = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        state.update(registered_at=now.isoformat(), start_at=start.isoformat(),
                     end_at=(start + timedelta(days=28)).isoformat(), daily_budget=1.0,
                     slot_hours=6, thresholds=[.58, .60])
    if "start_at" not in state:
        return state
    for day in state["days"].values():
        for batch in day["batches"].values():
            freeze(batch, now)
            settle(batch, odds, now)
    start, end = stamp(state["start_at"]), stamp(state["end_at"])
    if valid and now < end:
        state["last_fresh_observed_at"] = now.isoformat()
        candidates = payload.get("candidates") or []
        chosen = highlights(candidates)
        groups = {}
        for row in candidates:
            kickoff = stamp(row.get("kickoff_at"))
            if (not kickoff or not start <= kickoff < end or kickoff - timedelta(minutes=30) <= now
                    or not row.get("home") or not row.get("away") or not 0 < probability(row) < 1
                    or number(row.get("odds")) <= 1):
                continue
            date = kickoff.astimezone(KST).date().isoformat()
            key = event_key(row)
            day = state["days"].setdefault(date, {"observed": {}, "batches": {}})
            recommended = selection_key(row) in chosen
            seen = day["observed"].setdefault(key, {"recommended": False, "p58": False, "p60": False})
            seen["recommended"] |= recommended
            seen["p58"] |= recommended and probability(row) >= .58 and number(row["odds"]) >= 1.50
            seen["p60"] |= recommended and probability(row) >= .60 and number(row["odds"]) >= 1.50
            if not recommended:
                continue
            slot = kickoff.astimezone(KST).hour // 6
            for name in ("daily", f"slot{slot}"):
                groups.setdefault((date, name), []).append(row)
        for (date, name), rows in groups.items():
            batches = state["days"][date]["batches"]
            old = batches.get(name)
            if old and old["status"] != "draft":
                continue
            consumed = {k for n, b in batches.items() if n != "daily" and n != name and b["status"] == "frozen"
                        for k in b["rows"]} if name != "daily" else set()
            rows = [r for r in rows if event_key(r) not in consumed]
            if not rows:
                continue
            cutoff = min(stamp(r["kickoff_at"]) for r in rows) - timedelta(minutes=30)
            if old:
                cutoff = min(cutoff, stamp(old["cutoff_at"]))
            recorded = {}
            for row in sorted(rows, key=lambda r: (-probability(r), number(r["odds"]), event_key(r), selection_key(r))):
                key = event_key(row)
                recorded.setdefault(key, {**{k: row.get(k) for k in FIELDS}, "id": key,
                                           "probability": probability(row), "odds": number(row["odds"]),
                                           "probability_source": row.get("probability_source") or "unknown"})
            batches[name] = {"kind": "daily" if name == "daily" else "slot", "status": "draft",
                             "cutoff_at": cutoff.isoformat(), "recorded_at": now.isoformat(),
                             **{k: t.isoformat() for k, t in times.items()}, "rows": recorded}
    state["status"] = "observation_closed_settlement_pending" if now >= end else "recording"
    state["summary"] = summarize(state, now)
    return state


def summarize(state, now):
    completed_days = max(0, min(28, (now.astimezone(KST).date() - stamp(state["start_at"]).astimezone(KST).date()).days))
    result = {}
    for name in STRATEGIES:
        selected = set()
        staked = settled_stake = profit = pending_stake = 0.0
        pending = settled_count = 0
        closed_profit = peak = drawdown = 0.0
        fully_settled_days = 0
        for date, day in sorted(state["days"].items()):
            daily_profit = 0.0
            unresolved = False
            for batch in day["batches"].values():
                for item in batch.get("tickets", {}).get(name, []):
                    selected.update((date, k) for k in item["legs"])
                    staked += item["stake"]
                    if "return_units" not in item:
                        pending += 1
                        pending_stake += item["stake"]
                        unresolved = True
                    else:
                        settled_count += 1
                        settled_stake += item["stake"]
                        pnl = item["return_units"] - item["stake"]
                        profit += pnl
                        daily_profit += pnl
            if date < now.astimezone(KST).date().isoformat() and not unresolved:
                fully_settled_days += 1
                closed_profit += daily_profit
                peak = max(peak, closed_profit)
                drawdown = max(drawdown, peak - closed_profit)
        result[name] = {"selected_events": len(selected), "tickets_settled": settled_count,
                        "tickets_pending": pending, "stake_units": staked, "pending_stake_units": pending_stake,
                        "settled_stake_units": settled_stake, "profit_units": profit,
                        "roi_on_settled_stake": profit / settled_stake if settled_stake else None,
                        "elapsed_daily_budget_units": completed_days, "closed_day_profit_units": closed_profit,
                        "closed_day_drawdown_units_excluding_pending_days": drawdown,
                        "observed_closed_settled_days": fully_settled_days}
    observed = [r for day in state["days"].values() for r in day["observed"].values()]
    coverage = {"observed_events": len(observed),
                **{k: sum(bool(r[k]) for r in observed) for k in ("recommended", "p58", "p60")},
                "skipped_stale_batches": sum(b["status"] == "skipped_stale" for d in state["days"].values() for b in d["batches"].values())}
    for stats in result.values():
        stats["selection_rate_of_observed"] = stats["selected_events"] / len(observed) if observed else None
        stats["observed_but_not_selected"] = len(observed) - stats["selected_events"]
    coverage["observed_dates"] = len(state["days"])
    coverage["elapsed_dates_without_candidates"] = max(0, completed_days - sum(
        date < now.astimezone(KST).date().isoformat() for date in state["days"]))
    return {"strategies": result, "coverage": coverage}
