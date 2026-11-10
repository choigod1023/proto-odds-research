"""Replay time-based probability corrections using matched settled archives."""

import argparse
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SPORTS = ("sc", "bs", "bk", "vl")
MODELS = ("reference", "static", "calendar", "recent", "combined")
FOCUS_LEAGUES = {"K리그1", "K리그2", "KBO", "MLB", "NPB", "KBL", "NBA", "KOVO남", "KOVO여"}
PRIOR = 80.0
MIN_HISTORY = 30.0
EVAL_START = "2025-01-01"
EVAL_END = "2026-11-02"


def monday(day):
    d = date.fromisoformat(day)
    return d - timedelta(days=d.weekday())


def correction(prob, outcomes, weights):
    n = float(weights.sum())
    if n < MIN_HISTORY:
        return np.zeros(prob.shape[1]), n
    expected = weights @ prob
    observed = np.bincount(outcomes, weights=weights, minlength=prob.shape[1])
    pseudo = np.maximum(expected * (PRIOR / n), 1e-9)
    return np.log((observed + pseudo) / (expected + pseudo)), n


def estimates(prob, outcomes, ordinals, years, months, week, day):
    static, static_n = correction(prob, outcomes, np.ones(len(prob)))
    distance = np.abs(months - day.month)
    prior_cycle = (years < day.year) & (np.minimum(distance, 12 - distance) <= 1)
    seasonal, calendar_n = correction(prob, outcomes, prior_cycle.astype(float))
    age = week.toordinal() - ordinals
    weights = np.where(age <= 180, np.exp2(-age / 45), 0.0)
    recent, recent_n = correction(prob, outcomes, weights)

    def blend(shift, n):
        return static + (shift - static) * (n / (n + PRIOR)) if n >= MIN_HISTORY else static

    calendar_shift = blend(seasonal, calendar_n)
    recent_shift = blend(recent, recent_n)
    combined = ((calendar_shift + recent_shift) / 2 if min(calendar_n, recent_n) >= MIN_HISTORY
                else calendar_shift if calendar_n >= MIN_HISTORY else recent_shift)
    return {"reference": np.zeros_like(static), "static": static, "calendar": calendar_shift,
            "recent": recent_shift, "combined": combined}, {
                "static": static_n, "calendar": calendar_n, "recent": recent_n,
            }


def adjusted(prob, shift):
    if not np.any(shift):
        return prob
    out = prob * np.exp(shift - max(shift))
    return out / out.sum()


def replay(rows):
    groups = defaultdict(list)
    seen = set()
    for r in rows:
        if r["id"] in seen:
            raise ValueError(f"Duplicate identity {r['id']}")
        seen.add(r["id"])
        key = (r["sport"], r["league"], r["market"], r["period"], len(r["odds"]))
        groups[key].append(r)
    for group in groups.values():
        group.sort(key=lambda r: (r["day"], r["id"]))
        days = [date.fromisoformat(r["day"]) for r in group]
        ordinals = [d.toordinal() for d in days]
        all_prob = np.asarray([r["base"] for r in group])
        outcomes = np.asarray([r["y"] for r in group], dtype=int)
        if not np.all(np.isfinite(all_prob) & (all_prob >= 0)) or not np.allclose(all_prob.sum(axis=1), 1):
            raise ValueError("Invalid reference probabilities")
        if not np.all((0 <= outcomes) & (outcomes < all_prob.shape[1])):
            raise ValueError("Invalid settlement code")
        years, months = np.asarray([d.year for d in days]), np.asarray([d.month for d in days])
        as_array = np.asarray(ordinals)
        cache = {}
        for i, row in enumerate(group):
            if not EVAL_START <= row["day"] <= EVAL_END:
                continue
            d = days[i]
            week = monday(row["day"])
            cutoff = week - timedelta(days=7)
            key = (week, d.year, d.month)
            if key not in cache:
                n = bisect_right(ordinals, cutoff.toordinal())
                cache[key] = estimates(all_prob[:n], outcomes[:n], as_array[:n], years[:n], months[:n], week, d)
            shifts, support = cache[key]
            predictions = {}
            for name, shift in shifts.items():
                p = all_prob[i] if name == "reference" else adjusted(all_prob[i], shift)
                pick = int(np.argmax(p))
                hit = int(pick == row["y"])
                predictions[name] = {"p": p.tolist(), "pick": pick, "hit": hit, "net": row["odds"][pick] * hit - 1,
                                     "loss": -math.log(max(float(p[row["y"]]), 1e-12))}
            yield dict(row, cutoff=cutoff.isoformat(), support=support, predictions=predictions)


