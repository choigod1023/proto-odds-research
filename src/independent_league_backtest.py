"""Independent league/market retrospective archive experiment, never deployment.

No shared learned coefficients, league pooling, production imports or database
access. Archived odds do not establish prices/labels available at historical
T-30. All resulting significance statements concern this retrospective sample.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import logsumexp

from bets import winner_index
from devig import market_probabilities
from matches import actual_game_year, clean_team

SUPPORTED = {("승패", 2): {"홈승", "홈패"},
             ("승무패", 3): {"홈승", "무승부", "홈패"},
             ("언더오버", 2): {"언더", "오버"},
             ("핸디캡", 2): {"핸디승", "핸디패"},
             ("핸디캡", 3): {"핸디승", "핸디무", "핸디패"}}
DATE = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_protocol(p):
    if p.get("schema") != "independent-league-archive-protocol-v1" or p.get("production_allowed") is not False:
        raise ValueError("offline protocol required")
    if not p["training_start"] < p["evaluation_start"] <= p["evaluation_end"]:
        raise ValueError("invalid dates")
    for field in ("embargo_days", "minimum_training_markets", "minimum_evaluation_markets",
                  "minimum_evaluation_events", "minimum_evaluation_weeks", "bootstrap_repeats",
                  "block_calendar_weeks"):
        if type(p[field]) is not int or p[field] < 1:
            raise ValueError("positive integer required: " + field)
    if p["refit"] != "quarterly" or not 0 < p["alpha"] < 1 or not 0 < p["ridge"] < float("inf"):
        raise ValueError("invalid model/inference settings")
    seen = set()
    for c in p["candidates"]:
        if not c["name"] or c["name"] in seen:
            raise ValueError("unique candidates required")
        seen.add(c["name"])
        h = c["half_life_days"]
        if h is not None and (isinstance(h, bool) or not math.isfinite(h) or h <= 0):
            raise ValueError("invalid recency half life")


def normalize(raw, protocol):
    """Reject ambiguous reissued prices/results; retain exact match times."""
    validate_protocol(protocol)
    counts, scopes = Counter(), Counter()
    frame = raw.fillna("").copy()
    parsed = frame["date_text"].astype(str).str.extract(DATE).apply(pd.to_numeric, errors="coerce")
    years = actual_game_year(frame["year"], frame["round"], parsed[0])
    times = pd.to_datetime(dict(year=years, month=parsed[0], day=parsed[1],
                               hour=parsed[2], minute=parsed[3]), errors="coerce")
    records, conflicts = {}, set()
    for values, kickoff in zip(frame.to_dict("records"), times):
        try:
            n = int(values["n_way"])
        except (ValueError, TypeError):
            counts["invalid_n_way"] += 1
            continue
        market = str(values["market_family"])
        if (market, n) not in SUPPORTED:
            counts["unsupported_market"] += 1
            continue
        if pd.isna(kickoff):
            counts["invalid_date"] += 1
            continue
        day = kickoff.date().isoformat()
        if not protocol["training_start"] <= day <= protocol["evaluation_end"]:
            counts["outside_dates"] += 1
            continue
        sport, league = str(values["sport"]).strip(), str(values["league"]).strip()
        scopes[sport+"|"+league] += 1
        void_flag = str(values["is_void"]).lower()
        if void_flag not in ("true", "1", "false", "0"):
            counts["invalid_void_flag"] += 1
            continue
        if void_flag in ("true", "1"):
            counts["void"] += 1
            continue
        result = str(values["result"]).strip()
        if result not in SUPPORTED[(market, n)]:
            counts["unknown_or_unsettled:"+market+":"+result] += 1
            continue
        try:
            odds = tuple(float(x) for x in str(values["odds"]).split(","))
            if len(odds) != n or not all(math.isfinite(x) and x > 1 for x in odds):
                raise ValueError("invalid odds")
            if not 1.0 <= sum(1/x for x in odds) <= 1.40:
                raise ValueError("unsupported overround")
        except ValueError:
            counts["invalid_odds"] += 1
            continue
        home, away = clean_team(values["home"]), clean_team(values["away"])
        if not sport or not league or not home or not away or home == away:
            counts["invalid_teams_scope"] += 1
            continue
        event = (kickoff.isoformat(), sport, league, home, away)
        label = str(values["market_label"]).strip()
        offer = (*event, market, str(values["booking_class"]), label, n)
        winner = winner_index(n, result)
        previous = records.get(offer)
        if previous is not None:
            if previous["odds"] != odds or previous["winner"] != winner:
                conflicts.add(offer)
            else:
                counts["duplicate_same_offer"] += 1
            continue
        records[offer] = {"event": digest(event), "offer": digest(offer), "day": day,
                          "kickoff": kickoff.isoformat(), "sport": sport, "league": league,
                          "market": market, "n_way": n, "odds": odds, "winner": winner}
    clean = [r for key, r in records.items() if key not in conflicts]
    for row in clean:
        row["q"] = market_probabilities(list(row["odds"]))
    clean.sort(key=lambda r: (r["kickoff"], r["offer"]))
    counts["conflicting_offers_excluded"] = len(conflicts)
    return clean, {"input_rows": len(raw), "usable_markets": len(clean),
                   "unique_events": len({r["event"] for r in clean}),
                   "excluded_or_duplicates": dict(counts), "raw_supported_scopes": dict(scopes),
                   "first_day": min((r["day"] for r in clean), default=None),
                   "last_day": max((r["day"] for r in clean), default=None)}


def scope(row):
    return row["sport"], row["league"], row["market"], row["n_way"]


def fit(rows, anchor, candidate, ridge):
    scopes = {scope(r) for r in rows}
    if len(scopes) != 1:
        raise ValueError("cross-league or cross-market training prohibited")
    if not rows or max(r["day"] for r in rows) >= anchor:
        raise ValueError("training must strictly precede anchor")
    q = np.asarray([r["q"] for r in rows], dtype=float)
    logq = np.log(np.clip(q, 1e-12, 1))
    y = np.asarray([r["winner"] for r in rows], dtype=int)
    classes = q.shape[1]
    half = candidate["half_life_days"]
    age = np.asarray([(pd.Timestamp(anchor)-pd.Timestamp(r["day"])).days for r in rows])
    weights = np.ones(len(rows)) if half is None else np.exp2(-age/half)
    weights *= len(weights)/weights.sum()

    def objective(theta):
        logits = (1+theta[0])*logq
        logits[:, :-1] += theta[1:]
        logp = logits-logsumexp(logits, axis=1)[:, None]
        error = np.exp(logp)
        error[np.arange(len(y)), y] -= 1
        error *= weights[:, None]
        value = -np.sum(weights*logp[np.arange(len(y)), y]) + .5*ridge*np.dot(theta, theta)
        gradient = np.r_[np.sum(error*logq), error[:, :-1].sum(axis=0)] + ridge*theta
        return value, gradient

    result = minimize(objective, np.zeros(classes), jac=True, method="L-BFGS-B",
                      bounds=[(-.95, 4)]+[(-5, 5)]*(classes-1),
                      options={"maxiter": 300, "ftol": 1e-12, "gtol": 1e-7})
    if not result.success:
        raise ValueError("optimizer failed: "+str(result.message))
    return {"scope": list(next(iter(scopes))), "theta": result.x.tolist(),
            "train_n": len(rows), "train_first": min(r["day"] for r in rows),
            "train_last": max(r["day"] for r in rows),
            "train_hash": digest([{k: r[k] for k in ("offer", "q", "winner", "day")} for r in rows])}


def predict(rows, model):
    if any(list(scope(r)) != model["scope"] for r in rows):
        raise ValueError("model cannot cross league/market scope")
    theta = np.asarray(model["theta"])
    logits = (1+theta[0])*np.log(np.clip(np.asarray([r["q"] for r in rows]), 1e-12, 1))
    logits[:, :-1] += theta[1:]
    return np.exp(logits-logsumexp(logits, axis=1)[:, None])


def metrics_per_row(row, probability):
    p = np.asarray(probability)
    y = np.zeros(len(p)); y[row["winner"]] = 1
    choice = int(np.argmax(p))
    return {"brier": float(np.sum((p-y)**2)),
            "log_loss": float(-math.log(max(float(p[row["winner"]]), 1e-12))),
            "hit": int(choice == row["winner"]), "odds": row["odds"][choice],
            "profit": row["odds"][choice]-1 if choice == row["winner"] else -1,
            "choice": choice}


def average(rows, candidate):
    values = [r["metrics"][candidate] for r in rows]
    return {"n": len(rows), **{k: float(np.mean([v[k] for v in values]))
                               for k in ("brier", "log_loss", "hit", "odds", "profit")}}


def diagnostic(rows, candidate, protocol, seed):
    weeks = defaultdict(lambda: np.zeros(3))
    for r in rows:
        day = pd.Timestamp(r["day"])
        monday = day-pd.Timedelta(days=day.weekday())
        gain = [r["metrics"]["baseline"][k]-r["metrics"][candidate][k]
                for k in ("brier", "log_loss")]
        weeks[monday] += np.r_[gain, 1]
    timeline = pd.date_range(min(weeks), max(weeks), freq="7D")
    values = np.asarray([weeks[d] for d in timeline])
    observed = values[:, :2].sum(axis=0)/values[:, 2].sum()
    if len(weeks) < 2:
        return {"active_weeks": len(weeks), "calendar_weeks": len(timeline),
                "gain": observed.tolist(), "ci95": None, "p_two_sided": [None, None]}
    width = min(protocol["block_calendar_weeks"], len(values))
    rng = np.random.default_rng(seed)
    draws = []
    for start in range(0, protocol["bootstrap_repeats"], 500):
        size = min(500, protocol["bootstrap_repeats"]-start)
        starts = rng.integers(0, len(values), size=(size, math.ceil(len(values)/width)))
        indexes = ((starts[:, :, None]+np.arange(width)) % len(values)).reshape(size, -1)[:, :len(values)]
        sums = values[indexes].sum(axis=1)
        valid = sums[:, 2] > 0
        draws.append(sums[valid, :2]/sums[valid, 2, None])
    sampled = np.concatenate(draws)
    intervals = np.quantile(sampled, [.025, .975], axis=0).T
    # Centered paired block-bootstrap diagnostic; not an exact randomization p.
    ps = (1+np.sum(np.abs(sampled-observed) >= np.abs(observed), axis=0))/(len(sampled)+1)
    return {"active_weeks": len(weeks), "calendar_weeks": len(timeline),
            "gain": observed.tolist(), "ci95": intervals.tolist(),
            "p_two_sided": ps.tolist(), "bootstrap_valid_draws": len(sampled)}


def holm(pvalues):
    valid = sorted((v, k) for k, v in pvalues.items() if v is not None)
    adjusted, last = {k: None for k in pvalues}, 0.0
    for i, (p, key) in enumerate(valid):
        last = max(last, min(1., (len(valid)-i)*p))
        adjusted[key] = last
    return adjusted


def run_league(rows, protocol):
    if len({(r["sport"], r["league"]) for r in rows}) != 1:
        raise ValueError("independent leagues required")
    groups = defaultdict(list)
    for row in sorted(rows, key=lambda r: (r["kickoff"], r["offer"])):
        groups[scope(row)].append(row)
    first = pd.Timestamp(protocol["evaluation_start"])
    last = pd.Timestamp(protocol["evaluation_end"])+pd.Timedelta(days=1)
    quarter = first.to_period("Q").start_time
    evaluated, folds, skipped = [], [], []
    while quarter < last:
        next_quarter = quarter+pd.offsets.QuarterBegin(startingMonth=1)
        eval_start = max(first, quarter).date().isoformat()
        eval_end = min(last, next_quarter).date().isoformat()
        train_end = (quarter-pd.Timedelta(days=protocol["embargo_days"])).date().isoformat()
        for market_scope, offers in sorted(groups.items()):
            training = [r for r in offers if protocol["training_start"] <= r["day"] < train_end]
            evaluation = [r for r in offers if eval_start <= r["day"] < eval_end]
            if not evaluation:
                continue
            if len(training) < protocol["minimum_training_markets"]:
                skipped.append({"scope": list(market_scope), "quarter": str(quarter.date()),
                                "training": len(training), "evaluation": len(evaluation),
                                "reason": "insufficient_same_league_market_training"})
                continue
            outputs, models = {}, {}
            for c in protocol["candidates"]:
                model = fit(training, train_end, c, protocol["ridge"])
                outputs[c["name"]] = predict(evaluation, model)
                models[c["name"]] = model
            folds.append({"scope": list(market_scope), "evaluation_start": eval_start,
                          "evaluation_end_exclusive": eval_end, "train_cutoff_exclusive": train_end,
                          "evaluation_n": len(evaluation), "models": models})
            for i, row in enumerate(evaluation):
                evaluated.append({**row, "metrics": {
                    "baseline": metrics_per_row(row, row["q"]),
                    **{c: metrics_per_row(row, ps[i]) for c, ps in outputs.items()}}})
        quarter = next_quarter
    evaluated.sort(key=lambda r: (r["kickoff"], r["offer"]))
    return evaluated, folds, skipped


def experiment(rows, quality, protocol):
    validate_protocol(protocol)
    leagues = defaultdict(list)
    for r in rows:
        leagues[(r["sport"], r["league"])].append(r)
    results, pvalues = {}, {}
    for index, (league_scope, records) in enumerate(sorted(leagues.items())):
        name = "|".join(league_scope)
        evaluated, folds, skipped = run_league(records, protocol)
        entry = {"sport": league_scope[0], "league": league_scope[1],
                 "available_markets": len(records), "evaluation_markets": len(evaluated),
                 "folds": folds, "skipped": skipped}
        if not evaluated:
            entry["status"] = "insufficient_training"
            results[name] = entry
            continue
        entry.update(first_evaluation=min(r["day"] for r in evaluated),
                     last_evaluation=max(r["day"] for r in evaluated),
                     evaluation_events=len({r["event"] for r in evaluated}),
                     baseline=average(evaluated, "baseline"), candidates={})
        for c in protocol["candidates"]:
            candidate = c["name"]
            stable_seed = int(digest([protocol["seed"], name])[:8], 16)
            diag = diagnostic(evaluated, candidate, protocol, stable_seed)
            by_year = {}
            for year in sorted({r["day"][:4] for r in evaluated}):
                rs = [r for r in evaluated if r["day"].startswith(year)]
                by_year[year] = {"baseline": average(rs, "baseline"), "candidate": average(rs, candidate)}
            by_market = {}
            for market_scope in sorted({(r["market"], r["n_way"]) for r in evaluated}):
                rs = [r for r in evaluated if (r["market"], r["n_way"]) == market_scope]
                by_market[market_scope[0]+"|"+str(market_scope[1])] = {
                    "baseline": average(rs, "baseline"), "candidate": average(rs, candidate)}
            entry["candidates"][candidate] = {"metrics": average(evaluated, candidate),
                "diagnostic": diag, "by_year": by_year, "by_market": by_market,
                "choice_changed": sum(r["metrics"][candidate]["choice"] != r["metrics"]["baseline"]["choice"] for r in evaluated)}
            for i, metric in enumerate(("brier", "log_loss")):
                pvalues[name+":"+candidate+":"+metric] = diag["p_two_sided"][i]
        results[name] = entry
        print("evaluated "+name+" "+str(len(evaluated))+" markets", flush=True)
    adjusted = holm(pvalues)
    for name, entry in results.items():
        if not entry["evaluation_markets"]:
            continue
        any_signal = False
        for candidate, result in entry["candidates"].items():
            result["holm_adjusted_p"] = {k: adjusted[name+":"+candidate+":"+k] for k in ("brier", "log_loss")}
            enough = (entry["evaluation_markets"] >= protocol["minimum_evaluation_markets"]
                      and entry["evaluation_events"] >= protocol["minimum_evaluation_events"]
                      and result["diagnostic"]["active_weeks"] >= protocol["minimum_evaluation_weeks"])
            both_years = (len(result["by_year"]) >= 2 and all(
                yr["baseline"][k] > yr["candidate"][k]
                for yr in result["by_year"].values() for k in ("brier", "log_loss")))
            intervals = result["diagnostic"]["ci95"]
            statistical = bool(intervals and all(ci[0] > 0 for ci in intervals)
                and all(p is not None and p < protocol["alpha"] for p in result["holm_adjusted_p"].values()))
            result["checks"] = {"enough_evaluation": enough, "both_years_improve": both_years,
                                "losses_improve_after_multiplicity": statistical}
            result["historical_signal"] = enough and both_years and statistical
            any_signal |= result["historical_signal"]
        entry["status"] = "historical_signal_only" if any_signal else "not_established"
    return {"schema": "independent-league-archive-report-v1", "protocol": protocol,
            "protocol_sha256": digest(protocol), "quality": quality, "leagues": results,
            "hypotheses_adjusted": len(pvalues), "production_allowed": False, "pristine_future": False,
            "limitations": ["Archive odds and result observation times unknown; not a deployable T-30 backtest.",
              "Historical research data already explored; significance is retrospective, not preregistered future evidence.",
              "Rows are independent market offers after dedup, not independent physical games or stored service picks.",
              "Class calibration only: xG, pitcher and lineup feature weights not evaluated.",
              "Missing/conflicting archives are excluded nonrandomly; uncovered fixtures and leagues cannot be inferred."]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--protocol", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.output.exists() or args.output.resolve() in {args.input.resolve(), args.protocol.resolve()}:
        ap.error("output must be a new file, distinct from inputs")
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    before = file_hash(args.input)
    rows, quality = normalize(pd.read_csv(args.input, low_memory=False), protocol)
    if before != file_hash(args.input):
        raise ValueError("archive changed while reading")
    print(json.dumps(quality, ensure_ascii=False), flush=True)
    report = experiment(rows, quality, protocol)
    report["source"] = {"name": args.input.name, "sha256": before, "normalized_sha256": digest(rows)}
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    print("report: "+str(args.output), flush=True)


if __name__ == "__main__":
    main()
