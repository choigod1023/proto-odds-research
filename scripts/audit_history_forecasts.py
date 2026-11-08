"""Independently reconcile saved conditional research forecasts, never fit them."""
from collections import defaultdict
from decimal import Decimal
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path


def normalize(row, kind):
    if kind == "kbo":
        if row["year"] != "2026":
            return None
        winner = row["winner"]
        if winner not in {"home", "away", "draw"}:
            raise ValueError("unknown_winner")
        odds = [row["prices"]["home"], row["prices"]["away"]]
        probs = {name: item["p_home"] for name, item in row["arms"].items()}
        for name, item in row["arms"].items():
            side = "home" if probs[name] >= .5 else "away"
            if item["side"] != side or item["odds"] != row["prices"][side]:
                raise ValueError("saved_choice_or_price_differs")
        return dict(id=row["fixture_id"], day=row["date"], y=None if winner == "draw" else int(winner == "home"), odds=odds, probs=probs)
    if kind == "kbl":
        if row["period"] != "evaluation":
            return None
        return dict(id=row["id"], day=row["day"], y=row["y"], odds=row["odds"], probs=row["prob"])
    if kind == "nba":
        if not "2026-01-01" <= row["day"] <= "2026-10-27":
            return None
        return dict(id=row["id"], day=row["day"], y=row["home_win"], odds=row["odds"],
                    probs={name: row[name] for name in ("market_home", "candidate", "existing_outcome_form")})
    if kind in {"vnl", "kovo"}:
        day = row["date"] if kind == "kovo" else row["day"]
        if not day.startswith("2026-"):
            return None
        # Both saved research schemas explicitly encode 1=home. The original
        # price archive uses the opposite convention and is never read here.
        return dict(id=row["id"], day=day, y=row["y"], odds=row["odds"],
                    probs={name: row[name] for name in ("market", "candidate")})
    raise ValueError("unsupported_schema")


def audit(rows):
    if not rows:
        raise ValueError("no_evaluation_rows")
    ids = set()
    models = set(rows[0]["probs"])
    by_model = {name: dict(hits=0, decided=0, refunds=0, net=Decimal(0), odds=0., ll=0., brier=0., daily=defaultdict(Decimal)) for name in models}
    for row in rows:
        if row["id"] in ids:
            raise ValueError("duplicate_evaluation_event")
        ids.add(row["id"])
        if set(row["probs"]) != models or row["y"] not in {0, 1, None}:
            raise ValueError("inconsistent_models_or_outcome")
        if len(row["odds"]) != 2 or any(type(p) not in (int, float) or not math.isfinite(p) or p <= 1 for p in row["odds"]):
            raise ValueError("invalid_two_way_price")
        for name, probability in row["probs"].items():
            if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("invalid_probability")
            value = by_model[name]
            side = int(probability >= .5)
            price = row["odds"][0 if side else 1]
            value["odds"] += price
            if row["y"] is None:
                net = Decimal(0)
                value["refunds"] += 1
            else:
                hit = side == row["y"]
                net = Decimal(str(price))-1 if hit else Decimal(-1)
                value["decided"] += 1
                value["hits"] += hit
                safe = min(1-1e-12, max(1e-12, probability))
                value["ll"] -= math.log(safe if row["y"] else 1-safe)
                value["brier"] += (probability-row["y"])**2
            value["net"] += net
            value["daily"][row["day"]] += net
    result = {}
    for name, value in sorted(by_model.items()):
        equity = peak = drawdown = Decimal(0)
        for day in sorted(value["daily"]):
            equity += value["daily"][day]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
        decided = value["decided"]
        result[name] = dict(n=len(rows), decided=decided, refunds=value["refunds"], hits=value["hits"],
                            accuracy=value["hits"]/decided if decided else None,
                            net_units=str(value["net"]), conditional_roi=float(value["net"]/len(rows)),
                            mean_odds=value["odds"]/len(rows), logloss=value["ll"]/decided if decided else None,
                            brier=value["brier"]/decided if decided else None, daily_mdd_units=str(drawdown))
    return dict(models=result, period=[min(r["day"] for r in rows), max(r["day"] for r in rows)],
                limits=["Independent arithmetic and cohort audit only; no source identity or pregame availability certification",
                        "Conditional one-unit historical returns, not wallet returns or future profit"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--kind", required=True, choices=("kbo", "kbl", "nba", "vnl", "kovo"))
    parser.add_argument("--league", help="Exact saved league label; never inferred from outcomes")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    blob = args.predictions.read_bytes()
    raw = gzip.decompress(blob) if args.predictions.suffix == ".gz" else blob
    source_rows = [json.loads(line) for line in raw.decode("utf8").splitlines()]
    rows = [normalize(row, args.kind) for row in source_rows if args.league is None or row.get("league") == args.league]
    result = audit([r for r in rows if r is not None])
    result["predictions_sha256"] = hashlib.sha256(blob).hexdigest()
    result["schema"] = args.kind
    result["league_filter"] = args.league
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    print(json.dumps(result["models"]))
