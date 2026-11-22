"""Compare frozen KBO M0 with a ridge correction from pregame reliever workload."""
import argparse
from collections import Counter
from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from workload import TEAM

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "kbo-m0-followup-20261103"))
import study
from workload import workload

EVOLUTION = "experiments/kbo-m0-evolution-20261103/"
EFFECTS = "experiments/kbo-player-effects-20261103/inputs/"
METRICS = study.metrics


def archived():
    before = "ed11ca0561e485319f2b951409287f42167f42b5"
    p25 = study.fixed(before, EVOLUTION + "artifacts/selection.json")
    p26 = study.fixed("627cadca9096acae18a30f72484c342edab7acd3", EVOLUTION + "artifacts/predictions.json")
    old = "d08ccb708033fb6b067e84e80eae81ea3c9b4cfa"
    labels26 = study.fixed(old, EFFECTS + "test-labels.jsonl.gz")
    prices26 = study.fixed(old, EFFECTS + "price-ledgers.json")["ledgers"]["extended_day_team_source_confirmed"]
    return {
        "2025": (p25["baseline_and_nominee_validation_predictions"]["baseline"],
                 study.core.rows("train-actual.jsonl.gz"), None, None),
        "2026": (p26["predictions"]["actual"]["baseline"],
                 study.core.rows("test-actual.jsonl.gz"), METRICS.unique(labels26), METRICS.unique(prices26)),
    }


def make_rows(days, forecasts, fixtures):
    preds, teams = METRICS.unique(forecasts), METRICS.unique(fixtures)
    rows, excluded = [], Counter()
    for day, batch in sorted(days.items()):
        target = date.fromisoformat(day)
        for game in batch["games"]:
            fid = game["id"]
            pred, team_row = preds.get(fid), teams.get(fid)
            if "home_score" not in game:
                excluded["no_official_final"] += 1
                continue
            if not pred or not team_row:
                excluded["missing_m0_or_fixture"] += 1
                continue
            if (day != pred["event_date"] or day != team_row["event_date"]
                    or any(TEAM[team_row[side + "_id"]] != game[side] for side in ("home", "away"))):
                raise ValueError(f"M0 fixture mismatch {fid}")
            sides = [workload(days, target, game[s]) for s in ("home", "away")]
            if any(s is None for s in sides):
                excluded["incomplete_prior_history_or_core"] += 1
                continue
            keys, scales = ("pitches_3d", "back_to_back", "days_since"), (30., 1., 7.)
            features = [(sum(p[k] for p in sides[0]) - sum(p[k] for p in sides[1])) / (3 * scale)
                        for k, scale in zip(keys, scales)]
            label = {"status": "final", "home_score": game["home_score"], "away_score": game["away_score"]}
            p = pred["probabilities_2way"]["home"]
            if not np.isfinite(p) or not 0 < p < 1:
                raise ValueError("invalid archived M0 probability")
            rows.append(dict(fixture_id=fid, date=day, winner=METRICS.winner(label), label=label,
                             m0=p, features=features, core=sides,
                             back_to_back=any(p["back_to_back"] for side in sides for p in side)))
    return rows, dict(excluded)


def fit(rows, test):
    decisive = [r for r in rows if r["winner"] != "draw"]
    if not decisive or not test or max(r["date"] for r in rows) >= min(r["date"] for r in test):
        raise ValueError("training must be nonempty and precede testing")
    x = np.array([r["features"] for r in decisive])
    y = np.array([r["winner"] == "home" for r in decisive])
    base = logit([r["m0"] for r in decisive])

    def objective(w):
        logits = base + x @ w
        loss = np.mean(np.logaddexp(0, logits) - y * logits) + 0.05 * (w @ w)
        gradient = x.T @ (expit(logits) - y) / len(y) + 0.10 * w
        return loss, gradient

    fit = minimize(objective, np.zeros(3), jac=True, method="L-BFGS-B")
    if not fit.success:
        raise ValueError(f"optimization failed: {fit.message}")
    return fit.x


