"""Offline replay of preserved recommendations; never changes production or auto-promotes."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import hashlib
import heapq
import itertools
import json
import math
from pathlib import Path

KST = timezone(timedelta(hours=9))
STAKE = 10000
LIMIT = 50000
PROTOCOL = "interest-shadow-v1"

def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(KST)

def payout(odds):
    value = math.prod(Decimal(str(o)) for o in odds)
    return int(value.quantize(Decimal(".01"), rounding=ROUND_DOWN)
               .quantize(Decimal(".1"), rounding=ROUND_CEILING) * STAKE)

def eligible(doc, cutoff, actual=True):
    rows, excluded = [], Counter()
    for item in doc.get("recommendation_history", {}).values():
        try:
            r = dict(item)
            r["t"] = max(stamp(r["recorded_at"]), stamp(r["published_at"]))
            r["k"] = stamp(r["kickoff_at"])
            r["s"] = stamp(r["settled_at"]) if r.get("settled_at") else None
            r["p"], r["o"] = float(r["probability"]), float(r["odds"])
            r["event"] = (r["k"], r["sport"], r["league"], r["home"], r["away"])
            if not math.isfinite(r["p"]) or not 0 < r["p"] < 1 or not math.isfinite(r["o"]) or r["o"] <= 1:
                raise ValueError("Invalid probability/odds")
            if r["t"] > cutoff or r["t"] >= r["k"] - timedelta(minutes=30):
                excluded["unavailable_or_after_freeze"] += 1
                continue
            if r["s"] and r["s"] < r["k"]:
                excluded["settlement_before_kickoff"] += 1
                continue
            if actual and r.get("recommended") is not True:
                excluded["not_historically_recommended"] += 1
                continue
            if not actual and not (r["p"] >= .55 and r["o"] >= 1.5):
                excluded["outside_proxy_threshold"] += 1
                continue
            rows.append(r)
        except (ValueError, TypeError, KeyError, AttributeError):
            excluded["invalid"] += 1
    return rows, dict(excluded)

def replay(rows, cutoff, weekly=2):
    if weekly not in (2, 3):
        raise ValueError("weekly must be 2 or 3")
    balances = defaultdict(int)
    active, queue, tickets = {}, [], []
    used, days = set(), set()
    weeks = Counter()
    equity = peak = drawdown = 0
    def settle(now):
        nonlocal equity, peak, drawdown
        while queue and queue[0][0] <= now:
            at, i = heapq.heappop(queue)
            ticket = active.pop(i)
            net = ticket["payout"] - STAKE
            balances[ticket["month"]] += net
            equity += net
            peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
            ticket["settled_at"] = at.isoformat()
            ticket["net"] = net
    for now in sorted({r["t"] for r in rows}):
        settle(now)
        month, week = now.strftime("%Y-%m"), now.isocalendar()[:2]
        if now.date() in days or weeks[week] >= weekly:
            continue
        if balances[month] - STAKE * (len(active) + 1) < -LIMIT:
            continue
        options = [r for r in rows if r["t"] <= now and
                   r["k"] > now + timedelta(minutes=30) and
                   r["k"].date() == now.date() and r["event"] not in used]
        pairs = [(a, b) for a, b in itertools.combinations(options, 2)
                 if a["event"] != b["event"] and a["round"] == b["round"]]
        if not pairs:
            continue
        a, b = min(pairs, key=lambda ab: (-ab[0]["p"]*ab[1]["p"],
                                          tuple(sorted((ab[0]["id"], ab[1]["id"])))))
        i = len(tickets)
        ticket = {"at": now.isoformat(), "month": month,
                  "ids": [a["id"], b["id"]], "stake": STAKE,
                  "policies": [a.get("policy"), b.get("policy")],
                  "available_loss_budget_before": LIMIT + balances[month] - STAKE*len(active)}
        tickets.append(ticket)
        active[i] = ticket
        used.update((a["event"], b["event"]))
        days.add(now.date())
        weeks[week] += 1
        pair = (a, b)
        misses = [r for r in pair if r.get("result_source") == "official"
                  and r.get("result") == "miss" and r["s"]]
        complete = all(r.get("result_source") == "official" and
                       r.get("result") in ("hit", "miss", "void") and r["s"] for r in pair)
        if misses or complete:
            at = min(r["s"] for r in misses) if misses else max(r["s"] for r in pair)
            ticket["payout"] = 0 if misses else payout([r["o"] if r["result"] == "hit" else 1 for r in pair])
            heapq.heappush(queue, (at, i))
    settle(cutoff)
    settled = [t for t in tickets if "net" in t]
    # Do not expose future payouts for unresolved tickets.
    for ticket in active.values():
        ticket.pop("payout", None)
    net = sum(t["net"] for t in settled)
    assert all(v >= -LIMIT for v in balances.values())
    assert len(used) == 2*len(tickets)
    assert net == sum(balances.values())
    return {"tickets": len(tickets), "settled": len(settled),
            "net": net, "pending_stake": STAKE*len(active),
            "roi": net/(STAKE*len(settled)) if settled else None,
            "monthly_net_by_placement": dict(balances),
            "loss_months": sum(v < 0 for v in balances.values()),
            "limit_months": sum(v == -LIMIT for v in balances.values()),
            "max_drawdown": drawdown, "ticket_log": tickets}

def audit(doc):
    cutoff = stamp(doc["generated_at"])
    report = {"protocol": PROTOCOL, "as_of": cutoff.isoformat(),
              "mode": "retrospective_snapshot_replay_not_prospective",
              "monthly_loss_limit": LIMIT, "stake": STAKE,
              "verification_status": "unvalidated",
              "verified_recommendations": 0, "cohorts": {}}
    for name, actual in (("historical_flags_mixed_policies", True), ("p55_proxy", False)):
        rows, excluded = eligible(doc, cutoff, actual)
        resolved = [r for r in rows if r["s"] and r["s"] <= cutoff
                    and r.get("result_source") == "official"
                    and r.get("result") in ("hit", "miss")]
        calibration = {
            "n": len(resolved),
            "mean_probability": sum(r["p"] for r in resolved)/len(resolved) if resolved else None,
            "hit_rate": sum(r["result"] == "hit" for r in resolved)/len(resolved) if resolved else None,
            "brier": sum((r["p"]-int(r["result"] == "hit"))**2 for r in resolved)/len(resolved) if resolved else None,
        }
        report["cohorts"][name] = {"eligible": len(rows), "excluded": excluded,
            "descriptive_calibration_not_holdout": calibration,
            "weekly": {str(w): replay(rows, cutoff, w) for w in (2,3)}}
    report["verified_cash_control"] = {"tickets": 0, "net": 0, "roi": None}
    report["limitations"] = [
        "Final preserved snapshots are not the complete historical opportunity set.",
        "Historical flags mix policy versions; p55 proxy does not reconstruct all current eligibility rules.",
        "No automatic verification or promotion; cash avoidance is not evidence of profitability.",
        "Monthly budgets include all pending stakes; results use official observed settlement timestamps.",
        "Future shadow performance requires snapshots captured before decisions, not future final snapshots."]
    return report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Read-only saved recommendation JSON")
    parser.add_argument("--output", type=Path, help="New report path; never overwrite existing evidence")
    args = parser.parse_args()
    raw = args.snapshot.read_bytes()
    report = audit(json.loads(raw))
    report["source_sha256"] = hashlib.sha256(raw).hexdigest()
    result = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(result+"\n")
    else:
        print(result)

if __name__ == "__main__":
    main()
