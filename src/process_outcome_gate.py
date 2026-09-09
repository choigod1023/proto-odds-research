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


def evaluate(rows, node):
    train = [r for r in rows if r["kickoff"][:10] <= "2024-12-30"]
    test = [r for r in rows if r["kickoff"][:4] >= "2025"]
    if len(train) < 200 or len(test) < 100:
        raise ValueError("insufficient outcome sample")
    candidate = fit_predict(train, test)
    # Isolate generic market calibration from the incremental process information.
    calibrated = fit_predict([{**r, "features": [0, 0]} for r in train],
                             [{**r, "features": [0, 0]} for r in test])
    probs = {"market": market(test), "market_calibration": calibrated, "process": candidate}
    report = {"train_games": len(train), "test_games": len(test), "production_allowed": False,
              "scope": "K1 winner-market subset; retrospective exploratory, NOT full-site replay",
              "odds_capture_verified": False, "feature_publication_verified": False,
              "fixed_model": "L2=32 market offset; train-only scaling; no alpha search",
              "groups": {}}
    for year in ["all"] + sorted({r["kickoff"][:4] for r in test}):
        mask = np.array([year == "all" or r["kickoff"].startswith(year) for r in test])
        subset = [r for r, keep in zip(test, mask) if keep]
        output = {"probabilities": {name: metrics(subset, p[mask]) for name, p in probs.items()}}
        output["paired_market"] = paired_interval(subset, probs["market"][mask], candidate[mask])
        output["paired_calibration"] = paired_interval(subset, calibrated[mask], candidate[mask])
        policies = {name: replay(subset, p[mask], node) for name, p in probs.items()}
        output["policy"] = {name: {k:v for k,v in value.items() if k != "picks"} for name,value in policies.items()}
        output["matched_policy"] = matched_policy(policies["market"]["picks"], policies["process"]["picks"])
        report["groups"][year] = output
    return report, {"test_rows": test, "probabilities": {k:v.tolist() for k,v in probs.items()}}


def run(source, controls, output, node):
    if hashlib.sha256(source.read_bytes()).hexdigest() != AUDITED_SHA256:
        raise ValueError("Unaudited shots snapshot")
    output.mkdir(parents=True, exist_ok=False)
    meta = {"shots_sha256": AUDITED_SHA256, "controls_sha256": hashlib.sha256(controls.read_bytes()).hexdigest(),
            "code_hashes": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                            for name in ("process_outcome_gate.py", "dynamic_count_gate.py", "soccer_process_gate.py", "league_context_validation.py")}}
    with closing(sqlite3.connect(output/"result.sqlite3")) as db, db:
        db.execute("CREATE TABLE run (metadata TEXT,status TEXT,report TEXT,data TEXT)")
        db.execute("INSERT INTO run VALUES (?, 'running',NULL,NULL)", (json.dumps(meta),))
    try:
        rows, audit = build_rows(json.loads(source.read_bytes()), json.loads(controls.read_bytes()))
        report, predictions = evaluate(rows, node)
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
    a = p.parse_args()
    print(json.dumps(run(a.source, a.controls, a.output, a.node)))