def forecasts(rows, weights):
    m0 = np.array([r["m0"] for r in rows])
    challenger = expit(logit(m0) + np.array([r["features"] for r in rows]).reshape(-1, 3) @ weights)
    return {"m0": m0, "m0_relief": challenger}


def evaluate(rows, weights, prices=None):
    if not rows:
        return {"n": 0, "arms": {}, "paired": None}
    arms, decisions = {}, {}
    actual = np.array([r["winner"] for r in rows])
    decisive = actual != "draw"
    for name, ps in forecasts(rows, weights).items():
        choice = np.where(ps >= 0.5, "home", "away")
        if prices is not None:
            probs = {r["fixture_id"]: {"q2": {"home": p, "away": 1-p}} for r, p in zip(rows, ps)}
            labels = {r["fixture_id"]: r["label"] for r in rows}
            arms[name], decisions[name] = METRICS.settle(prices, probs, labels)
        else:
            hits = int((choice == actual).sum())
            arms[name] = {"n": len(rows), "hits": hits, "refunds": int((~decisive).sum()),
                          "decisive_accuracy": hits / decisive.sum() if decisive.any() else None}
        p = ps[decisive]
        arms[name]["log_loss"] = float(-np.log(np.where(actual[decisive] == "home", p, 1-p)).mean()) if len(p) else None
    paired = METRICS.paired(decisions["m0_relief"], decisions["m0"]) if prices is not None else None
    return {"n": len(rows), "arms": arms, "paired": paired}


def exact_quotes(rows, labels, quotes):
    matched, excluded = [], Counter()
    for row in rows:
        fid = row["fixture_id"]
        label, quote = labels.get(fid), quotes.get(fid)
        if not label or not quote:
            excluded["missing_archived_price_or_label"] += 1
            continue
        source = quote["price_source"]
        if not label.get("market_id") or not label.get("settlement_rule"):
            excluded["missing_archived_product_or_rule"] += 1
            continue
        if (source["market_id"] != label["market_id"] or not source["market_id"].endswith(":full_game_2way")
                or label["settlement_rule"] != "draw_void_refund"
                or row["date"] != quote["date"] or row["winner"] != quote["winner"]
                or any(row["label"][k] != label[k] for k in ("home_score", "away_score"))):
            raise ValueError(f"archived price terms or official result conflict {fid}")
        if any(not Decimal(str(v)).is_finite() for v in source["prices"].values()):
            raise ValueError("nonfinite price")
        matched.append((row, quote))
    return matched, dict(excluded)


def run(source):
    raw = source.read_bytes()
    data = json.loads(gzip.decompress(raw))
    archive = archived()
    cohorts, excluded, coverage = {}, {}, {}
    for year, obj in data["years"].items():
        days = obj["days"]
        cohorts[year], excluded[year] = make_rows(days, *archive[year][:2])
        coverage[year] = {"official_boxes": sum("appearances" in g for b in days.values() for g in b["games"]),
                          "unusable_boxes": obj["unusable_boxes"], "days": len(days)}
    training, test = cohorts["2025"], cohorts["2026"]
    weights = fit(training, test)
    matched, price_exclusions = exact_quotes(test, *archive["2026"][2:])
    report = {}
    for group in ("all", "core_back_to_back"):
        keep = lambda r: group == "all" or r["back_to_back"]
        rows = [r for r in test if keep(r)]
        priced = [(r, q) for r, q in matched if keep(r)]
        report[group] = {"all_predictions": evaluate(rows, weights),
                         "same_product_price": evaluate([r for r, _ in priced], weights, [q for _, q in priced])}
    return {"input_sha256": hashlib.sha256(raw).hexdigest(), "archive_hashes": study.SOURCES,
            "coverage": coverage, "excluded": excluded, "price_exclusions": price_exclusions,
            "coefficients": weights.tolist(), "training_n": len(training), "evaluation": report,
            "matched_ids": [r["fixture_id"] for r, _ in matched]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(args.source), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")
