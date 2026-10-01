"""Past-only generated count features -> market-offset outcome and real policy gate."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
from scipy.special import expit
from scipy.optimize import minimize
from dynamic_count_gate import forecast
from soccer_process_gate import AUDITED_SHA256, load_games
from league_context_validation import market, fit_predict, metrics, paired_interval, replay, matched_policy

ALIASES = {"울산HDFC": "울산", "수원삼성": "수원", "FC서울": "서울", "대구FC": "대구",
           "포항스틸": "포항", "광주FC": "광주", "수원FC": "수원FC", "대전하나": "대전",
           "전북현대": "전북", "인천유나": "인천", "강원FC": "강원", "제주SKFC": "제주",
           "김천상무": "김천", "FC안양": "안양", "부천FC": "부천"}


def build_rows(raw, controls):
    games, _ = load_games(raw)
    lookup = {}
    audit = Counter()
    for row in controls:
        if row["league"] != "K리그1":
            continue
        _, home, away, stamp = row["event_id"].split("|")
        kickoff = datetime.fromisoformat(row["kickoff"])
        if kickoff.tzinfo is None or stamp != row["kickoff"] or len(row["odds"]) != 3:
            raise ValueError("invalid market identity")
        if home not in ALIASES or away not in ALIASES:
            audit["unmapped_team"] += 1
            continue
        key = (kickoff.astimezone(timezone(timedelta(hours=9))).date(), ALIASES[home], ALIASES[away])
        if key in lookup:
            raise ValueError("duplicate market fixture")
        lookup[key] = row
    result = []
    for day in sorted({g["day"] for g in games}):
        batch = [g for g in games if g["day"] == day]
        try:
            predicted, count = forecast(games, batch, day, "dynamic")
        except ValueError as exc:
            if str(exc) != "insufficient history":
                raise
            audit["warmup_games"] += len(batch)
            continue
        for j, game in enumerate(batch):
            key = (day, *game["teams"])
            if key not in lookup:
                audit["no_market_match"] += 1
                continue
            row = lookup[key]
            hs, aw = raw[game["id"]]["home_score"], raw[game["id"]]["away_score"]
            target = 0 if hs > aw else 1 if hs == aw else 2
            if target != row["target"]:
                audit["settlement_conflict"] += 1
                continue
            result.append({**row, "features": np.log(predicted[2*j:2*j+2]).tolist(),
                           "process_training_games": count, "process_game_id": game["id"],
                           "process_latest_allowed_date": str(day-timedelta(days=2))})
    return result, dict(audit)


def fit_favorite(train, test):
    def matrix(rows):
        mp = market(rows); favorite = mp.argmax(axis=1)
        counts = np.array([r['features'] for r in rows])
        difference = counts[:,0]-counts[:,1]
        aligned = np.where(favorite == 0, difference, np.where(favorite == 2, -difference, -abs(difference)))
        return mp, favorite, np.column_stack([aligned,counts.sum(axis=1)])
    mp, favorite, x = matrix(train)
    mt, ft, xt = matrix(test)
    mean,scale = x.mean(axis=0),x.std(axis=0)
    scale[scale < 1e-8] = 1
    x = np.column_stack([np.ones(len(x)),(x-mean)/scale])
    xt = np.column_stack([np.ones(len(xt)),(xt-mean)/scale])
    prior = mp[np.arange(len(mp)),favorite]
    off = np.log(prior/(1-prior))
    y = np.array([r['target'] for r in train]) == favorite
    def loss(beta):
        eta = off+x@beta
        value = np.sum(np.logaddexp(0,eta)-y*eta)+16*np.sum(beta[1:]**2)
        gradient = x.T@(expit(eta)-y); gradient[1:] +=32*beta[1:]
        return value,gradient
    fit = minimize(loss,np.zeros(x.shape[1]),jac=True,method='L-BFGS-B',options={'maxiter':500})
    if not fit.success:
        raise ValueError('favorite optimizer failed')
    prior_test = mt[np.arange(len(mt)),ft]
    probability = expit(np.log(prior_test/(1-prior_test))+xt@fit.x)
    out = mt*((1-probability)/(1-prior_test))[:,None]
    out[np.arange(len(mt)),ft] = probability
    return out


def rolling_probabilities(rows, test, predictor=fit_predict):
    candidate = np.zeros((len(test),3)); calibrated = np.zeros_like(candidate)
    audits = []
    for day in sorted({r['kickoff'][:10] for r in test}):
        stamp = datetime.fromisoformat(day)
        low, high = str((stamp-timedelta(days=365)).date()), str((stamp-timedelta(days=2)).date())
        past = [r for r in rows if low <= r['kickoff'][:10] <= high]
        if len(past) < 150:
            raise ValueError('insufficient rolling outcome history')
        mask = np.array([r['kickoff'].startswith(day) for r in test])
        today = [r for r, keep in zip(test,mask) if keep]
        candidate[mask] = predictor(past,today)
        calibrated[mask] = predictor([{**r,'features':[0,0]} for r in past], [{**r,'features':[0,0]} for r in today])
        audits.append({'day':day,'train_games':len(past),'latest_allowed':high})
    return candidate, calibrated, audits


def evaluate(rows, node, rolling=False, favorite=False):
    train = [r for r in rows if r["kickoff"][:10] <= "2024-12-30"]
    test = [r for r in rows if r["kickoff"][:4] >= "2025"]
    if len(train) < 200 or len(test) < 100:
        raise ValueError("insufficient outcome sample")
    predictor = fit_favorite if favorite else fit_predict
    candidate = predictor(train, test)
    # Isolate generic market calibration from the incremental process information.
    calibrated = predictor([{**r, "features": [0, 0]} for r in train],
                             [{**r, "features": [0, 0]} for r in test])
    audits = []
    if rolling:
        candidate,calibrated,audits = rolling_probabilities(rows,test,predictor)
    probs = {"market": market(test), "market_calibration": calibrated, "process": candidate}
    report = {"train_games": len(train), "test_games": len(test), "production_allowed": False,
              "scope": "K1 winner-market subset; retrospective exploratory, NOT full-site replay",
              "odds_capture_verified": False, "feature_publication_verified": False,
              "fixed_model": "L2=32 market offset; train-only scaling; no alpha search",
              "groups": {}}
    report['training_mode'] = 'past365days_daily_refit' if rolling else 'frozen_through2024'
    report['daily_training_audit'] = audits
    report['learning_target'] = 'market_favorite_correctness' if favorite else 'three_way_outcome'
    for year in ["all"] + sorted({r["kickoff"][:4] for r in test}):
        mask = np.array([year == "all" or r["kickoff"].startswith(year) for r in test])
        subset = [r for r, keep in zip(test, mask) if keep]
        output = {"probabilities": {name: metrics(subset, p[mask]) for name, p in probs.items()}}
        output["paired_market"] = paired_interval(subset, probs["market"][mask], candidate[mask])
        output["paired_calibration"] = paired_interval(subset, calibrated[mask], candidate[mask])
        policies = {name: replay(subset, p[mask], node) for name, p in probs.items()}
        output["policy"] = {name: {k:v for k,v in value.items() if k != "picks"} for name,value in policies.items()}
        output["matched_policy"] = matched_policy(policies["market"]["picks"], policies["process"]["picks"])
        output['matched_calibration_policy'] = matched_policy(policies['market']['picks'],policies['market_calibration']['picks'])
        fav = probs['market'][mask].argmax(axis=1)
        y = np.array([r['target'] for r in subset]) == fav
        output['favorite_binary_brier'] = {name: float(np.mean((p[mask][np.arange(len(subset)),fav]-y)**2)) for name,p in probs.items()}
        report["groups"][year] = output
    return report, {"test_rows": test, "probabilities": {k:v.tolist() for k,v in probs.items()}}


def run(source, controls, output, node, rolling=False, favorite=False):
    if hashlib.sha256(source.read_bytes()).hexdigest() != AUDITED_SHA256:
        raise ValueError("Unaudited shots snapshot")
    output.mkdir(parents=True, exist_ok=False)
    meta = {"rolling": rolling, "favorite": favorite, "shots_sha256": AUDITED_SHA256, "controls_sha256": hashlib.sha256(controls.read_bytes()).hexdigest(),
            "code_hashes": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                            for name in ("process_outcome_gate.py", "dynamic_count_gate.py", "soccer_process_gate.py", "league_context_validation.py")}}
    with closing(sqlite3.connect(output/"result.sqlite3")) as db, db:
        db.execute("CREATE TABLE run (metadata TEXT,status TEXT,report TEXT,data TEXT)")
        db.execute("INSERT INTO run VALUES (?, 'running',NULL,NULL)", (json.dumps(meta),))
    try:
        rows, audit = build_rows(json.loads(source.read_bytes()), json.loads(controls.read_bytes()))
        report, predictions = evaluate(rows, node, rolling, favorite)
        report["join_audit"] = audit
        (output/"report.md").write_text("# K1 process to outcome gate\n\nExploratory only. Archived odds capture times unknown.\n\n```json\n"+json.dumps({**meta, **report}, indent=2)+"\n```\n", encoding="utf-8")
        with closing(sqlite3.connect(output/"result.sqlite3")) as db, db:
            db.execute("UPDATE run SET status='complete',report=?,data=?", (json.dumps(report),json.dumps({"rows": rows, **predictions})))
    except Exception as exc:
        with closing(sqlite3.connect(output/"result.sqlite3")) as db, db:
            db.execute("UPDATE run SET status='failed',report=?", (json.dumps({"error":str(exc)}),))
        raise
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--controls", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--node", default="node")
    p.add_argument("--rolling", action="store_true")
    p.add_argument("--favorite", action="store_true")
    a = p.parse_args()
    result = run(a.source, a.controls, a.output, a.node, a.rolling, a.favorite)
    print(json.dumps({k:v for k,v in result.items() if k != 'daily_training_audit'}))
