"""Independent causal-window, source-profile and arithmetic audit; never fit a new candidate."""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path

EPS = 1e-6
MODELS = {"market", "prior_outcome_form", "nonspatial", "spatial", "spatial_residual"}


def read(path):
    blob = path.read_bytes()
    raw = gzip.decompress(blob) if path.suffix == ".gz" else blob
    return [json.loads(s) for s in raw.decode("utf8").splitlines() if s.strip()], hashlib.sha256(blob).hexdigest()


def index(rows, key="id"):
    result = {}
    for row in rows:
        if row[key] in result:
            raise ValueError("duplicate_identity")
        result[row[key]] = row
    return result


def zone(points, x, y):
    offset = 0 if points == 2 else 5
    if type(x) not in (int, float) or type(y) not in (int, float) or not math.isfinite(x) or not math.isfinite(y) or max(abs(x), abs(y)) > 1000:
        return offset+(4 if points == 2 else 3)
    radius = math.hypot(x-25, y)
    return offset+sum(radius >= edge for edge in ((4, 10, 16) if points == 2 else (24, 27)))


def verify_profiles(profiles, quality, cache):
    groups = defaultdict(list)
    for row in quality:
        groups[row["game_id"]].append(row)
    eligible = {identifier for identifier, rows in groups.items() if len(rows) == 2 and all(r["eligible"] for r in rows)}
    if len(eligible) != 1185 or set(index(profiles, "game_id")) != eligible:
        raise ValueError("source_pool_changed")
    field_attempts = 0
    for row in profiles:
        path = cache/"summary"/(row["game_id"]+".json")
        raw = path.read_bytes()
        receipt = json.loads(path.with_suffix(".receipt.json").read_text(encoding="utf8"))
        digest = hashlib.sha256(raw).hexdigest()
        if receipt["status"] != 200 or digest != receipt["sha256"] or digest != row["source_summary_sha256"]:
            raise ValueError("source_receipt_mismatch")
        data = json.loads(raw)
        comp = data["header"]["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        teams = [sides[s]["team"]["abbreviation"] for s in ("home", "away")]
        source_day = datetime.fromisoformat(comp["date"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=9))).date().isoformat()
        if str(data["header"]["id"]) != row["game_id"] or comp["date"] != row["date_utc"] or teams != row["teams"] or source_day != row["day"]:
            raise ValueError("source_fixture_mismatch")
        team_index = {str(sides[s]["id"]): i for i, s in enumerate(("home", "away"))}
        counts = [dict(attempts=[0]*9, makes=[0]*9, fta=0, ftm=0) for _ in range(2)]
        play_ids = set()
        for p in data["plays"]:
            if not p.get("id") or p["id"] in play_ids:
                raise ValueError("source_play_identity")
            play_ids.add(p["id"])
            if not p.get("shootingPlay"):
                continue
            points, made = p.get("pointsAttempted"), p.get("scoringPlay")
            if points not in (1, 2, 3) or type(made) is not bool or p.get("scoreValue") != points*made:
                raise ValueError("source_shooting_semantics")
            counter = counts[team_index[str(p["team"]["id"])]]
            if points == 1:
                counter["fta"] += 1
                counter["ftm"] += made
            else:
                coords = p.get("coordinate") or {}
                k = zone(points, coords.get("x"), coords.get("y"))
                counter["attempts"][k] += 1
                counter["makes"][k] += made
                field_attempts += 1
        boxes = {b["team"]["abbreviation"]: {s["name"]: s["displayValue"] for s in b["statistics"]} for b in data["boxscore"]["teams"]}
        for i, (team, stored) in enumerate(zip(teams, row["sides"])):
            if any(stored[key] != value for key, value in counts[i].items()):
                raise ValueError("spatial_count_mismatch")
            a, m, box = stored["attempts"], stored["makes"], boxes[team]
            fgm, fga = map(int, box["fieldGoalsMade-fieldGoalsAttempted"].split("-"))
            tm, ta = map(int, box["threePointFieldGoalsMade-threePointFieldGoalsAttempted"].split("-"))
            fm, fa = map(int, box["freeThrowsMade-freeThrowsAttempted"].split("-"))
            if [sum(a), sum(m), sum(a[5:]), sum(m[5:]), stored["fta"], stored["ftm"]] != [fga, fgm, ta, tm, fa, fm]:
                raise ValueError("box_count_mismatch")
            orb, tov = int(box["offensiveRebounds"]), int(box["totalTurnovers"])
            if stored["orb"] != orb or stored["tov"] != tov or not math.isfinite(stored["estimated_possessions"]) or abs(stored["estimated_possessions"]-(fga-orb+tov+.44*fa)) > 1e-9:
                raise ValueError("exposure_mismatch")
            if stored["points"] != fgm*2+tm+fm or stored["points"] != int(sides[("home", "away")[i]]["score"]):
                raise ValueError("source_score_mismatch")
    return dict(verified_games=len(profiles), verified_team_games=2*len(profiles), field_attempts_recounted=field_attempts,
                official_zone_certification=False, geometry="unofficial provider radius hypot(x-25,y)")


