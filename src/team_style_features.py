"""Causal score-history team styles, independent within sport/league.

This is an offline experiment input, not a production prediction formula.
All matches on a calendar day see the same start-of-day state. Historical
opponent strength and scoring expectations are frozen when that game is seen.
"""
from __future__ import annotations

from collections import defaultdict
import numpy as np
import pandas as pd

from features import HOME_ADV, K_BY_SPORT, season_key
from matches import _home, _away, actual_game_year
from team_style_archive import DATE, digest

GROUPS = {
    "scoring": ["gf5", "gf10", "gf20", "ga5", "ga10", "ga20",
                "attack20", "defence20", "season_margin", "venue_margin",
                "elo", "history_n"],
    "variance": ["gf_sd20", "ga_sd20", "margin_sd20", "largest_score_share20"],
    "opponent": ["strong_residual", "weak_residual", "strength_slope",
                 "strong_n", "weak_n"],
    "matchup": ["rest", "games7", "attack_defence_interaction", "strength_interaction"],
}


def score_history(raw):
    """Only straight full-time integer scores; never handicap-adjusted scores."""
    frame = raw.fillna("").copy()
    parsed = frame.date_text.astype(str).str.extract(DATE).apply(pd.to_numeric, errors="coerce")
    years = actual_game_year(frame.year, frame["round"], parsed[0])
    times = pd.to_datetime(dict(year=years, month=parsed[0], day=parsed[1],
                               hour=parsed[2], minute=parsed[3]), errors="coerce")
    records, conflicts = {}, set()
    invalid = 0
    for r, time in zip(frame.to_dict("records"), times):
        if r["market_family"] not in ("승패", "승무패"):
            continue
        if str(r.get("market_label", "")).strip() or str(r.get("booking_class", "")) not in ("2-way", "3-way"):
            continue
        if str(r["is_void"]).lower() not in ("false", "0") or pd.isna(time):
            continue
        home, hs = _home(r["home"])
        aws, away = _away(r["away"])
        if home is None or away is None or min(hs, aws) < 0 or home == away:
            invalid += 1
            continue
        expected = "홈승" if hs > aws else "홈패" if hs < aws else "무승부"
        key = (time.isoformat(), str(r["sport"]), str(r["league"]), home, away)
        if r["result"] != expected:
            conflicts.add(key)
            continue
        old = records.get(key)
        if old and (old["home_score"], old["away_score"]) != (hs, aws):
            conflicts.add(key)
        records[key] = dict(kickoff=time, sport=str(r["sport"]), league=str(r["league"]),
                            home_team=home, away_team=away, home_score=hs, away_score=aws,
                            event=digest(key))
    clean = [r for k, r in sorted(records.items()) if k not in conflicts]
    return clean, {"unique_scored_events": len(clean), "score_conflicts": len(conflicts),
                   "invalid_straight_score_rows": invalid}


def profile(history, day, season, venue, league_mean, rating, league_rating):
    recent = [r for r in history if 0 < (day-r["day"]).days <= 365]
    last = recent[-20:]
    scale = max(league_mean, 1.) if league_mean is not None else 1.
    out = {name: None for names in GROUPS.values() for name in names}
    out.update(elo=(rating-league_rating)/200., history_n=len(last),
               strong_n=0, weak_n=0, games7=sum((day-r["day"]).days <= 7 for r in recent))
    if not last:
        return out
    out["rest"] = min((day-last[-1]["day"]).days, 30)
    # Descriptive raw units for human inspection; not additional model columns.
    out["observed_gf20"] = float(np.mean([r["gf"] for r in last]))
    out["observed_ga20"] = float(np.mean([r["ga"] for r in last]))
    for window in (5, 10, 20):
        sample = recent[-window:]
        # Missing is kept distinct from zero until a league score prior exists.
        for field in ("gf", "ga"):
            out[field+str(window)] = ((sum(r[field] for r in sample)+8*scale)
                                      /(len(sample)+8)/scale-1)
    for target, field in (("attack20", "attack_residual"), ("defence20", "defence_residual")):
        available = [r[field] for r in last if r[field] is not None]
        out[target] = sum(available)/(len(available)+8)/scale if available else None
    for name, sample in (("season_margin", [r for r in recent if r["season"] == season]),
                         ("venue_margin", [r for r in last if r["venue"] == venue])):
        out[name] = sum(r["gf"]-r["ga"] for r in sample)/(len(sample)+8)/scale if sample else None
    if len(last) >= 5:
        for name, values in (("gf_sd20", [r["gf"] for r in last]),
                             ("ga_sd20", [r["ga"] for r in last]),
                             ("margin_sd20", [r["gf"]-r["ga"] for r in last])):
            out[name] = float(np.std(values, ddof=1))/scale
        total = sum(r["gf"] for r in last)
        out["largest_score_share20"] = max(r["gf"] for r in last)/total if total > 0 else 0.
    for name, sign in (("strong", 1), ("weak", -1)):
        sample = [r for r in last if sign*r["opponent_strength"] >= .25]
        out[name+"_n"] = len(sample)
        out[name+"_residual"] = sum(r["win_residual"] for r in sample)/(len(sample)+8) if sample else None
    # Ridge slope of expectation-adjusted performance on then-known opponent strength.
    x = np.array([r["opponent_strength"] for r in last])
    y = np.array([r["win_residual"] for r in last])
    out["strength_slope"] = float(np.dot(x-x.mean(), y-y.mean())/(np.sum((x-x.mean())**2)+8))
    return out


