"""Compare time and schedule adjustments on matched Korean KBL handicap offers."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit, ndtr

ROOT = Path(__file__).resolve().parents[2]
MODELS = ("market", "static", "calendar", "phase", "rest", "phase_rest")
PENALTIES = {"intercept": 10.0, "phase": 8.0, "rest": 8.0}
TRAIN_MIN = 30
SEASON = "S47"


def market_probability(odds):
    return odds[1] / sum(odds)


def rest_difference(row):
    home, away = row["home_rest"], row["away_rest"]
    if home is None or away is None:
        return 0.0
    return (min(home, 5) - min(away, 5)) / 5


def design(rows):
    return {
        "intercept": np.ones(len(rows)),
        "phase": np.array([r["phase"] == "playoff" for r in rows], dtype=float),
        "rest": np.array([rest_difference(r) for r in rows]),
    }


def fit(rows, names):
    basis = design(rows)
    x = np.column_stack([basis[name] for name in names])
    offset = logit([market_probability(r["odds"]) for r in rows])
    y = np.array([r["y"] for r in rows])
    penalty = np.array([PENALTIES[name] for name in names])

    def objective(b):
        z = offset + x @ b
        loss = np.sum(np.logaddexp(0, z) - y * z) + np.sum(penalty * b ** 2) / 2
        return loss, x.T @ (expit(z) - y) + penalty * b

    opt = minimize(objective, np.zeros(len(names)), jac=True, method="BFGS")
    if not opt.success:
        raise ValueError(str(opt.message))
    return dict(zip(names, opt.x.tolist(), strict=True))


def cutoff_for(day):
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday() + 7)).isoformat()


def recurring(history, target):
    month = int(target["day"][5:7])
    prior = []
    for row in history:
        distance = abs(int(row["day"][5:7]) - month)
        if row["season"] != target["season"] and min(distance, 12 - distance) <= 1:
            prior.append(row)
    return prior


def training_state(history, target):
    prior = recurring(history, target)
    counts = Counter(r["phase"] for r in history)
    rest_n = sum(r["home_rest"] is not None and r["away_rest"] is not None for r in history)
    support = {"train": len(history), "calendar": len(prior), "regular": counts["regular"],
               "playoff": counts["playoff"], "rest": rest_n}
    static = fit(history, ("intercept",)) if len(history) >= TRAIN_MIN else {"intercept": 0.0}
    phase = counts["regular"] >= TRAIN_MIN and counts["playoff"] >= 8
    rest = rest_n >= TRAIN_MIN
    calendar = dict(static)
    if len(prior) >= 20 and len(history) >= TRAIN_MIN:
        local = fit(prior, ("intercept",))["intercept"]
        weight = len(prior) / (len(prior) + 40)
        calendar["intercept"] = (1 - weight) * static["intercept"] + weight * local
    phase_coef = fit(history, ("intercept", "phase")) if phase else static
    rest_coef = fit(history, ("intercept", "rest")) if rest else static
    both = fit(history, ("intercept", "phase", "rest")) if phase and rest else phase_coef if phase else rest_coef
    return {
        "market": {}, "static": static, "calendar": calendar, "phase": phase_coef,
        "rest": rest_coef, "phase_rest": both,
    }, support


def replay(rows):
    ordered = sorted(rows, key=lambda r: (r["day"], r["kickoff"], r["offer_id"]))
    if len({r["id"] for r in ordered}) != len(ordered) or len({r["offer_id"] for r in ordered}) != len(ordered):
        raise ValueError("Expected one offer per KBL fixture")
    dates = [r["day"] for r in ordered]
    frozen = json.loads((ROOT / "data/model_candidates/handicap-v1.json").read_bytes())["KBL"]
    cache = {}
    for r in ordered:
        if r["season"] != SEASON:
            continue
        cutoff = cutoff_for(r["day"])
        key = (cutoff, r["day"][:7])
        if key not in cache:
            history = ordered[:bisect_right(dates, cutoff)]
            cache[key] = training_state(history, r)
        models, support = cache[key]
        base = market_probability(r["odds"])
        basis = design([r])
        predictions = {name: float(expit(logit(base) + sum(basis[k][0] * v for k, v in coefs.items())))
                       for name, coefs in models.items()}
        predictions["market"] = base
        if r["day"] > frozen["train_through"]:
            home, away = r["team_ids"]
            residual = frozen["team_residual"]
            margin = frozen["mean_margin"] + (residual[home] - residual[away]) / 2
            predictions["frozen"] = float(ndtr((margin + r["line"]) / frozen["sd_margin"]))
        yield dict(r, training_cutoff=cutoff, support=support, probabilities=predictions)


def observations(rows, name):
    data = []
    for r in rows:
        p = r["probabilities"][name]
        pick = int(p >= 0.5)
        hit = int(pick == r["y"])
        data.append((hit, r["odds"][1 - pick] * hit - 1, -math.log(max(p if r["y"] else 1 - p, 1e-12)), pick))
    return np.asarray(data)


def intervals(rows, change):
    by_day = defaultdict(lambda: np.zeros(4))
    for r, delta in zip(rows, change, strict=True):
        by_day[r["day"]] += np.r_[1, delta]
    a = np.asarray([by_day[d] for d in sorted(by_day)])
    n = len(a)
    width = min(n, 5)
    rng = np.random.default_rng(20261110)
    starts = rng.integers(n, size=(2000, math.ceil(n / width)))
    indexes = ((starts[:, :, None] + np.arange(width)) % n).reshape(2000, -1)[:, :n]
    sums = a[indexes].sum(axis=1)
    return dict(zip(("accuracy", "roi", "logloss"),
                    np.percentile(sums[:, 1:] / sums[:, :1], [2.5, 97.5], axis=0).T.tolist(), strict=True))


def summarize(rows):
    data = {name: observations(rows, name) for name in rows[0]["probabilities"]
            if all(name in r["probabilities"] for r in rows)}
    out = {}
    for name, value in data.items():
        out[name] = dict(zip(("accuracy", "roi", "logloss"), value[:, :3].mean(axis=0).tolist(), strict=True))
        out[name]["hits"] = int(value[:, 0].sum())
        if name not in ("static", "market"):
            out[name]["changed_pick_vs_static"] = int(np.count_nonzero(value[:, 3] != data["static"][:, 3]))
        if name == "phase_rest":
            out[name]["vs_calendar_95"] = intervals(rows, value[:, :3] - data["calendar"][:, :3])
            out[name]["vs_static_95"] = intervals(rows, value[:, :3] - data["static"][:, :3])
    return {
        "n": len(rows), "range": [rows[0]["day"], rows[-1]["day"]],
        "stage": dict(Counter(r["phase"] for r in rows)),
        "support": {
            "calendar": sum(r["support"]["calendar"] >= 20 and r["support"]["train"] >= TRAIN_MIN for r in rows),
            "phase": sum(r["support"]["playoff"] >= 8 and r["support"]["regular"] >= TRAIN_MIN for r in rows),
            "rest": sum(r["support"]["rest"] >= TRAIN_MIN and r["home_rest"] is not None and r["away_rest"] is not None for r in rows),
            "different_rest": sum(rest_difference(r) != 0 for r in rows),
        },
        "models": out,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--extra-schedule", type=Path)
    parser.add_argument("--matched", type=Path, help="Replay a bundled, validated fixture-offer ledger")
    parser.add_argument("--audit", type=Path, help="Source audit of the bundled fixture-offer ledger")
    args = parser.parse_args()
    if args.matched:
        if args.manifest or args.extra_schedule or not args.audit:
            parser.error("--matched requires --audit and cannot be combined with raw-source options")
        payload = gzip.decompress(args.matched.read_bytes())
        audit = json.loads(args.audit.read_bytes())
        if hashlib.sha256(payload).hexdigest() != audit["matched_sha256_uncompressed"]:
            raise ValueError("Matched ledger hash does not match the source audit")
        inputs = [json.loads(line) for line in payload.splitlines()]
    else:
        if args.audit:
            parser.error("--audit is only for --matched replays")
        import archive

        inputs, audit = archive.load(ROOT, args.manifest, args.extra_schedule)
    rows = list(replay(inputs))
    cohorts = {
        "season": rows,
        "regular": [r for r in rows if r["phase"] == "regular"],
        "playoff": [r for r in rows if r["phase"] == "playoff"],
        "non_tied_unrestricted": [r for r in rows if r["buy_reject"] == "0" and r["odds"][0] != r["odds"][1]],
        "same_frozen_coverage": [r for r in rows if "frozen" in r["probabilities"]],
        "both_period_inputs": [r for r in rows if r["support"]["calendar"] >= 20 and r["support"]["train"] >= TRAIN_MIN
                               and r["support"]["playoff"] >= 8 and r["support"]["regular"] >= TRAIN_MIN
                               and r["support"]["rest"] >= TRAIN_MIN and r["home_rest"] is not None and r["away_rest"] is not None],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    def encode_ledger(values):
        return b"".join((json.dumps(r, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode()
                        for r in values)

    matched = encode_ledger(inputs)
    ledger = encode_ledger(rows)
    matched_hash = hashlib.sha256(matched).hexdigest()
    audit["matched_sha256_uncompressed"] = matched_hash
    (args.out / "matched.jsonl.gz").write_bytes(gzip.compress(matched, mtime=0))
    (args.out / "predictions.jsonl.gz").write_bytes(gzip.compress(ledger, mtime=0))
    (args.out / "input-audit.json").write_bytes(
        (json.dumps(audit, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode())
    results = {"matched_sha256_uncompressed": matched_hash,
               "ledger_sha256_uncompressed": hashlib.sha256(ledger).hexdigest(),
               "cohorts": {name: summarize(r) for name, r in cohorts.items() if r}}
    (args.out / "summary.json").write_bytes(
        (json.dumps(results, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode())
    for name, group in results["cohorts"].items():
        print(name, group["n"], group["stage"], group["support"])
        for model, scores in group["models"].items():
            print(f'  {model:12} {scores["hits"]:3} {scores["accuracy"]:7.2%} {scores["roi"]:7.2%} {scores["logloss"]:.5f}')


if __name__ == "__main__":
    main()
