"""Fit a coherent K League score distribution to contemporaneous market quotes."""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln, logsumexp
from scipy.stats import poisson

SOURCE = "859fc7f7"
PREFIX = "experiments/kleague-cross-market-nba-20261106/"
MAX_GOALS = 24
GOALS = np.array(list(np.ndindex(MAX_GOALS + 1, MAX_GOALS + 1)), dtype=float)
LOG_FACTORIAL = gammaln(GOALS + 1).sum(axis=1)
DIFF = GOALS[:, 0] - GOALS[:, 1]
TOTAL = GOALS.sum(axis=1)
WINNER = np.array([DIFF > 0, DIFF == 0, DIFF < 0])
STARTS = np.log([[1.35, 1.05], [0.65, 2.25], [2.25, 0.65]])
BOUNDS = [(np.log(0.05), np.log(8.0))] * 2


def source_blob(name):
    return subprocess.check_output(["git", "show", f"{SOURCE}:{PREFIX}{name}"])


def load_source():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for name in ("kleague_replay.py", "round-260132.json", "round-260132.body.gz"):
            (root / name).write_bytes(source_blob(name))
        spec = importlib.util.spec_from_file_location("previous_market_audit", root / "kleague_replay.py")
        archive = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(archive)
        historical, _ = archive.snapshots()
        snapshot = archive.link_pregame_schedule(archive.pregame_snapshot(root / "round-260132.json"))
    heldout = {row["fixture_id"]: row for row in map(json.loads, source_blob("kleague_results.picks.jsonl").splitlines())}
    if len(historical) != 893 or len(heldout) != 239 or not heldout.keys() <= historical.keys():
        raise ValueError("Historical cohort differs from protocol")
    if (len(snapshot["fixtures"]) != 14
            or any(not row["official_fixture_id"] for row in snapshot["fixtures"])):
        raise ValueError("Pregame cohort differs from protocol")
    return historical, heldout, snapshot


def implied(odds):
    inverse = 1 / np.array(odds)
    return inverse / inverse.sum()


def market_events(quotes, extra):
    q = implied(quotes["winner"]["odds"])
    if not extra:
        return WINNER, q
    total = quotes["total"]["line"]
    handicap = DIFF + quotes["handicap"]["line"]
    groups = np.concatenate([WINNER, [TOTAL < total, TOTAL > total],
                             [handicap > 0, handicap == 0, handicap < 0]])
    target = np.concatenate([q, implied(quotes["total"]["odds"]), implied(quotes["handicap"]["odds"])])
    return groups, target / 3


def distribution(log_rates):
    logp = GOALS @ log_rates - LOG_FACTORIAL
    return np.exp(logp - logsumexp(logp))


def fit_score(quotes, extra):
    groups, target = market_events(quotes, extra)
    groups = groups.astype(float)

    def objective(log_rates):
        p = distribution(log_rates)
        market = groups @ p
        gradient = groups @ (p[:, None] * (GOALS - p @ GOALS))
        return -target @ np.log(market), -(target / market) @ gradient

    fits = [minimize(objective, start, jac=True, bounds=BOUNDS, method="L-BFGS-B",
                     options={"ftol": 1e-12, "gtol": 1e-8}) for start in STARTS]
    successful = [fit for fit in fits if fit.success]
    if not successful:
        raise RuntimeError("All starting points failed to fit the market distribution")
    chosen = min(successful, key=lambda fit: fit.fun)
    p = WINNER @ distribution(chosen.x)
    return p.tolist(), np.exp(chosen.x).tolist()


def forecasts(quotes):
    one, one_rates = fit_score(quotes, False)
    three, three_rates = fit_score(quotes, True)
    return ({"winner_implied": implied(quotes["winner"]["odds"]).tolist(),
             "poisson_winner": one, "poisson_three": three},
            {"poisson_winner": one_rates, "poisson_three": three_rates})


def historical_rows(historical, heldout):
    for key, row in sorted(historical.items(), key=lambda item: (item[1]["kickoff_kst"], item[0])):
        quotes = row["quotes"]
        p, rates = forecasts(quotes)
        if key in heldout:
            previous = heldout[key]
            if quotes != previous["quotes"] or row["official_score"] != previous["official_score"]:
                raise ValueError("Previous held-out quote or score differs")
            for model in ("team_poisson", "winner_calibrated", "cross_market"):
                p[model] = previous["probabilities"][model]
        home, away = row["official_score"]
        outcome = 0 if home > away else 1 if home == away else 2
        yield {"fixture_id": key, "league": row["league_id"], "kickoff_kst": row["kickoff_kst"],
               "historical_response_sha256": row["response_sha256"], "round": row["gmTs"],
               "previous_comparison": key in heldout, "score": row["official_score"], "outcome": outcome,
               "quotes": quotes, "probabilities": p, "rates": rates}