def build_styles(matches):
    """Return event feature records and latest pregame team profiles.

Input must be unique confirmed events. Grouping guarantees no coefficients,
score priors, ratings or rolling observations are borrowed from other leagues.
"""
    if len({m["event"] for m in matches}) != len(matches):
        raise ValueError("duplicate events must be normalized before features")
    grouped = defaultdict(list)
    for r in matches:
        grouped[(r["sport"], r["league"])].append(r)
    output, snapshots = {}, {}
    for (sport, league), rows in sorted(grouped.items()):
        histories, ratings, seasons = defaultdict(list), {}, {}
        score_sum, score_n = 0., 0
        days = defaultdict(list)
        for row in sorted(rows, key=lambda r: (r["kickoff"], r["event"])):
            days[pd.Timestamp(row["kickoff"]).normalize()].append(row)
        for day, games in sorted(days.items()):
            # Regress ratings toward 1500 at this team's season transition.
            for game in games:
                for team in (game["home_team"], game["away_team"]):
                    season = season_key(league, day)
                    if team in seasons and seasons[team] != season:
                        ratings[team] = 1500+(ratings[team]-1500)*.5
                    seasons[team] = season
                    ratings.setdefault(team, 1500.)
            active = [t for t, h in histories.items() if h and (day-h[-1]["day"]).days <= 365]
            center = float(np.mean([ratings[t] for t in active])) if active else 1500.
            league_mean = score_sum/score_n if score_n else None
            pending = []
            for game in games:
                home, away = game["home_team"], game["away_team"]
                season = season_key(league, day)
                hp = profile(histories[home], day, season, "home", league_mean, ratings[home], center)
                ap = profile(histories[away], day, season, "away", league_mean, ratings[away], center)
                for own, opp in ((hp, ap), (ap, hp)):
                    own["attack_defence_interaction"] = (own["attack20"]*opp["defence20"]
                        if own["attack20"] is not None and opp["defence20"] is not None else None)
                    own["strength_interaction"] = (own["strength_slope"]*opp["elo"]
                        if own["strength_slope"] is not None else None)
                features = {prefix+name: value for prefix, p in (("home_", hp), ("away_", ap))
                            for name, value in p.items()}
                output[game["event"]] = features
                for team, p in ((home, hp), (away, ap)):
                    snapshots[(sport, league, team)] = {"sport": sport, "league": league, "team": team,
                        "as_of": day.date().isoformat(), "history_last_day":
                        histories[team][-1]["day"].date().isoformat() if histories[team] else None,
                        "league_points_per_team": league_mean, **p}
                exp_win = 1/(1+10**(-(ratings[home]-ratings[away]+HOME_ADV)/400))
                def expected(p, other):
                    if league_mean is None:
                        return None
                    # Score-only expectation, not xG or a possession/lineup model.
                    return max(0., league_mean*(1+(p["gf20"] or 0)+(other["ga20"] or 0)))
                pending.append((game, exp_win, expected(hp, ap), expected(ap, hp), center))
            # No result, including earlier doubleheaders, is visible to today's features.
            deltas = defaultdict(float)
            for game, exp_win, eh, ea, old_center in pending:
                hs, aws = game["home_score"], game["away_score"]
                win = 1. if hs > aws else 0. if hs < aws else .5
                h, a = game["home_team"], game["away_team"]
                for team, opp, gf, ga, ew, y, ef, eg, venue in (
                    (h, a, hs, aws, exp_win, win, eh, ea, "home"),
                    (a, h, aws, hs, 1-exp_win, 1-win, ea, eh, "away")):
                    histories[team].append({"day": day, "season": season_key(league, day),
                        "venue": venue, "gf": gf, "ga": ga,
                        "attack_residual": gf-ef if ef is not None else None,
                        "defence_residual": ga-eg if eg is not None else None,
                        "win_residual": y-ew, "opponent_strength": (ratings[opp]-old_center)/200})
                delta = K_BY_SPORT.get(sport, 16.)*(win-exp_win)
                deltas[h] += delta
                deltas[a] -= delta
                score_sum += hs+aws
                score_n += 2
            for team, delta in deltas.items():
                ratings[team] += delta
    return output, list(snapshots.values())


def feature_columns(groups):
    return [prefix+name for group in groups for name in GROUPS[group]
            if (name != "strength_interaction" or {"opponent", "scoring"} <= set(groups))
            and (name != "attack_defence_interaction" or "scoring" in groups)
            for prefix in ("home_", "away_")]
