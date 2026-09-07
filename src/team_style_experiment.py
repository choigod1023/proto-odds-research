"""League-independent chronological team-style ablation. Offline only.

Market offset is the common reference, not the feature under investigation.
The additional inputs are observed scores, variability and opponent residuals.
No model produced here can be promoted to the production pipeline.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd
import scipy
from scipy.optimize import minimize
from scipy.special import logsumexp

from team_style_archive import (average, diagnostic, digest, file_hash, holm,
                                metrics_per_row, normalize, scope)
from team_style_features import GROUPS, build_styles, feature_columns, score_history

CANDIDATES = {
    "control": [],
    "scoring": ["scoring"],
    "full": list(GROUPS),
    "no_variance": ["scoring", "opponent", "matchup"],
    "no_opponent": ["scoring", "variance", "matchup"],
    "no_matchup": ["scoring", "variance", "opponent"],
    "no_scoring": ["variance", "opponent", "matchup"],
}
COMPARISONS = [("baseline", "full"), ("control", "scoring"), ("control", "full"),
               ("no_variance", "full"), ("no_opponent", "full"), ("no_matchup", "full"),
               ("no_scoring", "full")]


def code_hashes():
    return {name: file_hash(Path(__file__).with_name(name)) for name in
            ("team_style_experiment.py", "team_style_features.py", "team_style_archive.py",
             "features.py", "matches.py", "bets.py", "devig.py")}


def verify_unchanged(before, after):
    if before != after:
        raise RuntimeError("input or code changed during experiment")


def validate_protocol(p):
    if p.get("schema") != "team-style-ablation-v1" or p.get("production_allowed") is not False:
        raise ValueError("offline-only protocol required")
    if not p["training_start"] < p["evaluation_start"] <= p["evaluation_end"]:
        raise ValueError("invalid date range")
    if p["candidates"] != CANDIDATES or p["refit"] != "quarterly":
        raise ValueError("fixed candidates and quarterly protocol required")
    for k in ("minimum_training_markets", "embargo_days", "bootstrap_repeats", "block_calendar_weeks"):
        if type(p[k]) is not int or p[k] < 1:
            raise ValueError("positive integer required: "+k)
    if not np.isfinite(p["ridge"]) or p["ridge"] <= 0:
        raise ValueError("positive finite ridge required")


def design(rows, columns, state=None):
    values = np.array([[r["features"].get(c) for c in columns] for r in rows], dtype=float)
    if not columns:
        return np.ones((len(rows), 1)), {"medians": [], "scales": []}
    if state is None:
        medians, scales = [], []
        for col in values.T:
            finite = col[np.isfinite(col)]
            medians.append(float(np.median(finite)) if len(finite) else 0.)
            scales.append(max(float(np.std(finite)), 1e-6) if len(finite) else 1.)
        state = {"medians": medians, "scales": scales}
    missing = ~np.isfinite(values)
    standardized = (np.where(missing, state["medians"], values)-state["medians"])/state["scales"]
    # Fixed clipping protects new-season sparse/outlier values, never eval-fitted.
    x = np.column_stack((np.ones(len(rows)), np.clip(standardized, -5, 5), missing.astype(float)))
    return x, state


def fit(rows, cutoff, name, ridge, *, columns=None):
    scopes = {scope(r) for r in rows}
    if len(scopes) != 1 or max(r["day"] for r in rows) >= cutoff:
        raise ValueError("training must be strictly past and same league/market")
    if columns is None:
        columns = feature_columns(CANDIDATES[name])
    else:
        allowed = set(feature_columns(list(GROUPS)))
        if len(set(columns)) != len(columns) or not set(columns) <= allowed:
            raise ValueError("unique known team-style columns required")
    x, state = design(rows, columns)
    q = np.asarray([r["q"] for r in rows])
    offset = np.log(np.clip(q, 1e-12, 1))
    y = np.array([r["winner"] for r in rows])
    k = q.shape[1]
    def objective(flat):
        beta = flat.reshape(x.shape[1], k-1)
        logits = offset.copy()
        logits[:, :-1] += x @ beta
        logp = logits-logsumexp(logits, axis=1)[:, None]
        error = np.exp(logp)
        error[np.arange(len(y)), y] -= 1
        return (-float(np.sum(logp[np.arange(len(y)), y]))+.5*ridge*np.sum(beta**2),
                (x.T @ error[:, :-1]+ridge*beta).ravel())
    fitted = minimize(objective, np.zeros(x.shape[1]*(k-1)), jac=True, method="L-BFGS-B",
                      options={"maxiter": 250, "ftol": 1e-10, "gtol": 1e-6})
    if not fitted.success:
        # High penalties can exhaust L-BFGS line search at a nearly stationary
        # point. Solve the SAME strictly convex objective with an exact Hessian;
        # never omit a failed candidate from model selection.
        def hessian(flat):
            beta = flat.reshape(x.shape[1], k-1)
            logits = offset.copy()
            logits[:, :-1] += x @ beta
            prob = np.exp(logits-logsumexp(logits, axis=1)[:, None])
            h = np.zeros((x.shape[1], k-1, x.shape[1], k-1))
            for a in range(k-1):
                for b in range(k-1):
                    weight = prob[:, a]*((1. if a == b else 0.)-prob[:, b])
                    h[:, a, :, b] = (x.T*weight) @ x
            return h.reshape(len(flat), len(flat))+ridge*np.eye(len(flat))
        fitted = minimize(objective, np.zeros_like(fitted.x), jac=True, hess=hessian, method="trust-exact",
                          options={"maxiter": 100, "gtol": 1e-5})
    if not fitted.success or not np.isfinite(fitted.fun):
        raise ValueError(f"fit failed for {next(iter(scopes))}, cutoff={cutoff}, ridge={ridge}: {fitted.message}")
    return {"scope": list(next(iter(scopes))), "cutoff": cutoff, "training_n": len(rows),
            "columns": columns, "state": state,
            "beta": fitted.x.reshape(x.shape[1], k-1).tolist()}


def predict(rows, model):
    if any(list(scope(r)) != model["scope"] for r in rows):
        raise ValueError("cross-league/market prediction prohibited")
    x, _ = design(rows, model["columns"], model["state"])
    logits = np.log(np.clip(np.array([r["q"] for r in rows]), 1e-12, 1))
    logits[:, :-1] += x @ np.array(model["beta"])
    return np.exp(logits-logsumexp(logits, axis=1)[:, None])


def run_league(task):
    records, protocol = task
    if len({(r["sport"], r["league"]) for r in records}) != 1:
        raise ValueError("independent league required")
    offers = defaultdict(list)
    for r in sorted(records, key=lambda r: (r["kickoff"], r["offer"])):
        offers[scope(r)].append(r)
    first, last = pd.Timestamp(protocol["evaluation_start"]), pd.Timestamp(protocol["evaluation_end"])+pd.Timedelta(days=1)
    quarter = first.to_period("Q").start_time
    rows, folds, skipped, explanations = [], [], [], []
    while quarter < last:
        end = quarter+pd.offsets.QuarterBegin(startingMonth=1)
        cutoff = (quarter-pd.Timedelta(days=protocol["embargo_days"])).date().isoformat()
        begin, finish = max(first, quarter).date().isoformat(), min(last, end).date().isoformat()
        for key, items in sorted(offers.items()):
            train = [r for r in items if protocol["training_start"] <= r["day"] < cutoff]
            test = [r for r in items if begin <= r["day"] < finish]
            if not test:
                continue
            if len(train) < protocol["minimum_training_markets"]:
                skipped.append({"scope": list(key), "quarter": begin, "train": len(train), "test": len(test)})
                continue
            models = {name: fit(train, cutoff, name, protocol["ridge"]) for name in CANDIDATES}
            predictions = {name: predict(test, model) for name, model in models.items()}
            folds.append({"scope": list(key), "start": begin, "end_exclusive": finish,
                          "cutoff_exclusive": cutoff, "train": len(train), "test": len(test),
                          "model_hashes": {name: digest(model) for name, model in models.items()}})
            for i, r in enumerate(test):
                rows.append({**{k: v for k, v in r.items() if k != "features"}, "metrics": {
                    "baseline": metrics_per_row(r, r["q"]),
                    **{name: metrics_per_row(r, p[i]) for name, p in predictions.items()}}})
            if end >= last:
                for r, p in zip(test[-2:], predictions["full"][-2:]):
                    model = models["full"]
                    x, _ = design([r], model["columns"], model["state"])
                    choice = int(np.argmax(p))
                    rival = int(np.argsort(p)[-2])
                    b = np.column_stack((np.array(model["beta"]), np.zeros(len(x[0]))))
                    terms = x[0]*(b[:, choice]-b[:, rival])
                    names = ["intercept"]+model["columns"]+[c+"_missing" for c in model["columns"]]
                    ranked = sorted(zip(names, terms), key=lambda t: abs(t[1]), reverse=True)[:8]
                    explanations.append({"day": r["day"], "home": r["home_team"], "away": r["away_team"],
                        "market": r["market"], "odds": r["odds"], "winner": r["winner"],
                        "q": r["q"], "p_full": p.tolist(), "choice": choice, "rival": rival,
                        "feature_log_odds_contributions": [[n, float(v)] for n, v in ranked],
                        "total_feature_log_odds": float(terms.sum())})
        quarter = end
    name = records[0]["sport"]+"|"+records[0]["league"]
    entry = {"sport": records[0]["sport"], "league": records[0]["league"],
             "available_markets": len(records), "evaluation_markets": len(rows),
             "folds": folds, "skipped_folds": skipped}
    if not rows:
        entry["status"] = "insufficient_training"
        return name, entry
    rows.sort(key=lambda r: (r["kickoff"], r["offer"]))
    entry.update(status="retrospective_only", evaluation_events=len({r["event"] for r in rows}),
        first_day=rows[0]["day"], last_day=rows[-1]["day"],
        metrics={c: average(rows, c) for c in ["baseline", *CANDIDATES]},
        by_year={year: {c: average([r for r in rows if r["day"].startswith(year)], c)
                       for c in ["baseline", *CANDIDATES]} for year in sorted({r["day"][:4] for r in rows})},
        by_market={}, comparisons={}, examples=explanations)
    for key in sorted(offers):
        sub = [r for r in rows if scope(r) == key]
        if sub:
            entry["by_market"]["|".join(map(str, key[2:]))] = {c: average(sub, c) for c in ["baseline", *CANDIDATES]}
    for reference, candidate in COMPARISONS:
        diag = diagnostic(rows, candidate, protocol, int(digest([protocol["seed"], name])[:8], 16), reference)
        diag["positive_both_years"] = all(
            entry["by_year"][year][reference][loss] > entry["by_year"][year][candidate][loss]
            for year in ("2025", "2026") for loss in ("brier", "log_loss")) if {"2025", "2026"} <= entry["by_year"].keys() else False
        entry["comparisons"][reference+"_to_"+candidate] = diag
    return name, entry


def experiment(raw, protocol, workers=1):
    validate_protocol(protocol)
    markets, quality = normalize(raw, protocol)
    matches, score_quality = score_history(raw)
    matches = [r for r in matches if protocol["training_start"] <= r["kickoff"].date().isoformat() <= protocol["evaluation_end"]]
    styles, profiles = build_styles(matches)
    groups = defaultdict(list)
    missing = 0
    for r in markets:
        if r["event"] not in styles:
            missing += 1
            continue
        groups[(r["sport"], r["league"])].append({**r, "features": styles[r["event"]]})
    tasks = [(records, protocol) for _, records in sorted(groups.items())]
    results = {}
    def collect(iterator):
        for name, entry in iterator:
            results[name] = entry
            print(name, entry["evaluation_markets"], flush=True)
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            collect(pool.map(run_league, tasks))
    else:
        collect(map(run_league, tasks))
    pvalues = {}
    for name, entry in results.items():
        for comparison, d in entry.get("comparisons", {}).items():
            for index, p in enumerate(d["p_two_sided"]):
                pvalues[(name, comparison, index)] = p
    adjusted = holm(pvalues)
    for name, entry in results.items():
        for comparison, d in entry.get("comparisons", {}).items():
            d["holm_p"] = [adjusted[(name, comparison, i)] for i in range(2)]
            d["historical_signal"] = bool(entry["evaluation_markets"] >= 300
                and entry["evaluation_events"] >= 150 and d["active_weeks"] >= 26
                and d["positive_both_years"] and d["ci95"] is not None
                and all(x[0] > 0 for x in d["ci95"])
                and all(p is not None and p < .05 for p in d["holm_p"]))
    return {"schema": "team-style-ablation-report-v1", "protocol": protocol,
            "protocol_hash": digest(protocol), "production_allowed": False,
            "quality": {**quality, **score_quality, "markets_without_confirmed_scores": missing,
                        "feature_events": len(styles)},
            "comparisons_count": len(pvalues), "leagues": results,
            "team_profiles": profiles}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.output.resolve() in (args.games.resolve(), args.protocol.resolve()):
        parser.error("output cannot overwrite an input")
    if args.output.exists():
        parser.error("choose a new output path; refusing overwrite")
    before = file_hash(args.games)
    initial_code = code_hashes()
    initial_protocol = file_hash(args.protocol)
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    report = experiment(pd.read_csv(args.games, low_memory=False), protocol, args.workers)
    verify_unchanged((before, initial_code, initial_protocol),
                     (file_hash(args.games), code_hashes(), file_hash(args.protocol)))
    report["source"] = {"file": args.games.name, "sha256": before, "bytes": args.games.stat().st_size}
    report["code_hashes"] = initial_code
    report["runtime"] = {"python": platform.python_version(), "numpy": np.__version__,
                         "pandas": pd.__version__, "scipy": scipy.__version__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