def sums(sides):
    vectors = [[s["estimated_possessions"]]+s["attempts"]+s["makes"]+[s["fta"], s["ftm"]] for s in sides]
    return [sum(values) for values in zip(*vectors)]


def clip(p):
    return min(1-EPS, max(EPS, p))


def logodds(p):
    p = clip(p)
    return math.log(p/(1-p))


def sigmoid(x):
    return 1/(1+math.exp(-x))


def make_rates(own, defense, league, spatial):
    if spatial:
        families = ((0, 1, 2, 3, 4), (5, 6, 7, 8))
        n = 9
    else:
        def pool(x):
            return [x[0], sum(x[1:6]), sum(x[6:10]), sum(x[10:15]), sum(x[15:19]), x[19], x[20]]
        own, defense, league = map(pool, (own, defense, league))
        families, n = ((0,), (1,)), 2
    family_makes = []
    for family in families:
        total_attempts = sum(league[1+i] for i in family)
        makes = 0.
        for i in family:
            la, lm = league[1+i], league[1+n+i]
            if not la:
                continue
            alpha, prior_rate = 200*la/total_attempts, la/league[0]
            ao, ad = own[1+i], defense[1+i]
            po = clip((own[1+n+i]+alpha*clip(lm/la))/(ao+alpha))
            pd = clip((defense[1+n+i]+alpha*clip(lm/la))/(ad+alpha))
            probability = clip(sigmoid(logodds(po)+logodds(pd)-logodds(lm/la)))
            rate = ((ao+500*prior_rate)/(own[0]+500))*((ad+500*prior_rate)/(defense[0]+500))/prior_rate
            makes += min(1.5, max(0., rate))*probability
        family_makes.append(makes)
    prior_rate = league[-2]/league[0]
    po = (own[-1]+200*clip(league[-1]/league[-2]))/(own[-2]+200)
    pd = (defense[-1]+200*clip(league[-1]/league[-2]))/(defense[-2]+200)
    rate = ((own[-2]+500*prior_rate)/(own[0]+500))*((defense[-2]+500*prior_rate)/(defense[0]+500))/prior_rate
    free = min(1.5, max(0., rate))*clip(sigmoid(logodds(po)+logodds(pd)-logodds(league[-1]/league[-2])))
    return family_makes+[free]


def score_law(rates, exposure):
    lambdas = {2: exposure*rates[0], 3: exposure*rates[1], 1: exposure*rates[2]}
    p = [math.exp(-sum(lambdas.values()))]
    for k in range(1, 601):
        p.append(sum(j*lam*p[k-j] for j, lam in lambdas.items() if j <= k)/k)
    if abs(sum(p)-1) > 1e-11:
        raise ValueError("compound_score_tail")
    return p


