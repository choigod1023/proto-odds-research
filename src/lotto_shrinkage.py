"""Small, serial, retrospective shrinkage experiment; never changes live picks.

Beta pseudo-count posterior means are converted to odds, then conditioned on
exactly six inclusions. This is a Bayesian-inspired plug-in distribution, not
a full joint Bayesian posterior predictive distribution.
"""
from __future__ import annotations

import os
for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np
from lotto645 import load_draws_jsonl, indicator_matrix, data_hash

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/lotto/reports/shrinkage"
CONFIGS = [(None, None)] + [(h, s) for h in (50, 200, None) for s in (45, 450, 4500)]
SPLITS = {"validation": (740, 990), "final": (990, 1238), "previously_seen_extension": (1238, 1245)}


def durable(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def log_normalizer(weights, degree=6):
    """Vectorized exact elementary symmetric polynomial across rows."""
    dp = np.zeros((len(weights), degree + 1))
    dp[:, 0] = 1
    for i in range(weights.shape[1]):
        dp[:, 1:] += weights[:, i, None] * dp[:, :-1]
    return np.log(dp[:, degree])


def prior_weights(matrix, half_life, strength):
    counts = np.zeros(45)
    mass = 0.0
    decay = 1.0 if half_life is None else 2 ** (-1 / half_life)
    weights = np.empty_like(matrix, dtype=float)
    for t, observed in enumerate(matrix):
        p = (counts + strength * (6 / 45)) / (mass + strength)
        odds = p / (1 - p)
        weights[t] = odds / np.exp(np.log(odds).mean())
        counts = decay * counts + observed
        mass = decay * mass + 1
    return weights


def scores(matrix):
    result = np.zeros((len(CONFIGS), len(matrix)))
    for i, (half_life, strength) in enumerate(CONFIGS[1:], 1):
        w = prior_weights(matrix, half_life, strength)
        result[i] = (np.log(w) * matrix).sum(axis=1) - log_normalizer(w) + math.log(math.comb(45, 6))
    return result


def evaluate(matrix, details=False):
    gain = scores(matrix)
    train = gain[:, 200:740].mean(axis=1)
    chosen = int(np.argmax(train))  # Uniform wins an exact tie.
    # Prespecified diagnostic, never substitutes for the primary selection.
    nonuniform = int(np.argmax(train[1:])) + 1
    result = {"selected": chosen, "nonuniform_diagnostic": nonuniform,
              "training_mean_log_gain": train.tolist(), "splits": {}}
    for name, (lo, hi) in SPLITS.items():
        result["splits"][name] = {
            "n": hi - lo, "mean_log_gain": float(gain[chosen, lo:hi].mean()),
            "diagnostic_mean_log_gain": float(gain[nonuniform, lo:hi].mean())}
    if details:
        result["per_round"] = [{"draw_no": t + 1, "log_gain": float(gain[chosen, t]),
                                "diagnostic_log_gain": float(gain[nonuniform, t])}
                               for t in range(740, len(matrix))]
    return result


def main():
    draws = load_draws_jsonl(ROOT / "data/lotto/draws.jsonl")
    extra = load_draws_jsonl(ROOT / "data/lotto/reports/null-expanded/new-rounds/draws.jsonl")
    assert [d.draw_no for d in draws + extra] == list(range(1, 1246))
    protocol = {"version": 1, "configs_half_life_prior_strength": CONFIGS,
                "train_scoring_rounds": [201, 740], "splits_zero_based": SPLITS,
                "null_histories": 199, "null_seed_start": 2026101600,
                "workers": 1, "blas_threads": 1, "base_data_hash": data_hash(draws),
                "extra_data_hash": data_hash(extra),
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "selection": "max mean training log score; uniform first; hyperparameters frozen after 740",
                "primary_endpoint": "final mean log gain; upper-tail plus-one null p",
                "secondary": "validation and previously seen extension; descriptive; no fresh holdout",
                "null": "uniform six-subset histories; rerun entire training selection",
                "model": "Beta pseudo-count means -> odds -> exact size-six conditional plug-in distribution"}
    durable(OUT / "protocol.json", protocol)  # Written before scores are observed.
    started = time.perf_counter()
    actual = evaluate(indicator_matrix(draws + extra), True)
    durable(OUT / "actual.json", actual)
    nulls = []
    for index in range(199):
        rng = np.random.default_rng(2026101600 + index)
        numbers = np.argpartition(rng.random((1245, 45)), 6, axis=1)[:, :6]
        matrix = np.zeros((1245, 45))
        matrix[np.arange(1245)[:, None], numbers] = 1
        nulls.append(evaluate(matrix))
        if (index + 1) % 20 == 0:
            durable(OUT / "nulls.json", nulls)
            print(f"null histories {index + 1}/199", flush=True)
    durable(OUT / "nulls.json", nulls)
    summary = {"actual": {k: v for k, v in actual.items() if k != "per_round"},
               "null_selected_uniform": sum(x["selected"] == 0 for x in nulls), "comparisons": {}}
    for name in SPLITS:
        summary["comparisons"][name] = {}
        for metric in ("mean_log_gain", "diagnostic_mean_log_gain"):
            values = np.array([x["splits"][name][metric] for x in nulls])
            observed = actual["splits"][name][metric]
            summary["comparisons"][name][metric] = {
                "observed": observed, "geometric_probability_ratio": math.exp(observed),
                "null_interval_95": np.quantile(values, [.025, .975]).tolist(),
                "p_upper_plus_one": (1 + int(np.sum(values >= observed - 1e-12))) / 200}
    summary["elapsed_seconds"] = time.perf_counter() - started
    durable(OUT / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