def scores(rows, model):
    p = np.array([row["probabilities"][model] for row in rows])
    outcomes = np.array([row["outcome"] for row in rows])
    odds = np.array([row["quotes"]["winner"]["odds"] for row in rows])
    indices = np.arange(len(rows))
    picks = p.argmax(axis=1)
    hit = picks == outcomes
    return {"log_score": np.log(p[indices, outcomes]), "brier": ((p - np.eye(3)[outcomes]) ** 2).sum(axis=1),
            "hit": hit, "pnl": np.where(hit, odds[indices, picks] - 1, -1)}


def paired_interval(rows, delta, block):
    weeks = defaultdict(list)
    for row, value in zip(rows, delta, strict=True):
        day = date.fromisoformat(row["kickoff_kst"][:10])
        weeks[day - timedelta(days=day.weekday())].append(value)
    first, last = min(weeks), max(weeks)
    n = (last - first).days // 7 + 1
    series = [weeks[first + timedelta(weeks=i)] for i in range(n)]
    counts = np.array([len(week) for week in series])
    sums = np.array([sum(week) for week in series])
    starts = np.random.default_rng(20261106).integers(n, size=(10_000, (n + block - 1) // block))
    indices = ((starts[:, :, None] + np.arange(block)) % n).reshape(10_000, -1)[:, :n]
    denominators = counts[indices].sum(axis=1)
    means = sums[indices].sum(axis=1)[denominators > 0] / denominators[denominators > 0]
    return np.quantile(means, [0.025, 0.975]).tolist()


def evaluate(rows):
    metrics = {}
    per_model = {model: scores(rows, model) for model in rows[0]["probabilities"]}
    for model, values in per_model.items():
        metrics[model] = {"log_loss": -float(values["log_score"].mean()),
                          "brier": float(values["brier"].mean()), "accuracy": float(values["hit"].mean()),
                          "hits": int(values["hit"].sum()), "roi": float(values["pnl"].mean())}
    contrasts = {}
    for base in ("winner_implied", "poisson_winner"):
        delta = per_model["poisson_three"]["log_score"] - per_model[base]["log_score"]
        contrasts[base] = {"log_gain_nats": float(delta.mean()),
                           "intervals": {str(block): paired_interval(rows, delta, block) for block in (1, 2, 4)}}
    return {"n": len(rows), "first": rows[0]["kickoff_kst"], "last": rows[-1]["kickoff_kst"],
            "metrics": metrics, "three_market_log_gain": contrasts}


def summarize(rows):
    main = [row for row in rows if row["previous_comparison"]]
    separate = [row for row in rows if not row["previous_comparison"]]
    null_returns = [1 / np.sum(1 / np.array(row["quotes"]["winner"]["odds"])) - 1 for row in main]
    return {"source_commit": SOURCE, "timing": "retrospective_closed_response",
            "poisson_grid_omitted_mass_upper_bound": 2 * float(poisson.sf(MAX_GOALS, 8)),
            "league_counts": {str(league): sum(row["league"] == league for row in rows) for league in (1, 2)},
            "n": len(rows), "prior_sample_239": evaluate(main), "other_archive_654": evaluate(separate),
            "prior_sample_by_league": {str(league): evaluate([r for r in main if r["league"] == league])
                                       for league in (1, 2)},
            "mean_implied_return_if_normalized_winner_were_true": float(np.mean(null_returns))}


def freeze(snapshot, path):
    predictions = []
    for row in snapshot["fixtures"]:
        p, rates = forecasts(row["quotes"])
        predictions.append(row | {"probabilities": p, "rates": rates,
                                  "picks": {key: int(np.argmax(value)) for key, value in p.items()}})
    issued = datetime.now(timezone.utc)
    if any(not datetime.fromisoformat(snapshot["received_at_kst"]) <= issued < datetime.fromisoformat(row["kickoff_kst"])
           for row in predictions):
        raise ValueError("Cannot freeze predictions outside the pregame window")
    frozen = {"issued_at_utc": issued.isoformat(), "source_commit": SOURCE, "round": snapshot["round"],
              "odds_received_at_kst": snapshot["received_at_kst"], "source_sha256": snapshot["response_sha256"],
              "protocol_sha256": hashlib.sha256(Path(__file__).with_name("PROTOCOL.md").read_bytes()).hexdigest(),
              "study_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "primary_comparison": "poisson_three vs winner_implied, paired mean log score",
              "fixtures": predictions}
    with path.open("xb") as stream:
        stream.write(encoded(frozen))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args()
    history, previous, pregame = load_source()
    if args.freeze:
        freeze(pregame, args.freeze)
    rows = list(historical_rows(history, previous))
    args.output.write_bytes(encoded(summarize(rows)))
    with args.output.with_suffix(".picks.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