def features(profiles, day, teams):
    d = date.fromisoformat(day)
    active = [g for g in profiles if d-timedelta(days=365) <= date.fromisoformat(g["day"]) <= d-timedelta(days=4)]
    league = sums([s for g in active for s in g["sides"]])
    histories, own, defense, exposure = [], [], [], []
    for team in teams:
        historical = [g for g in active if team in g["teams"]]
        if len(historical) < 5:
            raise ValueError("insufficient_history")
        os, ds = [], []
        for g in historical:
            i = g["teams"].index(team)
            os.append(g["sides"][i])
            ds.append(g["sides"][1-i])
        o, a = sums(os), sums(ds)
        own.append(o); defense.append(a)
        exposure.append((o[0]+a[0])/(2*len(historical)))
        histories.append([g["game_id"] for g in historical])
    shared = math.sqrt(exposure[0]*exposure[1])
    result = dict(history_game_ids=histories, asof_prior_games=len(active))
    for name, spatial in (("nonspatial", False), ("spatial", True)):
        rates = [make_rates(own[i], defense[1-i], league, spatial) for i in range(2)]
        h, a = [score_law(r, shared) for r in rates]
        cumulative = probability = 0.
        for hp, ap in zip(h, a):
            probability += hp*(cumulative+.5*ap)
            cumulative += ap
        result[name] = probability
    result["feature_delta"] = logodds(result["spatial"])-logodds(result["nonspatial"])
    return result


def metrics(rows, name):
    daily = defaultdict(Decimal)
    hits = ll = brier = odds_sum = 0.
    for row in rows:
        p, y = row["probabilities"][name], row["home_win"]
        side = 0 if p >= .5 else 1
        hit = side == (0 if y else 1)
        price = row["odds"][side]
        daily[row["day"]] += Decimal(str(price))-1 if hit else Decimal(-1)
        hits += hit; odds_sum += price
        ll -= math.log(clip(p) if y else 1-clip(p))
        brier += (clip(p)-y)**2
    equity = peak = mdd = Decimal(0)
    for day in sorted(daily):
        equity += daily[day]; peak = max(peak, equity); mdd = max(mdd, peak-equity)
    return dict(n=len(rows), hits=int(hits), accuracy=hits/len(rows), roi=float(equity/len(rows)),
                net_units=str(equity), logloss=ll/len(rows), brier=brier/len(rows), mean_odds=odds_sum/len(rows), daily_mdd_units=str(mdd))


def beta_root(rows):
    def gradient(beta):
        return sum(r["feature_delta"]*(sigmoid(logodds(r["market"])+beta*r["feature_delta"])-r["home_win"]) for r in rows)+10*beta
    lo, hi = -10., 10.
    if gradient(lo) >= 0: return lo
    if gradient(hi) <= 0: return hi
    for _ in range(100):
        mid = (lo+hi)/2
        if gradient(mid) > 0: hi = mid
        else: lo = mid
    return (lo+hi)/2


