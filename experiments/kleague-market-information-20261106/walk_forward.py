"""Compare market-probability corrections on chronologically held-out K League matches."""
import argparse
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.special import log_softmax

from study import encoded, implied, paired_interval

L2_STRENGTHS = (0.02, 0.10, 0.50)
MIN_TRAIN = 150
MIN_VALIDATION = 80
BASELINE = "winner_implied"


def power_probabilities(odds):
    log_inverse = -np.log(odds)
    exponent = brentq(lambda x: np.exp(x * log_inverse).sum() - 1, 0, 256)
    return np.exp(exponent * log_inverse)


def centered(log_values):
    return log_values - log_values.mean(axis=1, keepdims=True)


def classwise(features):
    n, d = features.shape
    return (features[:, :, None, None] * np.eye(3)[None, None, :, :]).reshape(n, 3 * d, 3)


def design(rows):
    n = len(rows)
    quotes = [row["quotes"] for row in rows]
    winner = np.array([implied(quote["winner"]["odds"]) for quote in quotes])
    log_winner = np.log(winner)
    temperature = centered(log_winner)[:, None, :]
    league = np.array([1 if row["league"] == 1 else -1 for row in rows])
    league_bias = np.column_stack([np.ones(n), league])
    bias_temperature = np.concatenate([temperature, classwise(league_bias)], axis=1)
    dirichlet = np.column_stack([league_bias, centered(log_winner)[:, :2]])
    total = np.array([quote["total"]["odds"] for quote in quotes])
    handicap = np.array([quote["handicap"]["odds"] for quote in quotes])
    extra = np.column_stack([
        [quote["total"]["line"] - 2.5 for quote in quotes],
        np.log(total[:, 1] / total[:, 0]),
        [quote["handicap"]["line"] for quote in quotes],
        np.log(handicap[:, 1] / handicap[:, 0]),
        np.log(handicap[:, 1] / handicap[:, 2]),
    ])
    three = np.array([row["probabilities"]["poisson_three"] for row in rows])
    poisson_delta = 4 * centered(np.log(three) - log_winner)
    return log_winner, {
        "temperature": temperature,
        "league_bias_temperature": bias_temperature,
        "dirichlet": classwise(dirichlet),
        "poisson_delta": np.concatenate([bias_temperature, poisson_delta[:, None, :]], axis=1),
        "cross_quotes": classwise(np.column_stack([dirichlet, extra])),
    }, {
        BASELINE: winner,
        "winner_power": np.array([power_probabilities(quote["winner"]["odds"]) for quote in quotes]),
        "poisson_three": three,
    }


def fit(log_base, features, outcomes, l2, *, positive_temperature=False):
    one_hot = np.eye(3)[outcomes]

    def objective(beta):
        logp = log_softmax(log_base + np.einsum("ndk,d->nk", features, beta), axis=1)
        grad = np.einsum("ndk,nk->d", features, np.exp(logp) - one_hot) / len(outcomes)
        return -(one_hot * logp).sum() / len(outcomes) + l2 * (beta @ beta) / 2, grad + l2 * beta

    bounds = [(-0.99, None)] + [(None, None)] * (features.shape[1] - 1) if positive_temperature else None
    fitted = minimize(objective, np.zeros(features.shape[1]), method="L-BFGS-B", jac=True, bounds=bounds,
                      options={"ftol": 1e-12, "gtol": 1e-8})
    if not fitted.success:
        raise RuntimeError(f"Calibration fit failed: {fitted.message}")
    return fitted.x


