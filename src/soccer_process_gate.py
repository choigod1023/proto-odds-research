"""Offline, exploratory next-match count gate. Never changes production models."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import date, timedelta, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np

CONFIG = {"league": "K1", "window": 10, "minimum_history": 5,
          "history_lag_days": 2, "train_before": "2025-01-01", "ridge": 32,
          "bootstrap": 3000, "seed": 20260909}

# Audited snapshot: 228 fixtures/year (12 teams x 38 matches), then playoff reset.
AUDITED_SHA256 = "eae45623ea74c6bc15c75dbcd510cd7a1bb6d0949774ea84b0e583e71e025672"
PLAYOFF_IDS = {"2023120606291", "2023120636212", "2023120929063", "2023120921364",
               "2024112834171", "2024120131052", "2024120117343", "2024120805314",
               "2025120302041", "2025120526292", "2025120704023", "2025120829264"}


def load_games(raw):
    games, skipped = [], 0
    identities = set()
    for event_id, g in raw.items():
        if event_id in PLAYOFF_IDS:
            continue
        try:
            day = date.fromisoformat(g["date"])
            home, away = g["home"], g["away"]
            if not isinstance(home, str) or not home or not isinstance(away, str) or not away or home == away:
                raise ValueError("invalid teams")
            identity = (day, home, away)
            if identity in identities:
                raise ValueError("Duplicate fixture: cannot silently select revision")
            values = []
            for side in ("home", "away"):
                s = g["data"][side]
                v = [s["shots"], s["sog"]]
                if any(isinstance(x, bool) or not isinstance(x, (float, int)) or
                       not np.isfinite(x) or x < 0 or x != int(x) for x in v) or v[1] > v[0]:
                    raise TypeError("invalid counts")
                values.append(v)
            identities.add(identity)
            games.append({"id": str(event_id), "day": day, "teams": (home, away), "counts": values})
        except (KeyError, TypeError):
            skipped += 1
    return sorted(games, key=lambda g: (g["day"], g["id"])), skipped


def examples(games):
    history = defaultdict(list)
    rows = []
    for g in games:
        past = [[x for x in history[t] if x[0] <= g["day"] - timedelta(days=CONFIG["history_lag_days"])][-CONFIG["window"]:]
                for t in g["teams"]]
        if all(len(h) >= CONFIG["minimum_history"] for h in past):
            means = [np.mean([x[1] for x in h], axis=0) for h in past]
            for side in (0, 1):
                own, other = means[side], means[1-side]
                # Columns: shots for, SOG for, shots against, SOG against.
                rows.append({"id": g["id"], "day": g["day"], "side": side,
                             "x": [own[0], own[1], other[2], other[3], float(side == 0)],
                             "baseline": own[:2], "opponent": (own[:2] + other[2:])/2,
                             "y": g["counts"][side]})
        for side, team in enumerate(g["teams"]):
            history[team].append((g["day"], g["counts"][side] + g["counts"][1-side]))
    return rows


def interval_by_day(gains, days):
    unique = sorted(set(days))
    blocks = [np.asarray(gains)[np.asarray(days) == d] for d in unique]
    sums = np.array([b.sum() for b in blocks])
    sizes = np.array([len(b) for b in blocks])
    rng = np.random.default_rng(CONFIG["seed"])
    draws = rng.integers(len(blocks), size=(CONFIG["bootstrap"], len(blocks)))
    boot = sums[draws].sum(axis=1) / sizes[draws].sum(axis=1)
    return np.quantile(boot, [.005, .995]).tolist()


def evaluate(rows):
    cutoff = date.fromisoformat(CONFIG["train_before"])
    tr = [r for r in rows if r["day"] <= cutoff - timedelta(days=CONFIG["history_lag_days"])]
    te = [r for r in rows if r["day"] >= cutoff]
    counts = {"train_games": len({r["id"] for r in tr}), "test_games": len({r["id"] for r in te}),
              "test_dates": len({r["day"] for r in te})}
    if counts["train_games"] < 200 or counts["test_games"] < 100 or counts["test_dates"] < 30:
        return {**counts, "status": "insufficient_sample", "production_allowed": False}
    x = np.array([r["x"] for r in tr]); y = np.array([r["y"] for r in tr])
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1
    z = np.column_stack([np.ones(len(x)), (x - mean)/scale])
    penalty = np.eye(z.shape[1])*CONFIG["ridge"]; penalty[0, 0] = 0
    weights = np.linalg.solve(z.T@z + penalty, z.T@y)
    xt = np.array([r["x"] for r in te])
    predicted = np.maximum(0, np.column_stack([np.ones(len(te)), (xt-mean)/scale])@weights)
    actual = np.array([r["y"] for r in te])
    days = [r["day"].isoformat() for r in te]
    result = {**counts, "status": "exploratory_only", "production_allowed": False,
              "test_from": min(days), "test_to": max(days), "metrics": {},
              "predicted_sog_exceeds_shots": int((predicted[:, 1] > predicted[:, 0]).sum()),
              "by_year": {}}
    for i, target in enumerate(("shots", "sog")):
        loss = (actual[:, i]-predicted[:, i])**2
        metrics = {"candidate_mse": float(loss.mean()), "comparisons": {}}
        for name in ("baseline", "opponent"):
            reference = np.array([r[name][i] for r in te])
            reference_loss = (actual[:, i]-reference)**2
            gain = reference_loss-loss
            metrics["comparisons"][name] = {"mse": float(reference_loss.mean()),
                "gain": float(gain.mean()), "descriptive_99pct_day_bootstrap": interval_by_day(gain, days)}
        metrics["next_stage_candidate"] = all(v["descriptive_99pct_day_bootstrap"][0] > 0
                                             for v in metrics["comparisons"].values())
        result["metrics"][target] = metrics
        for year in sorted({d[:4] for d in days}):
            mask = np.array([d.startswith(year) for d in days])
            result["by_year"].setdefault(year, {"games": len({r["id"] for r in te if r["day"].year == int(year)})})[target] = {
                "candidate_mse": float(loss[mask].mean()),
                **{name + "_mse": float(np.mean((actual[mask, i] - np.array([r[name][i] for r in te])[mask])**2))
                   for name in ("baseline", "opponent")}}
    result["predictions"] = [{"event_id": r["id"], "day": r["day"].isoformat(), "side": r["side"],
                              "actual": r["y"], "predicted": p.tolist()} for r, p in zip(te, predicted)]
    return result


def run(source, database, report):
    if report.exists():
        raise FileExistsError("Report already exists; refusing to overwrite an experiment")
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != AUDITED_SHA256:
        raise ValueError("New source snapshot requires competition and target-quality audit")
    metadata = {"input_sha256": hashlib.sha256(payload).hexdigest(), "config": CONFIG,
                "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "evaluation": "reused historical data; exploratory; no pregame capture proof"}
    signature = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS runs (signature TEXT PRIMARY KEY, started TEXT, status TEXT, metadata TEXT, result TEXT)")
        db.execute("INSERT INTO runs VALUES (?, ?, 'running', ?, NULL)",
                   (signature, datetime.now(timezone.utc).isoformat(), json.dumps(metadata)))
    try:
        games, skipped = load_games(json.loads(payload))
        result = {"source_games": len(games), "excluded_playoffs": len(PLAYOFF_IDS),
                  "skipped": skipped, **evaluate(examples(games))}
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE runs SET status='evaluated', result=? WHERE signature=?", (json.dumps(result), signature))
    except Exception as exc:
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE runs SET status='failed', result=? WHERE signature=?", (json.dumps({"error": str(exc)}), signature))
        raise
    compact = {k: v for k, v in result.items() if k != "predictions"}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x", encoding="utf-8") as out:
        out.write("# K1 다음 경기 슈팅 예측: 첫 관문\n\n과거 재사용 탐색 결과. 승패 적중률 실험이 아니며 운영 반영 금지.\n\n")
        out.write("```json\n" + json.dumps({**metadata, **compact}, ensure_ascii=False, indent=2) + "\n```\n")
        out.write("\n날짜 +2일 이후에만 과거 기록을 사용했다. 실제 공개시각은 검증되지 않았다. "
                  "99% 구간은 날짜 묶음 재표집의 기술적 구간이며 반복 탐색 보정이나 독립 재현을 대체하지 않는다. "
                  "SOG 정의와 연도별 품질 감사 전에는 다음 단계로 승격하지 않는다.\n")
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE runs SET status='complete' WHERE signature=?", (signature,))
    return compact


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.database, args.report), ensure_ascii=True))
