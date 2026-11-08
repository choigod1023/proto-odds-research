"""Replay frozen candidate selection only; never write an operating ledger."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import random

RULES = ("price_first", "probability_first", "primary_only")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def rank(candidates, prefer_price):
    return min(candidates, key=lambda c: (
        c["odds"] < 1.5 if prefer_price else False,
        -c["probability"], -(c["probability"]*c["odds"]-1), c["selection_id"]))


def frozen_candidates(prediction):
    payload = prediction["predictions"]
    options = prediction["features"]["options"]
    primary = {k: payload.get(k) for k in ("selection_id", "offer_id", "market", "label", "line", "odds", "selection")}
    primary.update(label=primary.get("label") or "", settlement_rule="official_winner", kind="primary")
    candidates = [primary]
    for item in (payload.get("market_recommendations") or {}).get("items") or []:
        if item.get("automatic_recommendation") is True and item.get("candidate_only") is not True:
            candidates.append(dict(item, kind="line"))
    for candidate in candidates:
        exact = [o for o in options if (o["selection_id"], o["offer_id"]) ==
                 (candidate["selection_id"], candidate["offer_id"])]
        if len(exact) != 1:
            raise ValueError("Candidate missing or duplicated in frozen prices")
        option = exact[0]
        if any(option.get(k) != candidate.get(k) for k in ("market", "line", "odds", "selection")):
            raise ValueError("Candidate differs from frozen product")
        if (option.get("label") or "") != (candidate.get("label") or ""):
            raise ValueError("Candidate label differs from frozen product")
        peers = [o for o in options if (o["market"], o.get("label") or "", o.get("line")) ==
                 (candidate["market"], candidate.get("label") or "", candidate.get("line"))]
        expected = {"승패": {"홈", "원정"}, "승무패": {"홈", "무", "원정"},
                    "핸디캡": {"핸디홈", "핸디원정"}, "언더오버": {"언더", "오버"}}[candidate["market"]]
        if candidate["market"] == "핸디캡" and len(peers) == 3:
            expected = expected | {"핸디무"}
        if len(peers) != len(expected) or {p["selection"] for p in peers} != expected:
            raise ValueError("Incomplete or ambiguous frozen price vector")
        if any(type(p["odds"]) not in (int, float) or not math.isfinite(p["odds"]) or p["odds"] <= 1 for p in peers):
            raise ValueError("Invalid frozen price")
        candidate["probability"] = 1/(candidate["odds"]*sum(1/p["odds"] for p in peers))
    return candidates


def grade(candidate, outcome):
    if candidate["kind"] == "primary":
        result = outcome.get("result")
        if result is None:
            return None
        if outcome.get("selection_id") != candidate["selection_id"]:
            raise ValueError("Winner settlement selection mismatch")
    else:
        saved = (outcome.get("market_recommendation_results") or {}).get(candidate["offer_id"])
        if not saved:
            return None
        if any(saved.get(k) != candidate.get(k) for k in
               ("selection_id", "offer_id", "market", "line", "settlement_rule")):
            raise ValueError("Line settlement product mismatch")
        if Decimal(str(saved["odds"])) != Decimal(str(candidate["odds"])):
            raise ValueError("Line settlement price mismatch")
        result = saved["result"]
    if result not in {"hit", "miss", "void"}:
        raise ValueError("Unsupported grade; do not substitute a score or another market")
    net = Decimal(str(candidate["odds"]))-1 if result == "hit" else Decimal(-1) if result == "miss" else Decimal(0)
    if candidate["kind"] == "line" and Decimal(str(saved["net_units"])) != net:
        raise ValueError("Line settlement payoff mismatch")
    return dict(result=result, net_units=str(net))


def metrics(rows, rule):
    picks = [row["choices"][rule] for row in rows]
    settled = [p for p in picks if p["grade"] is not None]
    decided = [p for p in settled if p["grade"]["result"] != "void"]
    net = sum((Decimal(p["grade"]["net_units"]) for p in settled), Decimal(0))
    equity = peak = drawdown = Decimal(0)
    for pick in settled:
        equity += Decimal(pick["grade"]["net_units"])
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
    hits = sum(p["grade"]["result"] == "hit" for p in decided)
    return dict(opportunities=len(rows), settled=len(settled), decided=len(decided), hits=hits,
                pending=len(picks)-len(settled), refunds=len(settled)-len(decided),
                hit_rate=hits/len(decided) if decided else None,
                roi=float(net/len(settled)) if settled else None, net_units=str(net),
                average_odds=sum(p["odds"] for p in settled)/len(settled) if settled else None,
                maximum_drawdown_units=str(drawdown),
                by_market={name: sum(p["market"] == name for p in settled) for name in sorted({p["market"] for p in settled})})


def paired_interval(rows, rule, trials=2000):
    if not rows:
        return dict(n=0, dates=0, low_information=True, hit_rate_delta95=None, roi_delta95=None)
    blocks = defaultdict(list)
    for row in rows:
        blocks[row["date_kst"]].append(row)
    dates = sorted(blocks)
    randomizer = random.Random(20261108)
    values = {"hit_rate": [], "roi": []}
    for _ in range(trials):
        sample = [row for date in randomizer.choices(dates, k=len(dates)) for row in blocks[date]]
        base, alt = metrics(sample, "price_first"), metrics(sample, rule)
        for key in values:
            if base[key] is not None and alt[key] is not None:
                values[key].append(alt[key]-base[key])
    def interval(items):
        if not items:
            return None
        items.sort()
        return [items[int(.025*(len(items)-1))], items[int(.975*(len(items)-1))]]
    return dict(n=len(rows), dates=len(dates), low_information=len(dates)<10,
                hit_rate_delta95=interval(values["hit_rate"]), roi_delta95=interval(values["roi"]))


def evaluate(records):
    predictions = [r for r in records if r["record_type"] == "prediction"]
    if len({r["event_id"] for r in predictions}) != len(predictions):
        raise ValueError("Duplicate event: export must contain canonical snapshots only")
    settlements = {}
    for row in records:
        if row["record_type"] == "settlement" and row["ledger_sequence"] > settlements.get(row["snapshot_id"], {}).get("ledger_sequence", -1):
            settlements[row["snapshot_id"]] = row
    replay = []
    excluded = defaultdict(int)
    for prediction in predictions:
        saved = prediction["predictions"].get("featured_pick")
        if not saved:
            continue
        if (prediction.get("model") or {}).get("selection_policy") != "tipster-vote-v1":
            excluded["legacy_policy"] += 1
            continue
        cutoff = timestamp(prediction["kickoff"])-timedelta(minutes=30)
        if any(timestamp(prediction[k]) >= cutoff for k in ("captured_at", "as_of", "market_observed_at")):
            excluded["not_strictly_pre_t30"] += 1
            continue
        candidates = frozen_candidates(prediction)
        choices = dict(price_first=rank(candidates, True), probability_first=rank(candidates, False), primary_only=candidates[0])
        current = choices["price_first"]
        if any(current.get(k) != saved.get(k) for k in ("selection_id", "offer_id", "market", "line", "odds", "settlement_rule")):
            raise ValueError("Current selection rule failed exact stored pick replay")
        outcome = (settlements.get(prediction["snapshot_id"]) or {}).get("outcome") or {}
        frozen_grade = outcome.get("featured_pick_result")
        current_grade = grade(current, outcome)
        if frozen_grade and (current_grade is None or any(frozen_grade.get(k) != current_grade[k] for k in ("result", "net_units"))):
            raise ValueError("Stored featured grade differs from the exact offer grade")
        compact = {rule: {k: pick.get(k) for k in ("selection_id", "offer_id", "market", "line", "odds", "probability", "settlement_rule")}
                   | {"grade": grade(pick, outcome)} for rule, pick in choices.items()}
        fixture = prediction["features"]["fixture"]
        kst_date = (timestamp(prediction["kickoff"])+timedelta(hours=9)).date().isoformat()
        replay.append(dict(event_id=prediction["event_id"], snapshot_id=prediction["snapshot_id"],
                           kickoff=prediction["kickoff"], date_kst=kst_date,
                           sport=fixture["sport"], league=fixture["league"], choices=compact))
    replay.sort(key=lambda row: (row["kickoff"], row["event_id"]))
    common = [r for r in replay if all(r["choices"][rule]["grade"] is not None for rule in RULES)]
    def comparison(rows):
        return {rule: metrics(rows, rule) | (dict(delta_vs_current=paired_interval(rows, rule),
                changed=sum(r["choices"][rule]["selection_id"] != r["choices"]["price_first"]["selection_id"] for r in rows))
                if rule != "price_first" else {}) for rule in RULES}
    result = dict(opportunities=len(replay), common_settled=len(common), excluded=dict(excluded),
                  incomplete_or_pending=len(replay)-len(common), replay_matches=len(replay),
                  common=comparison(common), by_sport={s: comparison([r for r in common if r["sport"] == s]) for s in sorted({r["sport"] for r in replay})},
                  non_soccer_by_league={league: comparison([r for r in common if r["sport"] != "sc" and r["league"] == league])
                                        for league in sorted({r["league"] for r in replay if r["sport"] != "sc"})},
                  date_blocks=sorted({r["date_kst"] for r in common}),
                  limits=["Exploratory frozen-offer sensitivity, not new probability-model validation",
                          "Rules can choose different markets: same event and frozen snapshot, different products",
                          "API-selected export is incomplete and does not authenticate the whole ledger hash chain",
                          "Outcomes come from saved official grades, not newly independently fetched score originals",
                          "Small date-block count and already examined outcomes do not support deployment or future-profit claims"])
    return result, replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    blob = args.export.read_bytes()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    if hashlib.sha256(blob).hexdigest() != manifest["sha256"]:
        raise ValueError("Export receipt hash mismatch")
    records = [json.loads(l) for l in gzip.decompress(blob).decode("utf8").splitlines()]
    if len(records) != manifest["rows"]:
        raise ValueError("Export receipt count mismatch")
    result, replay = evaluate(records)
    result["source"] = {k: manifest[k] for k in ("sha256", "captured_at", "rows", "selection", "complete_ledger", "full_chain_verified")}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    serialized = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"))+"\n" for row in replay)
    (args.output/"selections.jsonl.gz").write_bytes(gzip.compress(serialized.encode("utf8"), mtime=0))
    for sport, rules in result["by_sport"].items():
        print(sport, json.dumps({name: {k: stats[k] for k in ("settled", "hits", "hit_rate", "roi", "average_odds")} for name, stats in rules.items()}))
    print("matched", result["common_settled"], "of", result["opportunities"])


if __name__ == "__main__":
    main()
