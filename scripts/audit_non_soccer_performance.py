"""Audit an explicitly scoped, read-only operating-ledger export (no network)."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import gzip
import hashlib
import json
from math import sqrt
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from featured_pick import performance


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def source(prediction):
    hybrid = prediction.get("features", {}).get("tipster_hybrid") or {}
    if hybrid.get("selection_source") == "market_fallback" or hybrid.get("status") == "market_fallback":
        return "market_fallback"
    policy = (prediction.get("model") or {}).get("selection_policy") or hybrid.get("version")
    if policy == "tipster-vote-v1" and hybrid.get("status") == "applied" and type(hybrid.get("authors")) is int and hybrid["authors"] > 0:
        return "tipster_vote"
    return "unknown"


def stats(rows):
    counts = Counter(row["result"] for row in rows)
    decided = counts["hit"] + counts["miss"]
    settled = decided + counts["void"]
    net = sum((Decimal(str(row["net_units"])) for row in rows if row["result"] != "pending"), Decimal(0))
    interval = None
    if decided:
        z = 1.959963984540054
        p = counts["hit"] / decided
        mid = (p + z*z / (2*decided)) / (1 + z*z/decided)
        half = z*sqrt(p*(1-p)/decided + z*z/(4*decided*decided)) / (1 + z*z/decided)
        interval = [mid-half, mid+half]
    return dict(captured=len(rows), hits=counts["hit"], misses=counts["miss"], refunds=counts["void"],
                pending=counts["pending"], decided=decided, settled=settled,
                hit_rate=counts["hit"]/decided if decided else None, wilson95=interval,
                net_units=str(net), roi=float(net/settled) if settled else None)


def grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    return {name: stats(values) for name, values in sorted(groups.items())}


def audit(rows):
    predictions = [row for row in rows if row["record_type"] == "prediction"]
    if len({p["event_id"] for p in predictions}) != len(predictions):
        raise ValueError("Export needs one canonical prediction per event; do not count revisions as games")
    settlements = {}
    for row in rows:
        if row["record_type"] == "settlement":
            previous = settlements.get(row["snapshot_id"])
            if previous is None or row["ledger_sequence"] > previous["ledger_sequence"]:
                settlements[row["snapshot_id"]] = row
    ordinary, markets, featured, late = [], [], {}, []
    for p in predictions:
        if p["kickoff"] < "2026-10-28":
            continue
        fixture, payload = p["features"]["fixture"], p["predictions"]
        outcome = (settlements.get(p["snapshot_id"]) or {}).get("outcome") or {}
        before = all(stamp(p[k]) < stamp(p["kickoff"])-timedelta(minutes=30)
                     for k in ("as_of", "captured_at", "market_observed_at"))
        if not before:
            late.append(p["snapshot_id"])
        base = dict(sport=fixture.get("sport"), league=fixture.get("league"),
                    policy=p["model"].get("selection_policy"), source=source(p), before_t30=before)
        result = outcome.get("result") or "pending"
        if result not in {"hit", "miss", "void", "pending"}:
            raise ValueError("Unknown primary grade")
        if result in {"hit", "miss", "void"}:
            assert outcome["selection_id"] == payload["selection_id"]
        net = Decimal(str(payload["odds"]))-1 if result == "hit" else Decimal(-1) if result == "miss" else Decimal(0)
        ordinary.append(dict(base, market=payload["market"], result=result, net_units=str(net)))
        market_items = (payload.get("market_recommendations") or {}).get("items") or []
        for item in market_items:
            grade = (outcome.get("market_recommendation_results") or {}).get(item["offer_id"]) or {}
            if grade:
                assert all(grade.get(k) == item.get(k) for k in
                           ("selection_id", "offer_id", "market", "line", "settlement_rule"))
                assert Decimal(str(grade["odds"])) == Decimal(str(item["odds"]))
            if before:
                markets.append(dict(base, market=item["market"], source=item.get("selection_source"),
                                    model=item.get("model"), result=grade.get("result") or "pending",
                                    net_units=grade.get("net_units", "0")))
        pick = payload.get("featured_pick")
        if pick and before:
            grade = outcome.get("featured_pick_result")
            if grade:
                assert all(grade.get(k) == pick.get(k) for k in
                           ("selection_id", "offer_id", "market", "line", "settlement_rule"))
                assert Decimal(str(grade["odds"])) == Decimal(str(pick["odds"]))
            pick_source = source(p)
            if pick["market"] not in {"승패", "승무패"}:
                exact = [i for i in market_items if all(i.get(k) == pick.get(k) for k in
                         ("selection_id", "offer_id", "market", "line", "odds", "settlement_rule"))]
                pick_source = exact[0].get("selection_source") if len(exact) == 1 else "unknown"
            featured[p["event_id"]] = dict(pick=pick, result=grade, fixture=fixture, selection_source=pick_source,
                                           selection_policy=base["policy"])
    current = [row for row in ordinary if row["policy"] == "tipster-vote-v1"]
    eligible = [row for row in current if row["before_t30"]]
    return dict(
        primary_all_policies=dict(by_sport=grouped(ordinary, "sport"), by_policy=grouped(ordinary, "policy")),
        current_primary=dict(by_sport=grouped(current, "sport"), by_source=grouped(current, "source")),
        current_primary_pre_t30=dict(by_sport=grouped(eligible, "sport"), by_league=grouped(eligible, "league"),
                                    by_source=grouped(eligible, "source")),
        markets_pre_t30=dict(overall=stats(markets), by_market=grouped(markets, "market"),
                             by_model=grouped(markets, "model"), by_source=grouped(markets, "source")),
        featured_pre_t30=performance(featured), late_or_missing_cutoff_snapshots=late,
        limits=["API-selected snapshot subset, not all operating predictions or a full verified hash chain",
                "10/28 onward only; mixed legacy policy and current policy kept separate",
                "T-30 is a timestamp check, not independent authentication of original source bytes",
                "Official grades are stored ledger outcomes, not independently re-fetched result originals",
                "Different market and odds cohorts are descriptive, not paired model superiority evidence"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    blob = args.export.read_bytes()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    if hashlib.sha256(blob).hexdigest() != manifest["sha256"]:
        raise ValueError("Export hash differs from source receipt")
    rows = [json.loads(line) for line in gzip.decompress(blob).decode("utf8").splitlines()]
    if len(rows) != manifest["rows"]:
        raise ValueError("Export row count differs from receipt")
    result = audit(rows)
    result["source"] = {k: manifest[k] for k in ("sha256", "captured_at", "rows", "selection", "complete_ledger", "full_chain_verified")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    print(json.dumps({k: result[k] for k in ("current_primary_pre_t30", "featured_pre_t30")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