def predict(rows):
    days = np.array([date.fromisoformat(row["kickoff_kst"][:10]) for row in rows])
    weeks = np.array([day - timedelta(days=day.weekday()) for day in days])
    outcomes = np.array([row["outcome"] for row in rows])
    log_base, features, fixed = design(rows)
    candidates = [(name, l2, matrix) for name, matrix in features.items() for l2 in L2_STRENGTHS]
    names = list(fixed) + [f"{name}/{l2:.2f}" for name, l2, _ in candidates]
    out_of_fold = []
    for week in sorted(set(weeks)):
        cutoff = week - timedelta(days=7)
        train = days < cutoff
        if train.sum() < MIN_TRAIN:
            continue
        test = weeks == week
        predictions = {name: p[test] for name, p in fixed.items()}
        for name, l2, matrix in candidates:
            constrained = name in ("temperature", "league_bias_temperature", "poisson_delta")
            beta = fit(log_base[train], matrix[train], outcomes[train], l2, positive_temperature=constrained)
            predictions[f"{name}/{l2:.2f}"] = np.exp(
                log_softmax(log_base[test] + np.einsum("ndk,d->nk", matrix[test], beta), axis=1))
        validation = [r for r in out_of_fold if date.fromisoformat(r["kickoff_kst"][:10]) < cutoff]
        selected = None
        if len(validation) >= MIN_VALIDATION:
            selected = min(names, key=lambda key: sum(-np.log(r["predictions"][key][r["outcome"]]) for r in validation))
        for idx, row in enumerate([rows[i] for i in np.flatnonzero(test)]):
            out_of_fold.append({key: row[key] for key in ("fixture_id", "league", "kickoff_kst", "outcome",
                                                         "previous_comparison")}
                               | {"fold": week.isoformat(), "train_before": cutoff.isoformat(),
                                  "train_n": int(train.sum()), "validation_n": len(validation),
                                  "selected": selected, "predictions": {key: p[idx].tolist() for key, p in predictions.items()}})
    return out_of_fold


def compare(rows):
    names = list(rows[0]["predictions"])
    outcomes = np.array([r["outcome"] for r in rows])
    p = {name: np.array([r["predictions"][name] for r in rows]) for name in names}
    p["past_selected"] = np.array([r["predictions"][r["selected"]] for r in rows])
    indices = np.arange(len(rows))
    truth = np.eye(3)[outcomes]
    base_loss = -np.log(p[BASELINE][indices, outcomes])
    models = {}
    for name, probs in p.items():
        loss = -np.log(probs[indices, outcomes])
        delta = loss - base_loss
        result = {"log_loss": float(loss.mean()), "delta": float(delta.mean()),
                  "brier": float(((probs - truth) ** 2).sum(axis=1).mean()),
                  "hits": int((probs.argmax(axis=1) == outcomes).sum()),
                  "interval_2_weeks": paired_interval(rows, delta, 2)}
        if name == "past_selected":
            result["sensitivity"] = {str(block): paired_interval(rows, delta, block) for block in (1, 4)}
        models[name] = result
    folds = {r["fold"]: r["selected"] for r in rows}
    return {"n": len(rows), "first": rows[0]["kickoff_kst"], "last": rows[-1]["kickoff_kst"],
            "weeks": len(folds), "selected_weeks": dict(Counter(folds.values())), "models": models}


def run(source, output):
    body = source.read_bytes()
    rows = sorted(map(json.loads, body.splitlines()), key=lambda r: (r["kickoff_kst"], r["fixture_id"]))
    if (len(rows) != 893 or len({r["fixture_id"] for r in rows}) != 893
            or sum(r["previous_comparison"] for r in rows) != 239):
        raise ValueError("Unexpected historical cohort")
    out_of_fold = predict(rows)
    ready = [r for r in out_of_fold if r["selected"] is not None]
    primary = [r for r in ready if r["previous_comparison"]]
    if len(primary) != 239:
        raise ValueError("Primary evaluation does not cover all 239 matches")
    report = {"input_sha256": hashlib.sha256(body).hexdigest(), "historical_n": len(rows),
              "candidate_oof_n": len(out_of_fold), "selection_warmup_n": len(out_of_fold) - len(ready),
              "initial_training_n": len(rows) - len(out_of_fold), "all_scored": compare(ready),
              "primary_239": compare(primary),
              "primary_by_league": {str(league): compare([r for r in primary if r["league"] == league])
                                    for league in (1, 2)}}
    output.write_bytes(encoded(report))
    ledger = output.with_suffix(".picks.jsonl")
    ledger.write_bytes("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in out_of_fold).encode("utf-8"))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    run(args.input, args.output)
