"""Audit saved score-component predictions without importing or fitting a model."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path

REQUIRED = {"market", "prior_efficiency", "prior_outcome_form", "components"}
PERIODS = {"A": "A", "B": "B", "validation": "A", "evaluation": "B"}


def metrics(rows, name):
    daily = defaultdict(Decimal)
    hits, ll, brier, odds_sum = 0, 0., 0., 0.
    for r in rows:
        p = r["probabilities"][name]
        side = int(p >= .5)
        hit = side == r["home_win"]
        price = r["odds"][0 if side else 1]
        net = Decimal(str(price)) - 1 if hit else Decimal(-1)
        daily[r["day"]] += net
        hits += hit
        odds_sum += price
        safe = min(1-1e-12, max(1e-12, p))
        ll -= math.log(safe if r["home_win"] else 1-safe)
        brier += (p-r["home_win"])**2
    equity = peak = mdd = Decimal(0)
    for day in sorted(daily):
        equity += daily[day]
        peak = max(peak, equity)
        mdd = max(mdd, peak-equity)
    return dict(n=len(rows), hits=hits, accuracy=hits/len(rows), roi=float(equity/len(rows)),
                net_units=str(equity), mean_odds=odds_sum/len(rows),
                logloss=ll/len(rows), brier=brier/len(rows), daily_mdd_units=str(mdd))


def unique_index(rows, label):
    result = {}
    for row in rows:
        if row["id"] in result:
            raise ValueError("duplicate_"+label+"_id")
        result[row["id"]] = row
    return result


def switches(rows, reference):
    changed = [r for r in rows if (r["probabilities"]["components"] >= .5) != (r["probabilities"][reference] >= .5)]
    return dict(n=len(changed), components=metrics(changed, "components") if changed else None,
                reference=metrics(changed, reference) if changed else None,
                interpretation="Post-hoc description of changed picks, not a new betting-selection policy")


def audit(rows, baseline, prices, *, allow_subset=False):
    if not rows:
        raise ValueError("no_predictions")
    old = unique_index(baseline, "baseline")
    quote = unique_index([p for p in prices if p["league"] == "NBA"], "price")
    current = unique_index(rows, "prediction")
    if not set(current) <= set(old):
        raise ValueError("not_in_frozen_comparison_cohort")
    if not allow_subset and set(current) != set(old):
        raise ValueError("incomplete_common_cohort_requires_explicit_subset")
    models = set(rows[0]["probabilities"])
    if not REQUIRED <= models:
        raise ValueError("missing_comparator")
    for r in rows:
        previous, price = old[r["id"]], quote[r["id"]]
        for key in ("day", "espn_id", "teams", "scores", "odds", "home_win", "history_event_ids"):
            if r[key] != previous[key]:
                raise ValueError("frozen_identity_price_or_history_changed:"+key)
        if price["day"] != r["day"] or price["odds"] != r["odds"]:
            raise ValueError("original_quote_changed")
        if r["home_win"] not in (0, 1) or r["home_win"] != int(price["y"] == 0):
            raise ValueError("price_outcome_label_mismatch")
        if len(r["scores"]) != 2 or r["scores"][0] == r["scores"][1] or int(r["scores"][0] > r["scores"][1]) != r["home_win"]:
            raise ValueError("score_outcome_mismatch")
        phase = PERIODS.get(r["period"])
        if phase == "A":
            valid_period = "2025-09-01" <= r["day"] <= "2025-12-31"
        elif phase == "B":
            valid_period = "2026-01-01" <= r["day"] <= "2026-10-27"
        else:
            valid_period = False
        if not valid_period:
            raise ValueError("wrong_evaluation_period")
        if len(r["odds"]) != 2 or any(type(o) not in (int, float) or not math.isfinite(o) or o <= 1 for o in r["odds"]):
            raise ValueError("invalid_price")
        probabilities = r["probabilities"]
        if set(probabilities) != models:
            raise ValueError("model_dependent_cohort")
        if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
            raise ValueError("invalid_probability")
        expected = {"market": previous["market_home"], "prior_efficiency": previous["candidate"],
                    "prior_outcome_form": previous["existing_outcome_form"]}
        market = (1/r["odds"][0])/(1/r["odds"][0]+1/r["odds"][1])
        if abs(market-probabilities["market"]) > 1e-12:
            raise ValueError("market_probability_changed")
        if any(abs(probabilities[k]-v) > 1e-12 for k, v in expected.items()):
            raise ValueError("frozen_baseline_probability_changed")
    groups = {}
    for period in sorted({PERIODS[r["period"]] for r in rows}):
        group = [r for r in rows if PERIODS[r["period"]] == period]
        groups[period] = dict(n=len(group), actual_dates=[min(r["day"] for r in group), max(r["day"] for r in group)],
                             models={name: metrics(group, name) for name in sorted(models)},
                             monthly={month: {name: metrics([r for r in group if r["day"].startswith(month)], name)
                                              for name in sorted(models)} for month in sorted({r["day"][:7] for r in group})},
                             changed_from_market={name: sum((r["probabilities"][name] >= .5) != (r["probabilities"]["market"] >= .5) for r in group)
                                                  for name in sorted(models)},
                             components_switches={name: switches(group, name) for name in sorted(REQUIRED-{"components"})})
    excluded = sorted(set(old)-set(current))
    return dict(schema="nba-components-independent-audit-v1", groups=groups, n=len(rows),
                frozen_cohort_n=len(old), coverage=len(rows)/len(old), excluded_ids=excluded,
                excluded_by_month=dict(Counter(old[i]["day"][:7] for i in excluded)),
                limits=["Independent saved-forecast arithmetic and frozen comparison audit; no fitting",
                        "Does not independently certify historical receipt times, model feature causality or settlement rules",
                        "One unit per event conditional historical returns; not a wallet or future expected profit"])


def read(path):
    raw = path.read_bytes()
    unpacked = gzip.decompress(raw) if path.suffix == ".gz" else raw
    return [json.loads(line) for line in unpacked.decode("utf8").splitlines() if line.strip()], hashlib.sha256(raw).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("predictions", "baseline", "prices", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--allow-subset", action="store_true", help="Explicitly disclose a smaller common cohort")
    args = parser.parse_args()
    predictions, ph = read(args.predictions)
    baseline, bh = read(args.baseline)
    prices, qh = read(args.prices)
    result = audit(predictions, baseline, prices, allow_subset=args.allow_subset)
    result["input_sha256"] = dict(predictions=ph, frozen_baseline=bh, prices=qh)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    print(json.dumps({"n": result["n"], "coverage": result["coverage"], "periods": list(result["groups"])}))
