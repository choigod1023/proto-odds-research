"""Freeze team-style feature/penalty choices using pre-2025 temporal validation.

All 2025+ data is excluded from hyperparameter selection. Quarterly coefficient
refits may use earlier evaluation results, but the selected configuration cannot
change. This is retrospective development evidence, never a production artifact.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path
import platform

import numpy as np
import pandas as pd
import scipy

from team_style_archive import average, diagnostic, digest, file_hash, holm, metrics_per_row, normalize, scope
from team_style_experiment import CANDIDATES, code_hashes, fit, predict, verify_unchanged
from team_style_features import build_styles, feature_columns, score_history

FEATURE_SETS = {"compact": [prefix+name for name in ("gf10", "ga10", "attack20", "defence20", "elo")
                            for prefix in ("home_", "away_")],
                **{name: feature_columns(groups) for name, groups in CANDIDATES.items()}}
STRATEGIES = ("baseline", "fixed_full", "tuned_minimum", "tuned_simple", "ridge_only")
COMPARISONS = [(a, b) for a in ("baseline", "fixed_full")
               for b in ("tuned_minimum", "tuned_simple", "ridge_only")]
COMPARISONS += [("ridge_only", "tuned_simple"), ("tuned_minimum", "tuned_simple")]


def validate_protocol(p):
    if p.get("schema") != "nested-team-style-v1" or p.get("production_allowed") is not False:
        raise ValueError("offline protocol required")
    if not (p["training_start"] < p["inner_start"] < p["selection_cutoff_exclusive"]
            < p["evaluation_start"] <= p["evaluation_end"]):
        raise ValueError("disjoint ordered inner and outer dates required")
    if p["feature_sets"] != FEATURE_SETS or p["selection_metric"] != "log_loss":
        raise ValueError("feature sets and selection metric must be explicit")
    for k in ("embargo_days", "minimum_training_markets", "minimum_inner_markets",
              "minimum_inner_events", "minimum_inner_weeks", "minimum_inner_quarters",
              "minimum_quarter_validation_markets", "bootstrap_repeats", "block_calendar_weeks"):
        if type(p[k]) is not int or p[k] < 1:
            raise ValueError("positive integer required: "+k)
    if (not p["ridge_grid"] or len(set(p["ridge_grid"])) != len(p["ridge_grid"])
            or any(not np.isfinite(r) or r <= 0 for r in p["ridge_grid"])):
        raise ValueError("unique positive finite penalties required")
    first_cutoff = pd.Timestamp(p["evaluation_start"]).to_period("Q").start_time-pd.Timedelta(days=p["embargo_days"])
    if pd.Timestamp(p["selection_cutoff_exclusive"]) > first_cutoff:
        raise ValueError("selection must respect first outer embargo")


def configs(p):
    return [{"id": "baseline", "features": "baseline", "columns": [], "ridge": None},
            *[{"id": name+"@"+str(ridge), "features": name, "columns": columns, "ridge": ridge}
              for name, columns in FEATURE_SETS.items() for ridge in p["ridge_grid"]]]


def complexity(c):
    # Baseline has no estimated coefficient; intercept-only is not equivalent.
    return (0 if c["id"] == "baseline" else len(c["columns"])*2+1,
            -(c["ridge"] or 0), c["id"])


def block_standard_error(days, losses, repeats, width, seed):
    """SE of the validation mean, circular calendar-week blocks; no future labels."""
    weeks = defaultdict(lambda: np.zeros(2))
    for day, loss in zip(days, losses):
        stamp = pd.Timestamp(day)
        weeks[stamp-pd.Timedelta(days=stamp.weekday())] += [loss, 1]
    timeline = pd.date_range(min(weeks), max(weeks), freq="7D")
    values = np.array([weeks.get(d, np.zeros(2)) for d in timeline])
    width = min(width, len(values))
    rng, means = np.random.default_rng(seed), []
    for offset in range(0, repeats, 500):
        size = min(500, repeats-offset)
        starts = rng.integers(len(values), size=(size, math.ceil(len(values)/width)))
        indexes = ((starts[:, :, None]+np.arange(width)) % len(values)).reshape(size, -1)[:, :len(values)]
        sums = values[indexes].sum(axis=1)
        valid = sums[:, 1] > 0
        means.extend((sums[valid, 0]/sums[valid, 1]).tolist())
    return float(np.std(means, ddof=1)) if len(means) >= 2 else None


def choose(candidates, se_by_id):
    """Conventional one-SE choice; SE is not a significance test or paired CI."""
    best = min(candidates, key=lambda c: (c["log_loss"], complexity(c)))
    se = se_by_id[best["id"]]
    if se is None:
        return best, best
    eligible = [c for c in candidates if c["log_loss"] <= best["log_loss"]+se]
    return best, min(eligible, key=complexity)


def inner_selection(records, p):
    if len({scope(r) for r in records}) != 1:
        raise ValueError("inner selection must be within one league/market")
    # Defensive API boundary: selection does not even inspect feature values of later rows.
    past = [r for r in records if p["training_start"] <= r["day"] < p["selection_cutoff_exclusive"]]
    grid = configs(p)
    losses = {c["id"]: [] for c in grid}
    briers = {c["id"]: [] for c in grid}
    days, events, folds, plans = [], [], [], []
    quarter = pd.Timestamp(p["inner_start"]).to_period("Q").start_time
    last = pd.Timestamp(p["selection_cutoff_exclusive"])
    while quarter < last:
        end = min(quarter+pd.offsets.QuarterBegin(startingMonth=1), last)
        cutoff = (quarter-pd.Timedelta(days=p["embargo_days"])).date().isoformat()
        begin, finish = max(quarter, pd.Timestamp(p["inner_start"])).date().isoformat(), end.date().isoformat()
        train = [r for r in past if r["day"] < cutoff]
        valid = [r for r in past if begin <= r["day"] < finish]
        if len(train) >= p["minimum_training_markets"] and len(valid) >= p["minimum_quarter_validation_markets"]:
            folds.append({"start": begin, "end_exclusive": finish, "training_cutoff_exclusive": cutoff,
                          "training_n": len(train), "validation_n": len(valid)})
            days.extend(r["day"] for r in valid)
            events.extend(r["event"] for r in valid)
            plans.append((train, valid, cutoff))
        quarter = quarter+pd.offsets.QuarterBegin(startingMonth=1)
    active_weeks = len({str(pd.Timestamp(d).to_period("W")) for d in days})
    detail = {"scope": list(scope(records[0])), "cutoff_exclusive": p["selection_cutoff_exclusive"],
              "inner_markets": len(days), "inner_events": len(set(events)), "active_weeks": active_weeks,
              "folds": folds, "grid": [], "status": "insufficient_inner_history"}
    if not (len(days) >= p["minimum_inner_markets"] and len(set(events)) >= p["minimum_inner_events"]
            and active_weeks >= p["minimum_inner_weeks"] and len(folds) >= p["minimum_inner_quarters"]):
        detail["chosen"] = {s: grid[0] for s in ("tuned_minimum", "tuned_simple", "ridge_only")}
        return detail
    # Establish support BEFORE any fitting: sparse scopes must deterministically
    # fall back even if their otherwise-unused numerical fits would fail.
    for train, valid, cutoff in plans:
        for c in grid:
            if c["id"] == "baseline":
                ps = [r["q"] for r in valid]
            else:
                model = fit(train, cutoff, "full", c["ridge"], columns=c["columns"])
                ps = predict(valid, model)
            for r, prob in zip(valid, ps):
                m = metrics_per_row(r, prob)
                losses[c["id"]].append(m["log_loss"])
                briers[c["id"]].append(m["brier"])
    seed = int(digest([p["seed"], detail["scope"]])[:8], 16)
    entries = [{**c, "log_loss": float(np.mean(losses[c["id"]])),
                "brier": float(np.mean(briers[c["id"]]))} for c in grid]
    best_id = min(entries, key=lambda c: (c["log_loss"], complexity(c)))["id"]
    full_entries = [c for c in entries if c["features"] == "full"]
    best_full_id = min(full_entries, key=lambda c: (c["log_loss"], complexity(c)))["id"]
    ses = {key: block_standard_error(days, losses[key], p["bootstrap_repeats"],
                                     p["block_calendar_weeks"], seed) for key in {best_id, best_full_id}}
    minimum, simple = choose(entries, ses)
    _, ridge_only = choose(full_entries, ses)
    detail.update(status="selected_pre2025", grid=entries, best_standard_error=ses[best_id],
                  one_se_threshold=minimum["log_loss"]+ses[best_id] if ses[best_id] is not None else None,
                  one_se_eligible=[c["id"] for c in entries if ses[best_id] is not None
                                   and c["log_loss"] <= minimum["log_loss"]+ses[best_id]],
                  chosen={"tuned_minimum": minimum, "tuned_simple": simple, "ridge_only": ridge_only},
                  selection_sensitivity={})
    for width in (2, 8):
        se = block_standard_error(days, losses[best_id], p["bootstrap_repeats"], width, seed)
        _, alternative = choose(entries, {best_id: se})
        detail["selection_sensitivity"][str(width)] = {"best_se": se, "chosen": alternative["id"]}
    return detail


def run_league(task):
    records, p = task
    if len({(r["sport"], r["league"]) for r in records}) != 1:
        raise ValueError("league isolation required")
    groups = defaultdict(list)
    for r in sorted(records, key=lambda r: (r["kickoff"], r["offer"])):
        groups[scope(r)].append(r)
    selections = {key: inner_selection(items, p) for key, items in sorted(groups.items())}
    first, last = pd.Timestamp(p["evaluation_start"]), pd.Timestamp(p["evaluation_end"])+pd.Timedelta(days=1)
    quarter = first.to_period("Q").start_time
    evaluated, folds, skipped = [], [], []
    while quarter < last:
        end = quarter+pd.offsets.QuarterBegin(startingMonth=1)
        begin, finish = max(first, quarter).date().isoformat(), min(end, last).date().isoformat()
        cutoff = (quarter-pd.Timedelta(days=p["embargo_days"])).date().isoformat()
        for key, items in sorted(groups.items()):
            train = [r for r in items if p["training_start"] <= r["day"] < cutoff]
            test = [r for r in items if begin <= r["day"] < finish]
            if not test:
                continue
            if len(train) < p["minimum_training_markets"]:
                skipped.append({"scope": list(key), "start": begin, "train": len(train), "test": len(test)})
                continue
            choices = {"baseline": configs(p)[0],
                       "fixed_full": {"id": "full@100", "features": "full", "columns": FEATURE_SETS["full"], "ridge": 100},
                       **selections[key]["chosen"]}
            cache, model_hashes = {}, {}
            for c in choices.values():
                if c["id"] in cache:
                    continue
                if c["id"] == "baseline":
                    cache[c["id"]] = [r["q"] for r in test]
                    model_hashes[c["id"]] = "market_only"
                else:
                    model = fit(train, cutoff, "full", c["ridge"], columns=c["columns"])
                    cache[c["id"]] = predict(test, model)
                    model_hashes[c["id"]] = digest(model)
            folds.append({"scope": list(key), "start": begin, "end_exclusive": finish, "cutoff_exclusive": cutoff,
                          "training_n": len(train), "evaluation_n": len(test),
                          "selection_hash": digest(selections[key]), "models": model_hashes,
                          "choices": {name: c["id"] for name, c in choices.items()},
                          "lambda_per_training_market": {name: c["ridge"]/len(train) if c["ridge"] is not None else None
                                                         for name, c in choices.items()}})
            for i, r in enumerate(test):
                evaluated.append({**{k:v for k,v in r.items() if k != "features"}, "metrics": {
                    name: metrics_per_row(r, cache[c["id"]][i]) for name, c in choices.items()}})
        quarter = end
    name = records[0]["sport"]+"|"+records[0]["league"]
    entry = {"sport": records[0]["sport"], "league": records[0]["league"],
             "selections": list(selections.values()), "folds": folds, "skipped": skipped,
             "evaluation_markets": len(evaluated)}
    if not evaluated:
        entry["status"] = "insufficient_outer_training"
        return name, entry
    entry.update(status="retrospective_only", evaluation_events=len({r["event"] for r in evaluated}),
                 metrics={s: average(evaluated, s) for s in STRATEGIES}, comparisons={}, by_market={}, by_year={})
    for year in sorted({r["day"][:4] for r in evaluated}):
        sub = [r for r in evaluated if r["day"].startswith(year)]
        entry["by_year"][year] = {s: average(sub, s) for s in STRATEGIES}
    for key in sorted(groups):
        sub = [r for r in evaluated if scope(r) == key]
        if sub:
            entry["by_market"]["|".join(map(str, key[2:]))] = {s: average(sub, s) for s in STRATEGIES}
    for reference, candidate in COMPARISONS:
        d = diagnostic(evaluated, candidate, p, int(digest([p["seed"], name])[:8], 16), reference)
        d["positive_both_years"] = all(entry["by_year"][y][reference][loss] > entry["by_year"][y][candidate][loss]
            for y in ("2025", "2026") for loss in ("brier", "log_loss")) if {"2025", "2026"} <= entry["by_year"].keys() else False
        entry["comparisons"][reference+"_to_"+candidate] = d
    return name, entry


def experiment(raw, p, workers=1):
    validate_protocol(p)
    markets, quality = normalize(raw, p)
    matches, sq = score_history(raw)
    matches = [r for r in matches if p["training_start"] <= r["kickoff"].date().isoformat() <= p["evaluation_end"]]
    styles, _ = build_styles(matches)
    groups = defaultdict(list)
    for r in markets:
        if r["event"] in styles:
            groups[(r["sport"], r["league"])].append({**r, "features": styles[r["event"]]})
    tasks = [(rs, p) for _, rs in sorted(groups.items())]
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
    pvalues = {(name, c, i): pv for name, e in results.items()
               for c, d in e.get("comparisons", {}).items() for i, pv in enumerate(d["p_two_sided"])}
    adjusted = holm(pvalues)
    for name, e in results.items():
        for c, d in e.get("comparisons", {}).items():
            d["holm_p"] = [adjusted[(name, c, i)] for i in range(2)]
            d["historical_signal"] = bool(e["evaluation_markets"] >= 300 and e["evaluation_events"] >= 150
                and d["active_weeks"] >= 26 and d["positive_both_years"] and d["ci95"] is not None
                and all(x[0]>0 for x in d["ci95"]) and all(x is not None and x<.05 for x in d["holm_p"]))
    return {"schema": "nested-team-style-report-v1", "production_allowed": False,
            "protocol": p, "protocol_hash": digest(p), "quality": {**quality, **sq,
                "markets_without_confirmed_scores": len(markets)-sum(len(v) for v in groups.values())},
            "comparisons_count": len(pvalues), "leagues": results}


def publish_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    # A second run may have created the path since the initial CLI check.
    # Exclusive creation protects its result rather than silently overwriting it.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    a = parser.parse_args()
    if a.output.exists() or a.output.resolve() in (a.games.resolve(), a.protocol.resolve()):
        parser.error("output must be a new non-input path")
    def fingerprint():
        return {"games": file_hash(a.games), "protocol": file_hash(a.protocol),
                "code": {**code_hashes(), "nested_team_style_validation.py": file_hash(Path(__file__))}}
    before = fingerprint()
    report = experiment(pd.read_csv(a.games, low_memory=False), json.loads(a.protocol.read_text(encoding="utf-8")), a.workers)
    verify_unchanged(before, fingerprint())
    report["provenance"] = before
    report["runtime"] = {"python": platform.python_version(), "numpy": np.__version__,
                         "pandas": pd.__version__, "scipy": scipy.__version__}
    publish_report(a.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
