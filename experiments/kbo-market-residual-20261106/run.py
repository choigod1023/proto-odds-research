"""Fit a constrained market-offset model and replay the locked KBO price cohorts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "kbo-m0-followup-20261103"))
import study

metrics = study.metrics
EVOLUTION = "experiments/kbo-m0-evolution-20261103/"
EFFECTS = "experiments/kbo-player-effects-20261103/inputs/"


def cohort(predictions, labels, quotes):
    predictions, labels, quotes = map(metrics.unique, (predictions, labels, quotes))
    rows, missing_terms = [], []
    for fixture in sorted(predictions.keys() & labels.keys() & quotes.keys()):
        pred, label, quote = predictions[fixture], labels[fixture], quotes[fixture]
        source = quote["price_source"]
        prices = source["prices"]
        if quote["date"] != pred["event_date"] or quote["winner"] != metrics.winner(label):
            raise ValueError(f"inconsistent fixture, price or result: {fixture}")
        if not label.get("market_id") or not label.get("settlement_rule"):
            missing_terms.append(fixture)
            continue
        if (source["market_id"] != label["market_id"]
                or not source["market_id"].endswith(":full_game_2way")
                or label["settlement_rule"] != "draw_void_refund"):
            raise ValueError(f"inconsistent product or settlement terms: {fixture}")
        probs = pred["probabilities_2way"]
        if (set(prices) != {"home", "away"} or set(probs) != {"home", "away"}
                or any(not np.isfinite(p) or p <= 1 for p in prices.values())
                or any(not np.isfinite(p) or not 0 < p < 1 for p in probs.values())
                or abs(sum(probs.values()) - 1) > 1e-9):
            raise ValueError(f"invalid price or probability: {fixture}")
        market = prices["away"] / (prices["home"] + prices["away"])
        rows.append((quote, label, market, probs["home"]))
    return sorted(rows, key=lambda r: (r[0]["date"], r[0]["fixture_id"])), missing_terms


def inputs():
    data = "f6681d7525e992ef52e770e50bf6ef2401c6ef42"
    fit = study.fixed("7f06e76dfe77cbb3dd7d9395995133effdb1b66a",
                      study.BASE + "historical/predictions2024.json")["baseline"]
    p25 = study.fixed("ed11ca0561e485319f2b951409287f42167f42b5", EVOLUTION + "artifacts/selection.json")
    p26 = study.fixed("627cadca9096acae18a30f72484c342edab7acd3", EVOLUTION + "artifacts/predictions.json")
    old = "d08ccb708033fb6b067e84e80eae81ea3c9b4cfa"
    labels26 = study.fixed(old, EFFECTS + "test-labels.jsonl.gz")
    prices26 = study.fixed(old, EFFECTS + "price-ledgers.json")["ledgers"]
    annual, excluded = {}, {}
    for year, predictions in ((2024, fit), (2025, p25["baseline_and_nominee_validation_predictions"]["baseline"])):
        labels = study.fixed(data, study.BASE + f"data/labels-{year}.jsonl.gz")
        prices = study.fixed(data, study.BASE + f"data/prices-{year}.jsonl.gz")
        annual[year], excluded[str(year)] = cohort(predictions, labels, prices)
    baseline26 = p26["predictions"]["actual"]["baseline"]
    annual[2026], excluded["2026"] = cohort(baseline26, labels26, prices26["extended_day_team_source_confirmed"])
    strict26, excluded["2026_strict"] = cohort(baseline26, labels26, prices26["strict_original_time_match"])
    complete_prices = {r[0]["fixture_id"]: r[0]["price_source"]["prices"] for r in annual[2026]}
    if any(complete_prices.get(r[0]["fixture_id"]) != r[0]["price_source"]["prices"] for r in strict26):
        raise ValueError("strict price rows must be a subset of 2026")
    return annual, strict26, excluded


def design(rows, gated):
    market = np.array([r[2] for r in rows])
    m0 = np.array([r[3] for r in rows])
    base = logit(market)
    residual = logit(m0) - base
    if not gated:
        return base, residual[:, None]
    gap = np.minimum(np.abs(market - 0.5) / 0.20, 1.0)
    return base, np.column_stack((residual * (1 - gap), residual * gap))


def train(rows, evaluation, gated):
    if not rows or not evaluation or rows[-1][0]["date"] >= evaluation[0][0]["date"]:
        raise ValueError("training dates must precede evaluation dates")
    decisive = [r for r in rows if r[0]["winner"] != "draw"]
    if not decisive:
        raise ValueError("at least one decisive training game is required")
    y = np.array([r[0]["winner"] == "home" for r in decisive], dtype=float)
    base, matrix = design(decisive, gated)

    def objective(w):
        logits = base + matrix @ w
        loss = np.mean(np.logaddexp(0, logits) - y * logits) + 0.001 * np.mean(w ** 2)
        grad = matrix.T @ (expit(logits) - y) / len(y) + 0.002 * w / len(w)
        if gated:
            gap = w[0] - w[1]
            loss += 0.005 * gap ** 2
            grad += 0.010 * gap * np.array([1., -1.])
        return loss, grad

    n = matrix.shape[1]
    fit = minimize(objective, np.full(n, 0.5), method="L-BFGS-B", jac=True, bounds=[(0, 1)] * n)
    if not fit.success:
        raise ValueError(f"optimization failed: {fit.message}")
    return fit.x.tolist()


def evaluate(rows, weights):
    labels = {r[0]["fixture_id"]: r[1] for r in rows}
    quotes = [r[0] for r in rows]
    base, matrix = design(rows, False)
    gate_base, gate_matrix = design(rows, True)
    probabilities = {
        "market": [r[2] for r in rows],
        "m0": [r[3] for r in rows],
        "constant": expit(base + matrix @ weights["constant"]),
        "gated": expit(gate_base + gate_matrix @ weights["gated"]),
    }
    result, decisions = {}, {}
    decisive = np.array([r[0]["winner"] != "draw" for r in rows])
    home_win = np.array([r[0]["winner"] == "home" for r in rows])[decisive]
    for arm, probabilities_home in probabilities.items():
        forecasts = {q["fixture_id"]: {"q2": {"home": p, "away": 1 - p}}
                     for q, p in zip(quotes, probabilities_home)}
        result[arm], decisions[arm] = metrics.settle(quotes, forecasts, labels)
        p = np.asarray(probabilities_home)[decisive]
        result[arm]["log_loss"] = float(np.mean(-np.log(np.where(home_win, p, 1 - p))))
        result[arm]["brier"] = float(np.mean((p - home_win) ** 2))
    paired = {arm: metrics.paired(decisions["gated"], decisions[arm])
              for arm in ("market", "m0", "constant")}
    return {"arms": result, "gated_minus": paired}


def run():
    annual, strict, excluded = inputs()
    result = {"status": "retrospective_diagnostic", "source_hashes": study.SOURCES,
              "excluded_missing_product_or_rule": excluded, "evaluation": {}}
    for year in (2025, 2026):
        training = [r for earlier in range(2024, year) for r in annual[earlier]]
        evaluation = annual[year]
        weights = {"constant": train(training, evaluation, False), "gated": train(training, evaluation, True)}
        result["evaluation"][str(year)] = {
            "training_years": list(range(2024, year)),
            "training_n": len(training),
            "training_decisive": sum(r[0]["winner"] != "draw" for r in training),
            "weights": weights,
            **evaluate(evaluation, weights),
        }
    result["evaluation"]["2026_strict"] = evaluate(strict, result["evaluation"]["2026"]["weights"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
