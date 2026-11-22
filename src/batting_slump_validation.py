"""Offline, non-promotable KBO slump ablation. No production writes.

Archive odds and boxscore-derived starters lack pregame availability timestamps.
All profits are retrospective proxy results, not executable/validated edge.
Model/window/policy choices are fixed before inspecting held-out results.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, logit

from batter_process import load, _rates
from devig import market_probabilities
from lineup_batting import load_games, woba, ORDER_PA
from matches import clean_team
from pitcher_form import StarterForm, load_starter_boxscores

KEY = ["date", "home_team", "away_team"]
WINDOWS = (5, 10, 20)


def features(team_rows, lineup_games, pitcher_rows):
    """All state is updated AFTER the whole date: excludes same-day leakage."""
    hist, players, parks = defaultdict(list), {}, defaultdict(list)
    league = dict.fromkeys(("ab", "bb", "s", "d", "t", "hr"), 0)
    lu = {(pd.Timestamp(x["date"]), x["home"], x["away"]): x for x in lineup_games}
    sp = {(pd.Timestamp(x["date"]), x["home_team"], x["away_team"]): x for x in pitcher_rows}
    pitcher = StarterForm.from_boxscores(pitcher_rows)
    rows = []
    statkeys = ("ab", "bb", "kk", "hit", "s", "d", "t", "hr_parsed", "run")
    # Date-only identities cannot safely distinguish doubleheaders.
    team_rows = team_rows.loc[~team_rows.duplicated(KEY, keep=False)].sort_values("date")
    for day, batch in team_rows.groupby("date", sort=True):
        pending = []
        for rec in batch.to_dict("records"):
            key = tuple(rec[k] for k in KEY)
            lineup, starters = lu.get(key), sp.get(key)
            out = {k: rec[k] for k in KEY}
            out.update(home_score=rec["home_score"], away_score=rec["away_score"])
            park = rec.get("stadium")
            out["park"] = np.mean(parks[park][-100:]) if parks[park] else 9.0
            for side, prefix in (("home", "h_"), ("away", "a_")):
                team = rec[side + "_team"]
                past = hist[team]
                base = _rates(past[-40:]) if len(past) >= 20 else {}
                out[side + "_base"] = base.get("runs", np.nan)
                for n in WINDOWS:
                    rate = _rates(past[-n:]) if len(past) >= 20 else {}
                    out[f"{side}_slump{n}"] = rate.get("woba", np.nan) - base.get("woba", np.nan)
                lg = woba(league) or .32
                values, weights = [], []
                if lineup:
                    for p in lineup[side + "_lineup"]:
                        a = players.get(p["code"], dict.fromkeys(league, 0))
                        pa = a["ab"] + a["bb"]
                        values.append(((woba(a) or lg) * pa + lg * 150) / (pa + 150))
                        weights.append(ORDER_PA.get(int(p["order"]), 4.3))
                out[side + "_lineup"] = np.average(values, weights=weights) if values else np.nan
                out[side + "_pitcher"] = (
                    pitcher.xfip_by_name(starters[side + "_sp"]["name"], str(day.date()))
                    if starters else np.nan
                )
                pending.append((team, {k: int(rec[prefix + k] or 0) for k in statkeys}))
            rows.append(out)
        for team, stats in pending:
            hist[team].append(stats)
        for rec in batch.to_dict("records"):
            parks[rec.get("stadium")].append(rec["home_score"] + rec["away_score"])
            lineup = lu.get(tuple(rec[k] for k in KEY))
            if lineup:
                for side in ("home", "away"):
                    for p in lineup[side + "_all"]:
                        a = players.setdefault(p["code"], dict.fromkeys(league, 0))
                        for k in league:
                            a[k] += int(p.get(k) or 0)
                            league[k] += int(p.get(k) or 0)
    return pd.DataFrame(rows)


def markets(path):
    d = pd.read_csv(path)
    d = d.loc[d.league.eq("KBO") & d.n_way.eq(2) &
              ~d.is_void.astype(str).str.lower().isin(["true", "1"])].copy()
    md = d.date_text.str.extract(r"(\d\d)\.(\d\d).*?(\d\d:\d\d)")
    d["date"] = pd.to_datetime(d.year.astype(str) + "-" + md[0] + "-" + md[1], errors="coerce")
    d["kickoff"] = pd.to_datetime(d.date.dt.strftime("%Y-%m-%d") + " " + md[2], errors="coerce")
    d["home_team"], d["away_team"] = d.home.map(clean_team), d.away.map(clean_team)
    accepted = d.market_family.eq("승패") & d.result.isin(["홈승", "홈패"])
    ou = (d.market_family.eq("언더오버") & d.result.isin(["언더", "오버"]) &
          d.market_label.fillna("").str.fullmatch(r"U \d+\.5"))
    d = d[accepted | ou].copy()
    d["market"] = np.where(d.market_family.eq("승패"), "win", "total")
    d["y"] = d.result.isin(["홈승", "오버"]).astype(int)
    d["line"] = pd.to_numeric(d.market_label.str.extract(r"U (\d+\.5)")[0], errors="coerce").fillna(0)
    odds = d.odds.str.split(",").map(lambda a: [float(v) for v in a])
    d["o1"] = [a[0] if m == "win" else a[1] for a, m in zip(odds, d.market)]
    d["o0"] = [a[1] if m == "win" else a[0] for a, m in zip(odds, d.market)]
    d = d[(d.o1 > 1) & (d.o0 > 1)].dropna(subset=["kickoff"])
    d["p"] = [market_probabilities([a, b])[0] for a, b in zip(d.o1, d.o0)]
    # Reject date identities with multiple kickoff times, conflicts, or totals lines.
    group = KEY + ["market"]
    valid = d.groupby(group).agg(times=("kickoff", "nunique"), outcomes=("y", "nunique"),
                                lines=("line", "nunique"))
    valid = valid[(valid.times == 1) & (valid.outcomes == 1) & (valid.lines == 1)]
    d = d.merge(valid.reset_index()[group], on=group, validate="many_to_one")
    # Fixed earliest issuance, not best odds or outcome-dependent median.
    return d.sort_values(["year", "round", "game_no"]).drop_duplicates(group)


def predict(train, test, cols):
    """Regularized logistic residual on market logit; preprocessing train-only."""
    med = train[cols].median().fillna(0)
    x, z = train[cols].fillna(med).to_numpy(), test[cols].fillna(med).to_numpy()
    mean, sd = x.mean(0), x.std(0)
    sd[sd < 1e-8] = 1
    x, z = (x - mean) / sd, (z - mean) / sd
    x, z = np.c_[np.ones(len(x)), x], np.c_[np.ones(len(z)), z]
    off = logit(train.p.clip(.001, .999).to_numpy())
    y = train.y.to_numpy()
    def objective(b):
        t = off + x @ b
        return (np.logaddexp(0, t) - y * t).mean() + .02 * np.sum(b[1:] ** 2)
    fit = minimize(objective, np.zeros(x.shape[1]), method="BFGS")
    if not fit.success and np.linalg.norm(fit.jac) > 1e-4:
        raise RuntimeError(fit.message)
    return expit(logit(test.p.clip(.001, .999)) + z @ fit.x)


def simulate(rows, probability, weekly, positive_only=False, target_stop=False):
    """One 2-leg ticket/day, first 2/3 qualifying days/week; settle next date.

    Fixed 10k stake, reserve worst-case stake, monthly loss floor -50k.
    Archive outcomes have no settled_at: next-day settlement is a proxy.
    """
    d = rows.copy()
    p = d[probability].to_numpy()
    ev1, ev0 = p * d.o1.to_numpy() - 1, (1-p) * d.o0.to_numpy() - 1
    side = ev1 >= ev0
    d["ev"] = np.maximum(ev1, ev0)
    d["chosen_odds"] = np.where(side, d.o1, d.o0)
    d["won"] = np.where(side, d.y.eq(1), d.y.eq(0))
    months, week_count, tickets, stakes = {}, defaultdict(int), [], defaultdict(int)
    for day, batch in d.groupby("date", sort=True):
        month = str(day.to_period("M"))
        months.setdefault(month, 0.0)
        week = tuple(day.isocalendar()[:2])
        if week_count[week] >= weekly or months[month] - 10000 < -50000:
            continue
        if target_stop and months[month] >= 50000:
            continue
        if positive_only:
            batch = batch[batch.ev > 0]
        pair = (batch.sort_values(["ev", "home_team", "away_team"], ascending=[False, True, True])
                .drop_duplicates(["home_team", "away_team"]).head(2))
        if len(pair) < 2:
            continue
        # Decimal product, no assumed upward rounding.
        profit = 10000 * (float(pair.chosen_odds.prod()) * bool(pair.won.all()) - 1)
        months[month] += profit
        stakes[month] += 10000
        week_count[week] += 1
        tickets.append(profit)
    vals = list(months.values())
    curve = np.r_[0, np.cumsum(tickets)]
    roi_ci = None
    if tickets:
        rng = np.random.default_rng(20261020)
        ix = rng.integers(0, len(vals), size=(2000, len(vals)))
        net = np.array(vals)[ix].sum(axis=1)
        stake = np.array([stakes[k] for k in months])[ix].sum(axis=1)
        roi_ci = np.quantile(net[stake > 0] / stake[stake > 0], [.025, .975]).tolist()
    return dict(tickets=len(tickets), net=round(sum(tickets)), roi=(sum(tickets)/(10000*len(tickets)) if tickets else None),
                monthly_block_roi_ci=roi_ci,
                months=len(vals), profitable_months=sum(v > 0 for v in vals),
                target_months=sum(v >= 50000 for v in vals), worst_month=round(min(vals, default=0)),
                monthly_std=float(np.std(vals)) if vals else 0,
                max_drawdown=round(float(np.max(np.maximum.accumulate(curve)-curve))),
                monthly_net={k: round(v) for k, v in months.items()})


def run(path):
    f = features(load(), load_games(), load_starter_boxscores())
    d = markets(path).merge(f, on=KEY, validate="many_to_one")
    expected = np.where(d.market.eq("win"), d.home_score > d.away_score,
                        d.home_score + d.away_score > d.line)
    coherent = (expected == d.y) & ~(d.market.eq("win") & d.home_score.eq(d.away_score))
    conflicts = int((~coherent).sum())
    d = d[coherent]
    d = d.dropna(subset=["home_base", "away_base"])
    report = {"protocol": "fixed windows 5/10/20; expanding train before each held-out year; Shin market baseline; no promotion",
              "excluded_score_result_conflicts": conflicts,
              "odds_sha256": sha256(Path(path).read_bytes()).hexdigest(),
              "limitations": ["Odds observation timestamps absent; executable ROI unverified",
                             "Starting lineups/pitchers reconstructed from boxscores, pregame availability unverified",
                             "Same-date doubleheader identities rejected; prior dates only",
                             "2-leg independence not established; payouts use unrounded odds product",
                             "No held-out model selection; multiple comparisons exploratory"],
              "results": {}}
    for market, a in d.groupby("market"):
        a = a.copy()
        sign = -1 if market == "win" else 1
        for name in ("base", "lineup", "pitcher", "slump5", "slump10", "slump20"):
            a[name] = a["home_" + name] + sign * a["away_" + name]
        controls = ["base", "lineup", "pitcher", "park"]
        models = {"calibrated_market": [], "context": controls,
                  **{f"slump{n}": controls + [f"slump{n}"] for n in WINDOWS}}
        pieces = []
        for year in (2024, 2025, 2026):
            tr, te = a[a.date.dt.year < year], a[a.date.dt.year == year].copy()
            if len(tr) < 100 or len(te) < 30:
                continue
            te["market_only"] = te.p
            for name, cols in models.items():
                te[name] = predict(tr, te, cols)
            pieces.append(te)
        if not pieces:
            raise ValueError("No temporal folds")
        oos = pd.concat(pieces)
        mr = {"n": len(oos), "start": str(oos.date.min().date()), "end": str(oos.date.max().date()),
              "missing_context": {c: int(oos[c].isna().sum()) for c in controls}, "models": {}}
        for name in ["market_only", *models]:
            err = (oos[name] - oos.y) ** 2
            daily = (err - (oos["context"]-oos.y)**2).groupby(oos.date).mean().to_numpy()
            rng = np.random.default_rng(20261020)
            ci = np.quantile([rng.choice(daily, len(daily), replace=True).mean() for _ in range(2000)], [.025,.975])
            mr["models"][name] = {
                "brier": float(err.mean()), "accuracy": float(((oos[name] >= .5) == oos.y).mean()),
                "daily_brier_delta_vs_context_ci": ci.tolist(),
                "seasons": {str(y): {"n": len(v), "brier": float(((v[name]-v.y)**2).mean())}
                            for y,v in oos.groupby(oos.date.dt.year)},
                "simulation": {f"weekly{w}_{policy}_{target}": simulate(oos, name, w, policy=="positive_ev", target=="target_stop")
                               for w in (2,3) for policy in ("rank_all","positive_ev")
                               for target in ("no_target_stop","target_stop")}}
        for name, item in mr["models"].items():
            for policy, sim in item["simulation"].items():
                base = mr["models"]["context"]["simulation"][policy]["monthly_net"]
                delta = np.array([v-base[k] for k,v in sim["monthly_net"].items()])
                rng = np.random.default_rng(20261020)
                sim["monthly_net_delta_vs_context_ci"] = np.quantile(
                    [rng.choice(delta, len(delta), replace=True).mean() for _ in range(2000)],
                    [.025,.975]).tolist()
        report["results"][market] = mr
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--games", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = run(args.games)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({m: {"n": r["n"], "models": {k: {"brier": v["brier"], "roi_weekly2": v["simulation"]["weekly2_rank_all_no_target_stop"]["roi"]}
                                              for k,v in r["models"].items()}}
                      for m,r in result["results"].items()}, ensure_ascii=True))
