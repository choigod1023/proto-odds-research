"""Offline singles: paired monthly-budget ablation, never alters the paper wallet.

Input: JSON list (or {records: [...]}) of cohort, league, model, event_id,
kickoff, odds, net_per_unit; optional probability and source-proven settled_at.
Date-only rows share one decision batch at local midnight. Otherwise T-30 is
an explicitly approximate placement time. Unknown settlements use next-day or
second-day midnight, reported separately. KST purchase-month P/L plus ALL open
stakes controls admission, matching the forward wallet's conservative budget.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import heapq
import gzip
import json
import math
from pathlib import Path
import random

KST = timezone(timedelta(hours=9))
STAKE = 10_000
RULES = ("all", "probability_55", "positive_ev", "ev_03")


def stamp(value):
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=KST)
    return t.astimezone(KST)


def normalize(records):
    out, seen = [], set()
    for raw in records:
        r = dict(raw)
        for key in ("cohort", "league", "model", "event_id", "kickoff"):
            if not isinstance(r.get(key), str) or not r[key]:
                raise ValueError(f"missing {key}")
        key = tuple(r[k] for k in ("cohort", "league", "model", "event_id"))
        if key in seen:
            raise ValueError(f"duplicate model/event {key}")
        seen.add(key)
        for field in ("odds", "net_per_unit"):
            v = r[field]
            if isinstance(v, bool) or not math.isfinite(float(v)):
                raise ValueError(f"invalid {field}")
            r[field] = float(v)
        if r["odds"] < 1 or not -1 <= r["net_per_unit"] <= r["odds"] - 1 + 1e-8:
            raise ValueError("invalid payoff")
        if r.get("probability") is not None:
            r["probability"] = float(r["probability"])
            if not math.isfinite(r["probability"]) or not 0 <= r["probability"] <= 1:
                raise ValueError("invalid probability")
        kickoff = stamp(r["kickoff"])
        date_only = len(r["kickoff"]) == 10
        decision = kickoff if date_only else kickoff - timedelta(minutes=30)
        settlement = stamp(r["settled_at"]) if r.get("settled_at") else None
        if settlement is not None and settlement < kickoff:
            raise ValueError("settlement before kickoff")
        r.update(_kickoff=kickoff, _decision=decision, _settlement=settlement,
                 _date_only=date_only)
        out.append(r)
    return out


def eligible(r, rule):
    if rule not in RULES:
        raise ValueError("unknown rule")
    if rule == "all":
        return True
    p = r.get("probability")
    if p is None:
        return False
    edge = Decimal(str(p)) * Decimal(str(r["odds"])) - 1
    return {"probability_55": p >= .55, "positive_ev": edge > 0,
            "ev_03": edge >= Decimal(".03")}[rule]


def simulate(records, cap=50_000, settlement_lag_days=1, rule="all"):
    if cap is not None and (isinstance(cap, bool) or cap < 0):
        raise ValueError("cap must be nonnegative or None")
    if settlement_lag_days not in (1, 2):
        raise ValueError("settlement lag must be one or two days")
    rows = normalize(records)
    monthly, reserved = defaultdict(int), 0
    queue, taken, skipped = [], [], []
    equity = peak = drawdown = 0
    peak_reserved = minimum_starting_bankroll = 0
    settlements = []
    known = sum(r["_settlement"] is not None for r in rows)
    if rows:
        day = min(r["_decision"] for r in rows).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = max(r["_decision"] for r in rows)
        while day <= end:
            monthly[day.strftime("%Y-%m")] = 0
            day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)

    def settle(until):
        nonlocal reserved, equity, peak, drawdown
        while queue and (until is None or queue[0][0] <= until):
            at = queue[0][0]
            batch = []
            while queue and queue[0][0] == at:
                _, _, t = heapq.heappop(queue)
                batch.append(t)
            for t in batch:
                monthly[t["month"]] += t["net"]
                reserved -= STAKE
            change = sum(t["net"] for t in batch)
            equity += change
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
            settlements.append({"at": at.isoformat(), "net": change})

    for r in sorted(rows, key=lambda r: (r["_decision"], r["event_id"])):
        at, month = r["_decision"], r["_decision"].strftime("%Y-%m")
        settle(at)
        if not eligible(r, rule):
            continue
        # No outcomes are read by the admission or eligibility conditions.
        if cap is not None and cap + monthly[month] - reserved < STAKE:
            skipped.append(r["event_id"])
            continue
        settle_at = r["_settlement"]
        if settle_at is None:
            settle_at = r["_kickoff"].replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=settlement_lag_days)
        net = int((Decimal(str(r["net_per_unit"])) * STAKE).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        ticket = dict(id=r["event_id"], at=at.isoformat(), settled_at=settle_at.isoformat(),
                      month=month, net=net, day=r["_kickoff"].date().isoformat())
        taken.append(ticket)
        reserved += STAKE
        peak_reserved = max(peak_reserved, reserved)
        minimum_starting_bankroll = max(minimum_starting_bankroll, reserved-equity)
        heapq.heappush(queue, (settle_at, len(taken), ticket))
    settle(None)
    stake = len(taken) * STAKE
    return dict(n=len(taken), opportunities=len(rows), coverage=len(taken)/len(rows) if rows else None,
                stake=stake, net=equity, roi=equity/stake if stake else None,
                wins=sum(t["net"] > 0 for t in taken), losses=sum(t["net"] < 0 for t in taken),
                refunds=sum(t["net"] == 0 for t in taken), monthly_net=dict(sorted(monthly.items())),
                positive_months=sum(n > 0 for n in monthly.values()), months=len(monthly),
                worst_month=min(monthly.values(), default=0), max_drawdown=drawdown,
                maximum_pending_stake=peak_reserved,
                minimum_starting_bankroll=minimum_starting_bankroll,
                cap=cap, settlement_lag_days=settlement_lag_days, rule=rule,
                source_proven_settlement_count=known, assumed_settlement_count=len(rows)-known,
                date_only_count=sum(r["_date_only"] for r in rows),
                tickets=taken, skipped_by_cap=skipped, settlements=settlements)


def cap_comparison(records, lag=1):
    capped, uncapped = simulate(records, settlement_lag_days=lag), simulate(records, cap=None, settlement_lag_days=lag)
    capped_ids = {x["id"] for x in capped["tickets"]}
    added = {t["id"]: t for t in uncapped["tickets"] if t["id"] not in capped_ids}
    assert len(added) == uncapped["n"] - capped["n"]
    extra_net = sum(t["net"] for t in added.values())
    assert extra_net == uncapped["net"] - capped["net"]
    return dict(capped=capped, uncapped=uncapped, extra_bets=len(added), extra_net=extra_net,
                extra_roi=extra_net/(len(added)*STAKE) if added else None,
                roi_delta=(uncapped["roi"]-capped["roi"]) if capped["n"] else None)


def bootstrap_difference(left, right, iterations=2000):
    """Paired kickoff-day blocks, conditional exploratory interval, not future EV."""
    a, b = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for result, dest in ((left, a), (right, b)):
        for t in result["tickets"]:
            dest[t["day"]][0] += t["net"]
            dest[t["day"]][1] += STAKE
    days = sorted(set(a) | set(b))
    if len(days) < 2:
        return None
    rng, diffs = random.Random(20261030), []
    for _ in range(iterations):
        an = ast = bn = bst = 0
        for day in rng.choices(days, k=len(days)):
            x, y = a[day], b[day]
            an += x[0]; ast += x[1]; bn += y[0]; bst += y[1]
        if ast and bst:
            diffs.append(bn/bst-an/ast)
    if not diffs:
        return None
    diffs.sort()
    return dict(days=len(days), samples=len(diffs), low=diffs[int(.025*len(diffs))],
                high=diffs[min(len(diffs)-1, int(.975*len(diffs)))],
                caveat="Conditional kickoff-day bootstrap; cap path not re-simulated; not a formal significance test or fresh holdout.")


def filter_experiment(records):
    days = sorted({stamp(r["kickoff"]).date().isoformat() for r in records})
    if len(days) < 6:
        return dict(status="insufficient_dates")
    split = days[max(1, 2*len(days)//3)]
    train = [r for r in records if stamp(r["kickoff"]).date().isoformat() < split]
    test = [r for r in records if stamp(r["kickoff"]).date().isoformat() >= split]
    train_results = {rule: simulate(train, cap=None, rule=rule) for rule in RULES}
    qualified = [(result["roi"], -i, rule) for i, (rule, result) in enumerate(train_results.items())
                 if result["n"] >= max(30, math.ceil(len(train)*.2))]
    if not qualified:
        return dict(status="insufficient_training_bets", split_date=split)
    selected = max(qualified)[2]
    baseline, candidate = simulate(test, cap=None), simulate(test, cap=None, rule=selected)
    return dict(status="exploratory_previously_seen_archive", split_date=split,
                selected_on_train_only=selected, train={k: compact(v) for k,v in train_results.items()},
                test_all=compact(baseline), test_selected=compact(candidate),
                test_roi_delta_interval=bootstrap_difference(baseline, candidate))


def compact(value):
    return {k:v for k,v in value.items() if k not in ("tickets", "settlements", "skipped_by_cap")}


def matched_model_selection(records):
    """Train-only selection over saved models/rules; never fits a new predictor."""
    groups = defaultdict(lambda: defaultdict(list))
    for r in records:
        groups[(r["league"], r["cohort"])][r["model"]].append(r)
    result = {}
    for key, models in sorted(groups.items()):
        models = {m:rows for m,rows in models.items() if all(r.get("eligible_for_policy_selection", True) for r in rows)}
        if len(models) < 2:
            continue
        ids = set.intersection(*({r["event_id"] for r in rows} for rows in models.values()))
        if len(ids) < 60:
            continue
        matched = {m:[r for r in rows if r["event_id"] in ids] for m,rows in models.items()}
        calendars = {tuple(sorted((r["event_id"], stamp(r["kickoff"]).date().isoformat()) for r in rows))
                     for rows in matched.values()}
        if len(calendars) != 1:
            raise ValueError("matched models disagree on event date")
        days = sorted({stamp(r["kickoff"]).date().isoformat() for rows in matched.values() for r in rows})
        if len(days) < 6:
            continue
        split = days[2*len(days)//3]
        train = {m:[r for r in rows if stamp(r["kickoff"]).date().isoformat() < split] for m,rows in matched.items()}
        test = {m:[r for r in rows if stamp(r["kickoff"]).date().isoformat() >= split] for m,rows in matched.items()}
        candidates = []
        for model, rows in sorted(train.items()):
            for i, rule in enumerate(RULES):
                r = simulate(rows, cap=None, rule=rule)
                if r["n"] >= max(30, math.ceil(.2*len(rows))):
                    candidates.append((r["roi"], model, -i, rule, r))
        if not candidates:
            continue
        _, model, _, rule, best = sorted(candidates, key=lambda x:(-x[0], x[1], -x[2]))[0]
        chosen = simulate(test[model], cap=None, rule=rule)
        comparisons = {}
        for m, rows in sorted(test.items()):
            baseline = simulate(rows, cap=None)
            comparisons[m] = dict(unfiltered_test=compact(baseline),
                                  selected_minus_baseline_roi=chosen["roi"]-baseline["roi"] if chosen["n"] and baseline["n"] else None,
                                  conditional_day_interval=bootstrap_difference(baseline, chosen))
        result["/".join(key)] = dict(status="exploratory_reused_archive_no_promotion", split_date=split,
                matched_events=len(ids), train_n=len(next(iter(train.values()))), test_n=len(next(iter(test.values()))),
                eligible_training_configurations=len(candidates), selected_model=model, selected_rule=rule,
                train=compact(best), test=compact(chosen), comparisons=comparisons)
    return result


def build_report(records):
    normalize(records)
    groups = defaultdict(list)
    for r in records:
        groups[(r["league"], r["cohort"], r["model"])].append(r)
    reports = {}
    for key, rows in sorted(groups.items()):
        comparisons = []
        for lag in (1, 2):
            result = cap_comparison(rows, lag)
            comparisons.append({**{k:v for k,v in result.items() if k not in ("capped", "uncapped")},
                                "capped":compact(result["capped"]), "uncapped":compact(result["uncapped"]),
                                "roi_delta_interval":bootstrap_difference(result["capped"], result["uncapped"])})
        reports["/".join(key)] = dict(from_date=min(r["kickoff"] for r in rows), to_date=max(r["kickoff"] for r in rows),
                                      eligible_for_policy_selection=all(r.get("eligible_for_policy_selection", True) for r in rows),
                                      cap_sensitivity=comparisons, filters=filter_experiment(rows))
    return dict(protocol="league-loss-cap-ablation-v1", stake=STAKE, capped_limit=50000,
                unit="singles", production_changed=False, research_only=True,
                budget_scope="each league/cohort/model is an independent hypothetical wallet; do not sum capped league accounts",
                sources=sorted({r.get("source_file", "unspecified") for r in records}),
                record_count=len(records), groups=reports,
                train_only_model_selection=matched_model_selection(records))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs", nargs="+", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    records, hashes = [], {}
    for path in args.inputs:
        raw = path.read_bytes(); hashes[str(path)] = hashlib.sha256(raw).hexdigest()
        payload = gzip.decompress(raw) if path.suffix == ".gz" else raw
        if ".jsonl" in path.suffixes:
            records.extend(json.loads(line) for line in payload.decode("utf-8-sig").splitlines() if line.strip())
        else:
            doc = json.loads(payload.decode("utf-8-sig"))
            records.extend(doc if isinstance(doc, list) else doc["records"])
    report = build_report(records); report["input_sha256"] = hashes
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    print(json.dumps({"groups":len(report["groups"]), "records":len(records), "output":str(args.output)}))


if __name__ == "__main__":
    main()
