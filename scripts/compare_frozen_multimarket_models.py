"""Fixed-model descriptive replay on an independently audited exact-product cohort."""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.market_outcomes import normalize_market, probabilities

MODELS = ("m0_actual", "m0_projected", "m1_projected")


def profit(row, side):
    if side not in row["prices"]:
        raise ValueError("selection has no exact-product price")
    price = Decimal(str(row["prices"][side]))
    if not price.is_finite() or price <= 1:
        raise ValueError("invalid exact-product price")
    return float(price - 1 if side == row["outcome"] else Decimal(-1))


def choose(row, forecast):
    sides = ("under", "over") if row["market"] == "total" else ("home", "away")
    labels = {"under": "언더", "over": "오버", "home": "핸디홈", "away": "핸디원정"}
    options = [dict(market=row["market"], game_no=str(row["game_no"]), line=str(row["line"]),
                    period="full_including_extra", settlement_rule="total_half" if row["market"] == "total" else "handicap_half",
                    unit="runs", selection=side) for side in sides]
    terms = normalize_market(options, "baseball", "KBO")
    if terms is None:
        raise ValueError("unsupported exact-product terms")
    rates = [float(forecast[k]) for k in ("home_rate", "away_rate")]
    if not all(math.isfinite(x) and x > 0 for x in rates):
        raise ValueError("invalid frozen score rates")
    ps = probabilities(terms, *rates)
    values = [ps[labels[s]]["p_win"] for s in sides]
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
        raise ValueError("invalid predicted probability")
    if abs(sum(values) - 1) > 1e-8 or values[0] == values[1]:
        raise ValueError("non-exhaustive or tied prediction")
    return sides[int(values[1] > values[0])]


def metrics(rows, model):
    rows = sorted(rows, key=lambda r: (r["date"], r["fixture_id"], r["round"], r["game_no"]))
    n = len(rows)
    if not n:
        return {"n": 0, "hit_rate": None, "roi": None}
    hits = sum(r["picks"][model] == r["outcome"] for r in rows)
    cumulative = peak = drawdown = 0.
    for r in rows:
        cumulative += r["returns"][model]
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    # Wilson interval describes this cohort only; no claim of random sampling.
    z = 1.959963984540054
    p = hits / n
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    radius = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return {"n": n, "hits": hits, "hit_rate": p, "net_units": round(cumulative, 6),
            "roi": cumulative / n, "max_drawdown_units": round(drawdown, 6),
            "descriptive_wilson95": [center-radius, center+radius]}


def paired_day_interval(rows, model, iterations=5000):
    days = defaultdict(lambda: [0., 0])
    for r in rows:
        values = days[r["date"]]
        values[0] += r["returns"][model] - r["returns"]["market"]
        values[1] += 1
    if not days:
        return None
    matrix = np.asarray(list(days.values()), dtype=float)
    rng = np.random.default_rng(20261105)
    draws = rng.integers(0, len(matrix), size=(iterations, len(matrix)))
    samples = matrix[draws].sum(axis=1)
    deltas = samples[:, 0] / samples[:, 1]
    return {"day_clusters": len(days), "iterations": iterations,
            "roi_difference": float(matrix[:, 0].sum() / matrix[:, 1].sum()),
            "descriptive_day_cluster_bootstrap95": list(map(float, np.quantile(deltas, [.025, .975])))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--cohort-group", choices=("strict", "sensitivity"), default="strict")
    parser.add_argument("--forecasts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cohort = json.loads(args.cohort.read_bytes())
    if isinstance(cohort, dict):
        cohort = cohort[args.cohort_group]
    if not isinstance(cohort, list) or not all(isinstance(r, dict) for r in cohort):
        raise ValueError("cohort must contain explicit audited product rows")
    forecasts = json.loads(gzip.decompress(args.forecasts.read_bytes()))
    rows, excluded = [], []
    for row in cohort:
        try:
            picks = {m: choose(row, forecasts[row["fixture_id"]][m]) for m in MODELS}
        except (ValueError, KeyError, TypeError) as e:
            excluded.append({"fixture_id": row["fixture_id"], "market": row["market"], "reason": str(e)})
            continue
        if picks["m0_actual"] != row["model_selection"]:
            raise ValueError("independent probability replay disagrees with audited M0 selection")
        picks["market"] = row["market_selection"]
        rows.append({**row, "picks": picks, "returns": {m: profit(row, s) for m, s in picks.items()}})
    report = {"cohort": str(args.cohort), "cohort_group": args.cohort_group, "cohort_sha256": hashlib.sha256(args.cohort.read_bytes()).hexdigest(),
              "forecasts_sha256": hashlib.sha256(args.forecasts.read_bytes()).hexdigest(),
              "retrospective": True, "probability_calibrated": False, "pregame_receipt_proven": False,
              "excluded_common_cohort": excluded, "markets": {}}
    for market in ("total", "handicap"):
        chosen = [r for r in rows if r["market"] == market]
        dates = sorted({r["date"] for r in chosen})
        cutoff = dates[len(dates)//2] if dates else None
        report["markets"][market] = {"date_min": min(dates) if dates else None, "date_max": max(dates) if dates else None,
            "descriptive_split_date": cutoff,
            "models": {m: {"all": metrics(chosen, m),
                "earlier": metrics([r for r in chosen if r["date"] < cutoff], m),
                "later": metrics([r for r in chosen if r["date"] >= cutoff], m),
                "paired_with_market": paired_day_interval(chosen, m),
                "changed_picks": sum(r["picks"][m] != r["picks"]["market"] for r in chosen)}
                for m in (*MODELS, "market")}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: {m: r["all"] for m, r in v["models"].items()} for k, v in report["markets"].items()}))


if __name__ == "__main__":
    main()