def blocks(rows, differences):
    daily = defaultdict(lambda: np.zeros(4))
    for r, change in zip(rows, differences, strict=True):
        daily[r["day"]] += np.r_[1, change]
    values = np.array([daily[d] for d in sorted(daily)])
    n = len(values)
    if n < 2:
        return None
    width = min(n, 7)
    rng = np.random.default_rng(20261110)
    starts = rng.integers(n, size=(1000, math.ceil(n / width)))
    take = ((starts[:, :, None] + np.arange(width)) % n).reshape(1000, -1)[:, :n]
    sums = values[take].sum(axis=1)
    ranges = np.percentile(sums[:, 1:] / sums[:, :1], [2.5, 97.5], axis=0)
    return dict(zip(("accuracy", "roi", "logloss"), ranges.T.tolist(), strict=True))


def stats(rows, intervals=False):
    n = len(rows)
    result = {}
    arrays = {}
    for model in MODELS:
        ps = [r["predictions"][model] for r in rows]
        values = np.array([[p["hit"], p["net"], p["loss"]] for p in ps])
        arrays[model] = values
        hits = int(values[:, 0].sum())
        result[model] = {"hits": hits, "accuracy": hits / n, "roi": float(values[:, 1].mean()),
                         "logloss": float(values[:, 2].mean())}
        if model != "reference":
            ref = "reference" if model == "static" else "static"
            delta = values - arrays[ref]
            result[model]["against"] = ref
            result[model]["changed_pick"] = sum(p["pick"] != r["predictions"][ref]["pick"]
                                                 for p, r in zip(ps, rows, strict=True))
            if intervals:
                result[model]["paired_95"] = blocks(rows, delta)
    return {"n": n, "range": [min(r["day"] for r in rows), max(r["day"] for r in rows)],
            "support": {name: sum(r["support"][name] >= MIN_HISTORY for r in rows)
                        for name in ("static", "calendar", "recent")}, "models": result}


def summarize(rows):
    by = defaultdict(list)
    for r in rows:
        league, market, year = r["league"], r["market"], r["day"][:4]
        for key in (("all",), ("year", year), ("market", market), ("league", league, market)):
            by[key].append(r)
        if league in FOCUS_LEAGUES:
            by[("league_year", league, market, year)].append(r)
    return {
        "/".join(key): stats(subset, intervals=key[0] in ("all", "year", "league_year")
                             or (key[0] == "league" and key[1] in FOCUS_LEAGUES))
        for key, subset in sorted(by.items())
    }


def run_sport(task):
    sport, source, directory = task
    rows = sorted(replay(source), key=lambda r: (r["day"], r["id"]))
    body = b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode() for r in rows)
    (directory / f"{sport}-predictions.jsonl.gz").write_bytes(gzip.compress(body, mtime=0))
    return sport, {"source_count": len(source), "prediction_sha256": hashlib.sha256(body).hexdigest(),
                   "groups": summarize(rows)}


def main():
    import period_inputs

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sport", choices=SPORTS, action="append")
    args = parser.parse_args()
    selected = tuple(dict.fromkeys(args.sport or SPORTS))
    args.out.mkdir(parents=True, exist_ok=True)
    by = {sport: [] for sport in selected}
    for r in period_inputs.rows(ROOT):
        if r["sport"] in by:
            by[r["sport"]].append(r)
    tasks = [(sport, by[sport], args.out) for sport in selected]
    with ProcessPoolExecutor(max_workers=len(tasks)) as workers:
        result = dict(workers.map(run_sport, tasks))
    report = {"sources": period_inputs.manifest(ROOT), "sports": result}
    (args.out / "summary.json").write_bytes(
        (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    )
    for sport, obj in result.items():
        all_rows = obj["groups"]["all"]
        print(sport, all_rows["n"], all_rows["support"])
        for model in MODELS:
            m = all_rows["models"][model]
            print(f'  {model:10} {m["hits"]:5} {m["accuracy"]:7.2%} {m["roi"]:7.2%} {m["logloss"]:.5f}')


if __name__ == "__main__":
    main()