def audit(rows, training, profiles, baseline, prices, manifest):
    old, quoted, source = index(baseline), index([p for p in prices if p["league"] == "NBA"]), index(profiles, "game_id")
    if set(index(rows)) != set(old): raise ValueError("primary_cohort_changed")
    cache, errors = {}, []
    def check_feature(row):
        key = (row["day"], tuple(row["teams"]))
        if key not in cache: cache[key] = features(profiles, row["day"], row["teams"])
        expected = cache[key]
        if len(row["history_game_ids"]) != 2 or any(set(a) != set(b) or len(a) != len(b) for a, b in zip(expected["history_game_ids"], row["history_game_ids"])):
            raise ValueError("causal_history_mismatch")
        if row["asof_prior_games"] != expected["asof_prior_games"]:
            raise ValueError("global_prior_window_mismatch")
        for name in ("spatial", "nonspatial", "feature_delta"):
            actual = row["probabilities"][name] if name in MODELS and "probabilities" in row else row[name]
            if type(actual) not in (int, float) or not math.isfinite(actual):
                raise ValueError("invalid_feature_probability")
            error = abs(expected[name]-actual)
            errors.append(error)
            if error > 2e-10: raise ValueError("independent_spatial_probability_mismatch")
    def check_label(row):
        q = quoted[row["id"]]
        if len(q["odds"]) != 2 or any(type(o) not in (int, float) or not math.isfinite(o) or o <= 1 for o in q["odds"]):
            raise ValueError("invalid_price")
        if "odds" in row and row["odds"] != q["odds"]:
            raise ValueError("original_quote_changed")
        if row["day"] != q["day"] or row["home_win"] != int(q["y"] == 0): raise ValueError("price_label_mismatch")
        return (1/q["odds"][0])/(1/q["odds"][0]+1/q["odds"][1])
    states = manifest["states"]
    for row in training:
        cutoff = {"A": "2025-08-28", "B": "2025-12-28"}[row["beta_state_id"]]
        if not "2025-01-01" <= row["day"] <= cutoff: raise ValueError("training_after_cutoff")
        if abs(check_label(row)-row["market"]) > 1e-12: raise ValueError("training_market_mismatch")
        check_feature(row)
    beta_check = {}
    for phase in ("A", "B"):
        selected = [r for r in training if r["beta_state_id"] == phase]
        if not selected: raise ValueError("no_training_rows")
        index(selected)
        beta = beta_root(selected)
        if len(selected) != states[phase]["n"] or abs(beta-states[phase]["beta"]) > 2e-6: raise ValueError("coefficient_mismatch")
        beta_check[phase] = dict(n=len(selected), independent_beta=beta, saved_beta=states[phase]["beta"])
    for row in rows:
        prior = old[row["id"]]
        if any(row[k] != prior[k] for k in ("espn_id", "day", "teams", "scores", "odds", "home_win")): raise ValueError("frozen_comparison_changed")
        p = row["probabilities"]
        if set(p) != MODELS or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in p.values()): raise ValueError("invalid_model_probability")
        if abs(check_label(row)-p["market"]) > 1e-12 or abs(p["market"]-prior["market_home"]) > 1e-12 or abs(p["prior_outcome_form"]-prior["existing_outcome_form"]) > 1e-12: raise ValueError("frozen_probability_changed")
        phase = "A" if row["day"] < "2026-01-01" else "B"
        if row["period"] != phase or row["beta_state_id"] != phase: raise ValueError("period_mismatch")
        check_feature(row)
        expected = sigmoid(logodds(p["market"])+states[phase]["beta"]*row["feature_delta"])
        if abs(expected-p["spatial_residual"]) > 1e-12: raise ValueError("anchored_probability_mismatch")
        if row["posthoc_target_in_source_pool"] != (row["espn_id"] in source): raise ValueError("posthoc_subset_mismatch")
    groups = {}
    for phase in ("A", "B"):
        selected = [r for r in rows if r["period"] == phase]
        posthoc = [r for r in selected if r["posthoc_target_in_source_pool"]]
        groups[phase] = dict(n=len(selected), actual_dates=[min(r["day"] for r in selected), max(r["day"] for r in selected)],
            models={m: metrics(selected, m) for m in sorted(MODELS)},
            monthly={month: {m: metrics([r for r in selected if r["day"].startswith(month)], m) for m in sorted(MODELS)} for month in sorted({r["day"][:7] for r in selected})},
            descriptive_target_pool_subset=dict(n=len(posthoc), models={m: metrics(posthoc, m) for m in sorted(MODELS)}),
            changed_from_market={m: sum((r["probabilities"][m] >= .5) != (r["probabilities"]["market"] >= .5) for r in selected) for m in sorted(MODELS)})
    return dict(n=len(rows), training_rows=len(training), independently_recomputed_feature_keys=len(cache),
                max_feature_or_probability_error=max(errors), beta_check=beta_check, groups=groups,
                limits=["Independent mathematical, temporal and arithmetic audit; no new candidate fitted or selected",
                        "Source-time lag and retrospective price matching do not certify real historical execution",
                        "Targets are not filtered by their own post-game shot quality in the primary comparison"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("profiles", "quality", "cache", "predictions", "training", "baseline", "prices", "manifest", "output"):
        p.add_argument("--"+name, required=True, type=Path)
    a = p.parse_args()
    profiles, ph = read(a.profiles); quality, qh = read(a.quality)
    rows, rh = read(a.predictions); training, th = read(a.training)
    baseline, bh = read(a.baseline); prices, oh = read(a.prices)
    profiles.sort(key=lambda g: (g["day"], g["date_utc"], g["game_id"]))
    source_result = verify_profiles(profiles, quality, a.cache)
    result = audit(rows, training, profiles, baseline, prices, json.loads(a.manifest.read_text(encoding="utf8")))
    result["source_verification"] = source_result
    result["sha256"] = dict(profiles=ph, quality=qh, predictions=rh, training=th, baseline=bh, prices=oh)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf8")
    print(json.dumps({k: result[k] for k in ("n", "training_rows", "max_feature_or_probability_error", "beta_check")}))
