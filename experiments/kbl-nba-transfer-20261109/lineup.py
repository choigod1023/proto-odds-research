"""Estimate player and player-pair effects from official historical KBL stints."""

import argparse
from collections import Counter, defaultdict
import gzip
from itertools import combinations
import json
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix, diags, vstack
from scipy.sparse.linalg import lsqr
from scipy.special import expit, logit

from collect_charts import capture
from run import fit_offset, load, match, metrics, read_snapshot, sha

ENDPOINT = "text-cast?quarterList=Q1,Q2,Q3,Q4"


def lineup_games(games):
    candidates = sorted(
        (g for g in games if "players" in g and g["day"] <= "2025-09-26"
         and sum(p[1] for p in g["players"].values()) == 24000),
        key=lambda g: (g["day"], g["id"]),
    )
    indices = np.linspace(0, len(candidates) - 1, min(100, len(candidates)), dtype=int)
    return [candidates[i] for i in indices]


def stint_features(home, away, pairs):
    features = {("home",): 1.0}
    for group, sign in ((home, 1), (away, -1)):
        for pid in group:
            features[(pid,)] = features.get((pid,), 0.0) + sign
        if pairs:
            for pair in combinations(sorted(group), 2):
                features[pair] = features.get(pair, 0.0) + sign
    return features


def fit(stints, pairs):
    columns, row_ids, col_ids, data, labels = {}, [], [], [], []
    for i, (home, away, seconds, margin) in enumerate(stints):
        seconds = max(seconds, 1)
        weight = np.sqrt(seconds / 2400)
        for feature, value in stint_features(home, away, pairs).items():
            j = columns.setdefault(feature, len(columns))
            row_ids.append(i)
            col_ids.append(j)
            data.append(weight * value)
        labels.append(weight * margin * 2400 / seconds)
    design = csr_matrix((data, (row_ids, col_ids)), shape=(len(stints), len(columns)))
    penalty = [0.01 if key == ("home",) else (4 if len(key) == 1 else 16) for key in columns]
    aug = vstack([design, diags(np.sqrt(penalty))]).tocsr()
    coefficients = lsqr(aug, np.r_[labels, np.zeros(len(columns))], atol=1e-9, btol=1e-9)[0]
    return dict(zip(columns, coefficients, strict=True))


def expected_pairs(minutes):
    """Integrate five equally spaced court slots over a random unit offset."""
    players = np.array(sorted(minutes))
    rng = np.random.default_rng(20261109)
    pairs = defaultdict(float)
    for _ in range(32):
        order = rng.permutation(players)
        bounds = np.cumsum([minutes[p] / 40 for p in order])
        bounds[-1] = 5
        breaks = sorted({0.0, 1.0, *(value % 1 for value in bounds[:-1])})
        for start, end in zip(breaks[:-1], breaks[1:], strict=True):
            if end - start <= 1e-12:
                continue
            slots = (start + end) / 2 + np.arange(5)
            lineup = order[np.searchsorted(bounds, slots, side="right")]
            for pair in combinations(sorted(lineup), 2):
                pairs[pair] += (end - start) / 32
    return pairs


def expected_margin(groups, effects):
    margin = effects[("home",)]
    for players, sign in zip(groups, (1, -1), strict=True):
        margin += sign * sum(minutes / 40 * effects.get((pid,), 0.0) for pid, minutes in players.items())
        margin += sign * sum(weight * effects.get(pair, 0.0) for pair, weight in expected_pairs(players).items())
    return float(margin)


def run(games, selected, boxes, casts, out):
    from stints import reconstruct

    all_stints, rejected, receipts, accepted = [], Counter(), [], []
    for g in selected:
        try:
            cast, receipt = read_snapshot(casts, g["id"], ENDPOINT)
            player_box, _ = read_snapshot(boxes, g["id"], "player-stat")
            receipts.append(receipt)
            box_home = {p["player"]["tcode"] for p in player_box if str(p["homeAway"]) == "1"}
            if box_home != {g["teams"][0]}:
                raise ValueError("official home team mismatch")
            stints = reconstruct(cast, player_box)
            all_stints.extend(stints)
            accepted.append(g["id"])
        except (OSError, ValueError, TypeError, KeyError) as error:
            rejected["missing" if isinstance(error, FileNotFoundError) else str(error)] += 1
    if not all_stints:
        raise ValueError("No verified stints")
    effects = [fit(all_stints, pairs) for pairs in (False, True)]
    rows, _ = match(games)
    output = []
    for row in rows:
        if row["day"] < "2025-10-01":
            continue
        single, pair = [expected_margin(row["minutes"], effect) for effect in effects]
        output.append(dict(row, x=[*row["x"][:3], single / 10, (pair - single) / 10]))
    calibration = [r for r in output if r["day"] <= "2025-12-26"]
    evaluation = [r for r in output if r["day"] >= "2026-01-01"]
    families = {"efficiency": 1, "rotation": 3, "player": 4, "pair": 5}
    coefficients = {name: fit_offset(calibration, n) for name, n in families.items()}
    for row in evaluation:
        offset = logit(row["market"])
        row["prob"] = {"market": row["market"]}
        for name, beta in coefficients.items():
            row["prob"][name] = float(expit(offset + np.dot(row["x"][:len(beta)], beta)))
    report = {"requested_games": len(selected), "accepted_games": accepted, "rejections": rejected,
              "stints": len(all_stints), "calibration_n": len(calibration), "evaluation_n": len(evaluation),
              "coef": {n: v.tolist() for n, v in coefficients.items()},
              "source_receipts": receipts, "metrics": metrics(evaluation, ("market", *families))}
    out.parent.mkdir(parents=True, exist_ok=True)
    body = b"".join(
        (json.dumps(row, sort_keys=True) + "\n").encode() for row in evaluation
    )
    report["predictions_sha256_uncompressed"] = sha(body)
    out.write_bytes((json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
    out.with_name("lineup-predictions.jsonl.gz").write_bytes(gzip.compress(body, mtime=0))
    print("games", len(accepted), "/", len(selected), "stints", len(all_stints), "rejections", dict(rejected))
    for name, metric in report["metrics"].items():
        print(name, metric["hits"], f'{metric["roi"]:.3%}', f'{metric["logloss"]:.6f}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boxes", type=Path, required=True)
    parser.add_argument("--casts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args()
    games, _, _ = load(args.boxes)
    selected = lineup_games(games)
    if args.capture:
        capture([g["id"] for g in selected], args.casts, ENDPOINT)
        return
    run(games, selected, args.boxes, args.casts, args.out)


if __name__ == "__main__":
    main()
