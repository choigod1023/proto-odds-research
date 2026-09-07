"""Offline score forecasts -> frozen reliability models -> real UI pick policy.

No production data stores or model promotion. The archive is read-only and the
report is exclusively created. Outcome fields never cross the Node policy bridge.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path
import platform
import subprocess

import numpy as np
import pandas as pd
import scipy
from scipy.optimize import minimize
from scipy.special import expit, logit

from score_replay_model import fit_score_model
from team_style_archive import digest, file_hash, holm, normalize, scope
from team_style_features import score_history

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ("market", "score", "blend", "selector_market", "selector_score")
BRIDGE = ROOT / "scripts" / "replay_recommendation_policy.mjs"


def validate_protocol(p):
    if p["schema"] != "score-selection-replay-v1" or p["production_allowed"] is not False:
        raise ValueError("offline protocol required")
    dates = [pd.Timestamp(p[k]) for k in ("training_start", "inner_start",
             "selection_cutoff_exclusive", "evaluation_start", "evaluation_end")]
    if not all(a < b for a, b in zip(dates, dates[1:])):
        raise ValueError("invalid temporal order")
    if dates[2] > dates[3]-pd.Timedelta(days=p["embargo_days"]):
        raise ValueError("selection cutoff violates outer embargo")
    for key in ("embargo_days", "half_life_days", "score_ridge", "selector_ridge",
                "minimum_selector_markets", "minimum_selector_events", "minimum_selector_weeks",
                "minimum_selector_class", "block_calendar_weeks", "bootstrap_repeats",
                "matched_odds_bin_width", "minimum_assessment_picks", "minimum_assessment_weeks"):
        if not math.isfinite(float(p[key])) or p[key] <= 0:
            raise ValueError("invalid positive setting: "+key)
    if p["strategies"] != list(STRATEGIES) or not set(p["sports"]) <= {"sc", "bs", "bk"}:
        raise ValueError("unsupported strategies/sports")
    if not p["blend_grid"] or not all(0 <= a <= 1 for a in p["blend_grid"]):
        raise ValueError("invalid blend grid")


def quarterly_predictions(args):
    league_scope, rows, matches, p = args
    first = pd.Timestamp(p["inner_start"]).to_period("Q").start_time
    last = pd.Timestamp(p["evaluation_end"])
    output, folds = [], []
    for start in pd.date_range(first, last, freq="QS"):
        end = min(start+pd.DateOffset(months=3), last+pd.Timedelta(days=1))
        target = [r for r in rows if max(start.date().isoformat(), p["inner_start"])
                  <= r["day"] < end.date().isoformat()]
        if not target:
            continue
        cutoff = start-pd.Timedelta(days=p["embargo_days"])
        model = fit_score_model(matches, cutoff, half_life_days=p["half_life_days"],
                                ridge=p["score_ridge"])
        folds.append({"start": start.date().isoformat(), "end_exclusive": end.date().isoformat(),
                      "cutoff_exclusive": cutoff.date().isoformat(), "markets": len(target),
                      "model": model.metadata if model else {"status": "insufficient_score_history"}})
        for row in target:
            prob = model.predict(row) if model else row["q"]
            if prob is None:
                raise RuntimeError("Unpriceable score offer: "+str(row["offer"])+" "+row["market_label"])
            prob = np.asarray(prob, dtype=float)
            if len(prob) != row["n_way"] or not np.isfinite(prob).all() or (prob < 0).any():
                raise ValueError("invalid score distribution")
            output.append({**row, "score_p": prob.tolist(), "score_available": model is not None})
    return league_scope, output, folds


def inner_rows(rows, p):
    return [r for r in rows if p["inner_start"] <= r["day"] < p["selection_cutoff_exclusive"]
            and r["winner"] is not None]


def supported_score_offer(row):
    if row["market"] in ("승패", "승무패"):
        return row["market_label"] == ""
    try:
        line = float(row["market_label"].split()[1])
    except (ValueError, IndexError):
        return False
    return (math.isfinite(line) and line*2 == round(line*2) and
            (row["market"] != "언더오버" or line >= 0))


def support(rows, p):
    counts = {"markets": len(rows), "events": len({r["event"] for r in rows}),
              "weeks": len({str(pd.Timestamp(r["day"]).to_period("W-SUN")) for r in rows})}
    ok = (counts["markets"] >= p["minimum_selector_markets"] and
          counts["events"] >= p["minimum_selector_events"] and
          counts["weeks"] >= p["minimum_selector_weeks"])
    return counts, ok


def selector_features(row, choice, score):
    q = np.clip(np.asarray(row["q"]), 1e-8, 1-1e-8)
    ordered = np.sort(q)
    x = [float(logit(q[choice])), math.log(row["odds"][choice]),
         sum(1/o for o in row["odds"])-1, float(ordered[-1]-ordered[-2]),
         float(-np.sum(q*np.log(q)))]
    if score:
        s = float(np.clip(row["score_p"][choice], 1e-8, 1-1e-8))
        x.extend([float(logit(s)-logit(q[choice])), abs(s-q[choice]),
                  float(row["score_available"])])
    return x


def fit_selector(rows, p, score):
    rows = inner_rows(rows, p)
    rows = [r for r in rows if any(q >= max(r["q"])-1e-9 and 1 < r["odds"][i] < 2.2
                                  for i, q in enumerate(r["q"]))]
    counts, ok = support(rows, p)
    base = {"support": counts, "cutoff_exclusive": p["selection_cutoff_exclusive"],
            "score_features": score, "status": "insufficient_inner_history"}
    if not ok:
        return base
    if len({scope(r) for r in rows}) != 1:
        raise ValueError("selector cannot pool leagues or markets")
    training = [(r, i) for r in rows for i, q in enumerate(r["q"])
                if q >= max(r["q"])-1e-9 and 1 < r["odds"][i] < 2.2]
    y = np.asarray([int(i == r["winner"]) for r, i in training], dtype=float)
    if min(y.sum(), len(y)-y.sum()) < p["minimum_selector_class"]:
        return {**base, "status": "insufficient_inner_outcomes"}
    raw = np.asarray([selector_features(r, i, score) for r, i in training])
    center, scale = np.mean(raw, axis=0), np.std(raw, axis=0)
    scale[scale < 1e-8] = 1
    x = np.c_[np.ones(len(raw)), np.clip((raw-center)/scale, -5, 5)]
    offset = logit(np.clip([r["q"][i] for r, i in training], 1e-8, 1-1e-8))
    ridge = p["selector_ridge"]
    def objective(beta):
        z = offset+x@beta
        return (float(np.sum(np.logaddexp(0, z)-y*z)+ridge*np.dot(beta, beta)/2),
                x.T@(expit(z)-y)+ridge*beta)
    def hessian(beta):
        prob = expit(offset+x@beta)
        return x.T@((prob*(1-prob))[:, None]*x)+ridge*np.eye(x.shape[1])
    fit = minimize(objective, np.zeros(x.shape[1]), jac=True, hess=hessian,
                   method="trust-exact", options={"gtol": 1e-5, "maxiter": 150})
    solver = "trust-exact"
    def converged(result):
        if not np.isfinite(result.x).all():
            return False
        loss, gradient = objective(result.x)
        return math.isfinite(loss) and np.linalg.norm(gradient) <= 1e-4
    if not converged(fit):
        solver = "L-BFGS-B cold retry"
        fit = minimize(objective, np.zeros(x.shape[1]), jac=True, method="L-BFGS-B",
                       options={"gtol": 1e-8, "ftol": 1e-14, "maxiter": 500, "maxls": 50})
    if not converged(fit):
        raise RuntimeError("selector fit failed "+str(scope(rows[0]))+": "+str(fit.message))
    # Ridge makes the objective strongly convex. The gradient norm certifies
    # an objective gap <= ||gradient||^2/(2*ridge), even if SciPy reports a
    # near-optimum trust-region precision warning. Never exclude this scope.
    gradient_norm = float(np.linalg.norm(objective(fit.x)[1]))
    result = {**base, "status": "trained_pre2025", "center": center.tolist(),
              "scale": scale.tolist(), "beta": fit.x.tolist(), "training_options": len(y),
              "training_last_day": max(r["day"] for r, _ in training), "ridge": ridge,
              "solver": solver, "solver_reported_success": bool(fit.success),
              "gradient_l2": gradient_norm,
              "objective_gap_upper_bound": gradient_norm**2/(2*ridge)}
    result["hash"] = digest(result)
    return result


def selector_probability(row, choice, model):
    q = row["q"][choice]
    if model["status"] != "trained_pre2025":
        return q
    raw = np.asarray(selector_features(row, choice, model["score_features"]))
    x = np.r_[1., np.clip((raw-model["center"])/model["scale"], -5, 5)]
    return float(expit(logit(np.clip(q, 1e-8, 1-1e-8))+x@model["beta"]))


def choose_blend(rows, p):
    rows = inner_rows(rows, p)
    counts, ok = support(rows, p)
    if not ok:
        return {"alpha": 0., "status": "insufficient_inner_history", "support": counts}
    if len({scope(r) for r in rows}) != 1:
        raise ValueError("blend cannot pool scopes")
    losses = [{"alpha": a, "log_loss": float(np.mean([
        -math.log(max((1-a)*r["q"][r["winner"]]+a*r["score_p"][r["winner"]], 1e-12))
        for r in rows]))} for a in p["blend_grid"]]
    chosen = min(losses, key=lambda x: (x["log_loss"], x["alpha"]))
    return {**chosen, "grid": losses, "status": "selected_pre2025", "support": counts}


def make_options(rows, learned):
    options = {name: [] for name in STRATEGIES}
    outcomes = {}
    for r in rows:
        setting = learned["|".join(map(str, scope(r)))]
        a = setting["blend"]["alpha"]
        for i, odds in enumerate(r["odds"]):
            sid = r["offer"]+":"+str(i)
            base = {"selection_id": sid, "event_key": r["event"], "sport": r["sport"],
                    "league": r["league"], "kickoff_at": r["kickoff"]+"+09:00",
                    "market": r["market"], "market_label": r["market_label"],
                    "sel": str(i), "odds": odds, "market_prob": r["q"][i],
                    "is_market_favorite": r["q"][i] >= max(r["q"])-1e-9,
                    "n_way": r["n_way"], "game_no": r["offer"], "round": "research"}
            ps = {"market": r["q"][i], "score": r["score_p"][i],
                  "blend": (1-a)*r["q"][i]+a*r["score_p"][i],
                  "selector_market": selector_probability(r, i, setting["selector_market"]),
                  "selector_score": selector_probability(r, i, setting["selector_score"])}
            for name, prob in ps.items():
                options[name].append({**base, "predicted_hit_prob": float(np.clip(prob, 1e-8, 1-1e-8))})
            won = None if r["winner"] is None else int(i == r["winner"])
            outcomes[sid] = {"id": sid, "event": r["event"], "day": r["day"],
                "sport": r["sport"], "league": r["league"], "market": r["market"],
                "n_way": r["n_way"], "odds": odds, "q": r["q"][i], "hit": won,
                "profit": 0. if r["is_void"] else odds-1 if won else -1.,
                "void": r["is_void"], "score_available": r["score_available"]}
    return options, outcomes


def run_policy(options):
    result = subprocess.run(["node", str(BRIDGE)], input=json.dumps({"strategies": options},
        ensure_ascii=False, allow_nan=False), text=True, encoding="utf-8", capture_output=True,
        check=False, cwd=ROOT)
    if result.returncode:
        raise RuntimeError("Recommendation policy bridge failed: "+result.stderr.strip())
    return json.loads(result.stdout)


def summary(picks, universe_days):
    graded = [r for r in picks if r["hit"] is not None]
    n, wins = len(graded), sum(r["hit"] for r in graded)
    return {"picks": len(picks), "graded": n, "wins": wins,
            "void": sum(r["void"] for r in picks),
            "accuracy": wins/n if n else None,
            "average_odds": float(np.mean([r["odds"] for r in picks])) if picks else None,
            "roi": float(np.mean([r["profit"] for r in picks])) if picks else None,
            "days": len({r["day"] for r in picks}), "universe_days": len(universe_days),
            "day_coverage": len({r["day"] for r in picks})/len(universe_days) if universe_days else None,
            "score_available_share": sum(r["score_available"] for r in picks)/len(picks) if picks else None,
            "low_odds_share": sum(r["odds"] < 1.5 for r in picks)/len(picks) if picks else None}


def compare_sets(candidate, reference, p, seed):
    """Paired calendar bootstrap of ratio differences, not independent row CIs."""
    if not candidate or not reference:
        return {"status": "no_comparable_picks", "gain": None, "ci95": None,
                "p_two_sided": None, "active_weeks": 0}
    weeks = defaultdict(lambda: np.zeros(10))
    for offset, rows in ((0, candidate), (5, reference)):
        for r in rows:
            d = pd.Timestamp(r["day"])
            monday = d-pd.Timedelta(days=d.weekday())
            weeks[monday][offset:offset+5] += [r["hit"] or 0, int(r["hit"] is not None),
                                             r["profit"], r["odds"], 1]
    active = len(weeks)
    side_weeks = [len({str(pd.Timestamp(r["day"]).to_period("W-SUN")) for r in part
                      if r["hit"] is not None}) for part in (candidate, reference)]
    timeline = pd.date_range(min(weeks), max(weeks), freq="7D")
    values = np.asarray([weeks.get(day, np.zeros(10)) for day in timeline])
    def difference(s):
        return np.stack([s[..., 0]/s[..., 1]-s[..., 5]/s[..., 6],
                         s[..., 2]/s[..., 4]-s[..., 7]/s[..., 9],
                         s[..., 3]/s[..., 4]-s[..., 8]/s[..., 9]], axis=-1)
    summed = values.sum(axis=0)
    if summed[1] == 0 or summed[6] == 0:
        return {"status": "no_graded_picks", "gain": None, "ci95": None,
                "p_two_sided": None, "active_weeks": active}
    observed = difference(summed)
    if min(side_weeks) < 2:
        return {"status": "insufficient_temporal_support", "gain": observed.tolist(),
                "ci95": None, "p_two_sided": None, "active_weeks": active,
                "graded_active_weeks_by_side": side_weeks}
    rng = np.random.default_rng(seed)
    width, samples = min(p["block_calendar_weeks"], len(values)), []
    for first in range(0, p["bootstrap_repeats"], 500):
        n = min(500, p["bootstrap_repeats"]-first)
        starts = rng.integers(0, len(values), size=(n, math.ceil(len(values)/width)))
        indexes = ((starts[..., None]+np.arange(width)) % len(values)).reshape(n, -1)[:, :len(values)]
        sums = values[indexes].sum(axis=1)
        valid = (sums[:, 1] > 0) & (sums[:, 6] > 0)
        samples.append(difference(sums[valid]))
    draws = np.concatenate(samples)
    pv = float((1+np.sum(abs(draws[:, 0]-observed[0]) >= abs(observed[0])))/(len(draws)+1))
    return {"status": "exploratory", "metrics": ["accuracy", "roi", "average_odds"],
            "gain": observed.tolist(), "ci95": np.quantile(draws, [.025, .975], axis=0).T.tolist(),
            "p_two_sided": pv, "active_weeks": active, "graded_active_weeks_by_side": side_weeks,
            "calendar_weeks": len(values),
            "bootstrap_valid_draws": len(draws)}


def matched_reference(candidate, reference_candidates, p):
    """Same-day/league/market/price-bin budget control, outcome-blind matching."""
    def key(r):
        return (r["day"], r["sport"], r["league"], r["market"], r["n_way"],
                math.floor((r["odds"]+1e-9)/p["matched_odds_bin_width"]))
    bins = defaultdict(list)
    for r in reference_candidates:
        bins[key(r)].append(r)
    for rows in bins.values():
        rows.sort(key=lambda r: (-r["q"], r["odds"], r["id"]))
    left, right, used = [], [], set()
    for row in sorted(candidate, key=lambda r: (r["day"], r["id"])):
        available = [r for r in bins[key(row)] if r["event"] not in used]
        if available:
            other = available[0]
            left.append(row); right.append(other); used.add(other["event"])
    return left, right


def aggregate(rows, policy, outcomes, p):
    # The bridge returns identifiers only, so outcomes are joined AFTER selection.
    strategies = policy["strategies"]
    selected = {name: [outcomes[sid] for sid in strategies[name]["highlighted"]] for name in STRATEGIES}
    base_candidates = [outcomes[sid] for sid in strategies["market"]["event_choices"]
                       if outcomes[sid]["q"] >= .55]
    by_day_league = defaultdict(list)
    for r in base_candidates:
        by_day_league[r["day"], r["sport"], r["league"]].append(r)
    base_pool = [r for group in by_day_league.values()
                 for r in ([v for v in group if v["odds"] >= 1.5] or group)]
    scopes = {"all": lambda r: True}
    for sport in sorted({r["sport"] for r in rows}):
        scopes["sport:"+sport] = lambda r, sport=sport: r["sport"] == sport
    for sport, league in sorted({(r["sport"], r["league"]) for r in rows}):
        scopes[sport+"|"+league] = lambda r, s=sport, l=league: (r["sport"], r["league"]) == (s, l)
    report, tests = {}, {}
    for name, keep in scopes.items():
        universe = {r["day"] for r in rows if keep(r)}
        choices = {arm: [r for r in picks if keep(r)] for arm, picks in selected.items()}
        item = {"metrics": {arm: summary(picks, universe) for arm, picks in choices.items()},
                "comparisons": {}, "by_year": {}, "by_market": {}}
        incremental = compare_sets(choices["selector_score"], choices["selector_market"], p,
                                  p["seed"]+int(digest([name, "incremental"])[:8], 16))
        item["score_information_control"] = incremental
        tests[name+"|score_information_control"] = incremental["p_two_sided"]
        for year in sorted({d[:4] for d in universe}):
            item["by_year"][year] = {arm: summary([r for r in picks if r["day"].startswith(year)],
                {d for d in universe if d.startswith(year)}) for arm, picks in choices.items()}
        for market in sorted({r["market"]+"|"+str(r["n_way"]) for r in rows if keep(r)}):
            item["by_market"][market] = {arm: summary([r for r in picks
                if r["market"]+"|"+str(r["n_way"]) == market], universe) for arm, picks in choices.items()}
        for arm in STRATEGIES[1:]:
            seed = p["seed"]+int(digest([name, arm])[:8], 16)
            comparison = compare_sets(choices[arm], choices["market"], p, seed)
            left, right = matched_reference(choices[arm], [r for r in base_pool if keep(r)], p)
            matched = compare_sets(left, right, p, seed)
            matched.update(candidate=summary(left, universe), reference=summary(right, universe),
                           retention=len(left)/len(choices[arm]) if choices[arm] else 0)
            item["comparisons"][arm] = {"policy": comparison, "matched": matched}
            tests[name+"|"+arm+"|policy"] = comparison["p_two_sided"]
            tests[name+"|"+arm+"|matched"] = matched["p_two_sided"]
        report[name] = item
    adjusted = holm(tests)
    for name, item in report.items():
        item["score_information_control"]["holm_p"] = adjusted[name+"|score_information_control"]
        for arm, pair in item["comparisons"].items():
            for comparison_name, c in pair.items():
                c["holm_p"] = adjusted[name+"|"+arm+"|"+comparison_name]
            a, b = item["metrics"][arm], item["metrics"]["market"]
            c, m = pair["policy"], pair["matched"]
            def improvement(x):
                return bool(x["ci95"] and x["ci95"][0][0] > 0 and x["holm_p"] < .05
                            and min(x["graded_active_weeks_by_side"]) >= p["minimum_assessment_weeks"])
            expected_years = {str(y) for y in range(int(p["evaluation_start"][:4]), int(p["evaluation_end"][:4])+1)}
            positive_years = set(item["by_year"]) == expected_years and all(v[arm]["accuracy"] is not None and v["market"]["accuracy"] is not None
                and v[arm]["accuracy"] > v["market"]["accuracy"] for v in item["by_year"].values())
            checks = {"policy_accuracy_ci": improvement(c), "matched_accuracy_ci": improvement(m),
                      "sufficient_picks": min(a["graded"], b["graded"], m["candidate"]["graded"],
                                              m["reference"]["graded"]) >= p["minimum_assessment_picks"],
                      "pick_count_preserved": a["picks"] >= b["picks"]*p["minimum_pick_count_ratio"],
                      "recommendation_days_preserved": a["days"] >= b["days"]*p["minimum_pick_count_ratio"],
                      "price_preserved": a["average_odds"] is not None and b["average_odds"] is not None
                          and a["average_odds"] >= b["average_odds"]-p["maximum_average_odds_drop"],
                      "matched_retention": m["retention"] >= p["minimum_matched_retention"],
                      "both_years_improved": positive_years}
            pair["checks"] = checks
            if arm == "selector_score":
                checks["score_information_improves_market_selector"] = improvement(item["score_information_control"])
            pair["historical_signal"] = all(checks.values())
    return {"scopes": report, "comparisons_count": len(tests),
            "selected_hashes": {arm: digest(picks) for arm, picks in selected.items()},
            "recent_pick_examples": {arm: sorted(picks, key=lambda r: (r["day"], r["id"]))[-20:]
                                      for arm, picks in selected.items()}}


def forecast_metrics(rows, learned):
    """Common settled market rows, before recommendation selection.

    Reliability selectors score binary favorite correctness; their outputs are
    NOT coherent full-outcome vectors and must not receive a multiclass Brier.
    """
    values = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["winner"] is None:
            continue
        key = row["sport"]+"|"+row["league"]
        a = learned["|".join(map(str, scope(row)))]["blend"]["alpha"]
        q, s = np.asarray(row["q"]), np.asarray(row["score_p"])
        y = np.zeros(row["n_way"]); y[row["winner"]] = 1
        for arm, prob in (("market", q), ("score", s), ("blend", (1-a)*q+a*s)):
            values[key][arm].append([np.sum((prob-y)**2), -math.log(max(prob[row["winner"]], 1e-12)),
                                    int(np.argmax(prob) == row["winner"])])
    return {key: {arm: {"n": len(v), **dict(zip(("brier", "log_loss", "all_market_hit_rate"),
                                               np.mean(v, axis=0).tolist()))}
                  for arm, v in arms.items()} for key, arms in values.items()}


def source_hashes(games, protocol):
    files = [ROOT/"src"/name for name in ("score_selection_replay.py", "score_replay_model.py",
             "team_style_archive.py", "team_style_features.py", "matches.py", "bets.py", "devig.py")]
    # Hash the full frontend dependency surface used by the actual policy import.
    files += list((ROOT/"web"/"src"/"lib").glob("*.js"))+[BRIDGE]
    return {"games": file_hash(games), "protocol": file_hash(protocol),
            "code": {str(f.relative_to(ROOT)).replace("\\", "/"): file_hash(f) for f in sorted(files)}}


def run(games, protocol_path, workers):
    p = json.loads(Path(protocol_path).read_text(encoding="utf-8")); validate_protocol(p)
    before = source_hashes(games, protocol_path)
    raw = pd.read_csv(games, dtype=str, keep_default_na=False)
    records, quality = normalize(raw, p, include_void=True)
    matches, score_quality = score_history(raw)
    quality["excluded_unsupported_sport_markets"] = sum(r["sport"] not in p["sports"] for r in records)
    quality["excluded_nonpositive_margin_markets"] = sum(sum(1/o for o in r["odds"]) <= 1 for r in records)
    quality["excluded_unsupported_score_lines"] = sum(not supported_score_offer(r) for r in records)
    records = [r for r in records if r["sport"] in p["sports"] and sum(1/o for o in r["odds"]) > 1
               and supported_score_offer(r)]
    matches = [r for r in matches if p["training_start"] <= r["kickoff"].date().isoformat() <= p["evaluation_end"]]
    grouped, scores = defaultdict(list), defaultdict(list)
    for r in records:
        grouped[r["sport"], r["league"]].append(r)
    for r in matches:
        scores[r["sport"], r["league"]].append(r)
    jobs = [(key, rows, scores[key], p) for key, rows in sorted(grouped.items())]
    predictions, folds = [], {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for key, rows, model_folds in executor.map(quarterly_predictions, jobs):
            predictions.extend(rows); folds["|".join(key)] = model_folds
            print("|".join(key), len(rows), flush=True)
    separated = defaultdict(list)
    for r in predictions:
        separated[scope(r)].append(r)
    learned = {"|".join(map(str, key)): {"blend": choose_blend(rows, p),
                "selector_market": fit_selector(rows, p, False),
                "selector_score": fit_selector(rows, p, True)} for key, rows in sorted(separated.items())}
    evaluation = [r for r in predictions if p["evaluation_start"] <= r["day"] <= p["evaluation_end"]]
    # League partitions are independent in the policy. Bound the JSON bridge's
    # memory without changing the daily choice sets or model predictions.
    batches = defaultdict(list)
    for row in evaluation:
        batches[row["sport"], row["league"]].append(row)
    outcomes, policy = {}, {"strategies": {arm: {"event_choices": [], "highlighted": []}
                                           for arm in STRATEGIES}}
    for key, batch in sorted(batches.items()):
        options, labels = make_options(batch, learned)
        result = run_policy(options)
        outcomes.update(labels)
        for arm in STRATEGIES:
            for field in ("event_choices", "highlighted"):
                policy["strategies"][arm][field].extend(result["strategies"][arm][field])
        policy["metadata"] = {k: v for k, v in result.items() if k != "strategies"}
    result = aggregate(evaluation, policy, outcomes, p)
    result["forecast_metrics"] = forecast_metrics(evaluation, learned)
    after = source_hashes(games, protocol_path)
    if before != after:
        raise RuntimeError("input/code changed while running; report not published")
    return {"schema": p["schema"], "production_allowed": False, "protocol": p,
            "provenance": before, "runtime": {"python": platform.python_version(),
                "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__},
            "quality": {**quality, **score_quality, "evaluation_markets": len(evaluation),
                "evaluation_events": len({r["event"] for r in evaluation}),
                "evaluation_leagues": len({(r["sport"], r["league"]) for r in evaluation}),
                "evaluation_void_markets": sum(r["is_void"] for r in evaluation),
                "score_model_available_markets": sum(r["score_available"] for r in evaluation)},
            "policy": policy.get("metadata"), "learned": learned, "score_folds": folds, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() in (args.games.resolve(), args.protocol.resolve()):
        raise ValueError("output must be a new path")
    if not 1 <= args.workers <= 8:
        raise ValueError("workers must be 1..8")
    result = run(args.games, args.protocol, args.workers)
    with args.output.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


if __name__ == "__main__":
    main()
