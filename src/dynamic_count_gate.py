"""Fixed, date-refit count experiments; offline and exploratory, never production."""
import argparse
from contextlib import closing
from datetime import date, timedelta, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
from scipy.optimize import minimize
from soccer_process_gate import AUDITED_SHA256, load_games, examples, interval_by_day

CONFIG = {"lookback_days": 365, "half_life_days": 90, "lag_days": 2,
          "penalty": .1, "maxiter": 300, "test_from": "2025-01-01",
          "target": "shots", "primary_loss": "MSE"}


def eligible(games, day):
    return [g for g in games if day-timedelta(days=CONFIG["lookback_days"]) <= g["day"]
            <= day-timedelta(days=CONFIG["lag_days"])]


def design(games, teams):
    matrix = np.zeros((2*len(games), 2+2*len(teams)))
    for j, g in enumerate(games):
        for side in (0, 1):
            row = 2*j+side
            matrix[row, :2] = [1, int(side == 0)]
            if g["teams"][side] in teams:
                matrix[row, 2+teams[g["teams"][side]]] = 1
            if g["teams"][1-side] in teams:
                matrix[row, 2+len(teams)+teams[g["teams"][1-side]]] = 1
    return matrix


def objective(beta, x, y, weight):
    eta = x@beta
    mu = np.exp(eta)
    loss = np.sum(weight*(mu-y*eta)) + CONFIG["penalty"]*np.sum(beta[2:]**2)
    grad = x.T@(weight*(mu-y))
    grad[2:] += 2*CONFIG["penalty"]*beta[2:]
    return float(loss), grad


def forecast(games, targets, day, mode):
    past = eligible(games, day)
    if len(past) < 50:
        raise ValueError("insufficient history")
    teams = {team: i for i, team in enumerate(sorted({t for g in past for t in g["teams"]}))}
    y = np.array([g["counts"][side][0] for g in past for side in (0, 1)])
    weights = np.repeat([2**(-(day-g["day"]).days/CONFIG["half_life_days"]) for g in past], 2)
    weights /= weights.sum()
    if mode == "venue_shrink":
        # Distinct hypothesis: shrink noisy team form to a league/venue mean.
        # A fixed ten pseudo-games; do not tune using these test outcomes.
        mean = float(np.sum(y*weights))
        league_venue = [float(np.average(y[side::2], weights=weights[side::2])) for side in (0, 1)]
        values = []
        for g in targets:
            for side in (0, 1):
                own = [p["counts"][s][0] for p in past for s in (0, 1) if p["teams"][s] == g["teams"][side]][-10:]
                opp = [p["counts"][1-s][0] for p in past for s in (0, 1) if p["teams"][s] == g["teams"][1-side]][-10:]
                own_mean = (sum(own)+10*mean)/(len(own)+10)
                opp_mean = (sum(opp)+10*mean)/(len(opp)+10)
                values.append((own_mean+opp_mean)/2*league_venue[side]/max(mean, .1))
        return np.array(values), len(past)
    x = design(past, teams)
    beta = np.zeros(x.shape[1]); beta[0] = np.log(max(np.average(y, weights=weights), .1))
    fit = minimize(objective, beta, args=(x, y, weights), jac=True, method="L-BFGS-B",
                   bounds=[(-5, 5)]*len(beta), options={"maxiter": CONFIG["maxiter"]})
    if not fit.success or not np.isfinite(fit.fun):
        raise ValueError("optimizer failure: " + str(fit.message))
    return np.exp(design(targets, teams)@fit.x), len(past)


def evaluate(games, mode):
    base = {(r["id"], r["side"]): r for r in examples(games)}
    targets = [g for g in games if g["day"] >= date.fromisoformat(CONFIG["test_from"])
               and (g["id"], 0) in base]
    predictions, failures = [], []
    for day in sorted({g["day"] for g in targets}):
        batch = [g for g in targets if g["day"] == day]
        try:
            values, n = forecast(games, batch, day, mode)
        except ValueError as exc:
            failures.append({"day": str(day), "games": len(batch), "error": str(exc)})
            continue
        for j, g in enumerate(batch):
            for side in (0, 1):
                row = base[g["id"], side]
                predictions.append({"id": g["id"], "day": str(day), "side": side,
                    "actual": g["counts"][side][0], "candidate": float(values[2*j+side]),
                    "baseline": float(row["baseline"][0]), "opponent": float(row["opponent"][0]),
                    "training_games": n})
    if not predictions:
        return {"status": "insufficient_sample", "failures": failures, "production_allowed": False}, []
    report = {"status": "exploratory_only", "production_allowed": False, "mode": mode,
              "games": len(predictions)//2, "dates": len({r["day"] for r in predictions}),
              "failures": failures, "groups": {}}
    for group in ["all"] + sorted({r["day"][:4] for r in predictions}):
        rows = [r for r in predictions if group == "all" or r["day"].startswith(group)]
        actual = np.array([r["actual"] for r in rows])
        losses = {key: (actual-np.array([r[key] for r in rows]))**2 for key in ("candidate", "baseline", "opponent")}
        metrics = {"games": len(rows)//2, "mse": {k: float(v.mean()) for k, v in losses.items()},
                   "mae": {k: float(np.mean(abs(actual-np.array([r[k] for r in rows])))) for k in losses}}
        metrics["comparisons"] = {key: {"mse_gain": float(np.mean(losses[key]-losses["candidate"])),
            "descriptive_99pct_day_ci": interval_by_day(losses[key]-losses["candidate"], [r["day"] for r in rows])}
            for key in ("baseline", "opponent")}
        report["groups"][group] = metrics
    report["advance_to_outcome_research"] = (not failures and report["games"] >= 100 and report["dates"] >= 30
        and all(v["descriptive_99pct_day_ci"][0] > 0 for v in report["groups"]["all"]["comparisons"].values())
        and all(g["mse"]["candidate"] < g["mse"]["opponent"] for k,g in report["groups"].items() if k != "all"))
    return report, predictions


def run(source, out, mode):
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != AUDITED_SHA256:
        raise ValueError("Unaudited source")
    out.mkdir(parents=True, exist_ok=False)
    metadata = {"config": CONFIG, "mode": mode, "input_sha256": AUDITED_SHA256,
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "helper_sha256": hashlib.sha256(Path(__file__).with_name("soccer_process_gate.py").read_bytes()).hexdigest(),
                "started_at": datetime.now(timezone.utc).isoformat()}
    with closing(sqlite3.connect(out/"result.sqlite3")) as db, db:
        db.execute("CREATE TABLE run (metadata TEXT, status TEXT, report TEXT, predictions TEXT)")
        db.execute("INSERT INTO run VALUES (?, 'running', NULL, NULL)", (json.dumps(metadata),))
    try:
        games, skipped = load_games(json.loads(payload))
        report, predictions = evaluate(games, mode)
        report["skipped_input"] = skipped
        (out/"report.md").write_text("# K1 dynamic count experiment\n\nExploratory reused historical data; NOT win hit rate. No production adoption.\n\n```json\n" + json.dumps({**metadata, **report}, indent=2) + "\n```\n", encoding="utf-8")
        with closing(sqlite3.connect(out/"result.sqlite3")) as db, db:
            db.execute("UPDATE run SET status='complete', report=?, predictions=?", (json.dumps(report), json.dumps(predictions)))
    except Exception as exc:
        with closing(sqlite3.connect(out/"result.sqlite3")) as db, db:
            db.execute("UPDATE run SET status='failed', report=?", (json.dumps({"error": str(exc)}),))
        raise
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["dynamic", "venue_shrink"], required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output, args.mode)))
