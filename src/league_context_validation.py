"""Research-only, league-independent context validation. Never promotes a model.

JSON input rows: event_id, league, kickoff (timezone required), feature_as_of,
features (finite numbers), target (H/A=0/1 or H/D/A=0/1/2), odds,
odds_as_of (nullable). Unknown historical price timing is NOT T-30 evidence.
Actual frontend rules are invoked on the supplied market subset, not all markets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from devig import shin

ALPHAS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)
ROOT = Path(__file__).resolve().parents[1]


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)


def validate_rows(rows):
    """Reject conflicting event revisions rather than choosing a favorable one."""
    grouped = defaultdict(list)
    revisions = defaultdict(set)
    rejected = defaultdict(int)
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise ValueError('invalid_row')
            if not all(isinstance(row.get(k), str) and row[k].strip() for k in ('event_id', 'league')):
                raise ValueError('invalid_identity')
            revisions[(row['league'], row['event_id'])].add(json.dumps(row, sort_keys=True))
            kickoff = timestamp(row["kickoff"])
            if timestamp(row["feature_as_of"]) > kickoff - timedelta(minutes=30):
                raise ValueError("feature_after_cutoff")
            if row.get("odds_as_of") and timestamp(row["odds_as_of"]) > kickoff - timedelta(minutes=30):
                raise ValueError("odds_after_cutoff")
            odds = row["odds"]
            if len(odds) not in (2, 3) or any(not math.isfinite(x) or x <= 1 for x in odds):
                raise ValueError("invalid_odds")
            if type(row["target"]) is not int or not 0 <= row["target"] < len(odds):
                raise ValueError("invalid_target")
            if not row["features"] or not all(math.isfinite(x) for x in row["features"]):
                raise ValueError("missing_features")
            if not row["event_id"] or not row["league"]:
                raise ValueError("missing_identity")
            grouped[(row["league"], row["event_id"])].append(row)
        except (ValueError, TypeError, KeyError) as exc:
            rejected[str(exc)] += 1
    out = []
    for group in grouped.values():
        variants = revisions[(group[0]['league'], group[0]['event_id'])]
        if len(variants) > 1:
            rejected["conflicting_event_rows"] += len(group)
        else:
            out.append(group[0])
            rejected["exact_duplicates"] += len(group) - 1
    return sorted(out, key=lambda r: (timestamp(r["kickoff"]), r["event_id"])), dict(rejected)


def split_rows(rows):
    """Date blocks, 7-day embargo at both boundaries; no random split."""
    dates = sorted({timestamp(r["kickoff"]).date() for r in rows})
    if len(dates) < 10:
        return [], [], []
    first, second = dates[int(len(dates) * .6)], dates[int(len(dates) * .8)]
    train = [r for r in rows if timestamp(r["kickoff"]).date() < first - timedelta(days=7)]
    valid = [r for r in rows if first <= timestamp(r["kickoff"]).date() < second - timedelta(days=7)]
    test = [r for r in rows if timestamp(r["kickoff"]).date() >= second]
    return train, valid, test


def softmax(scores):
    e = np.exp(scores - scores.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def market(rows):
    return np.array([shin(r["odds"]) for r in rows])


def fit_predict(train, test):
    """Fixed L2=32, train-only normalization, log-market offset."""
    x = np.array([r["features"] for r in train], float)
    xt = np.array([r["features"] for r in test], float)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1
    x = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    xt = np.column_stack([np.ones(len(xt)), (xt - mean) / scale])
    off = np.log(market(train))
    k = off.shape[1]
    y = np.eye(k)[[r["target"] for r in train]]

    def loss(beta):
        b = beta.reshape(x.shape[1], k)
        p = softmax(off + x @ b)
        value = -np.sum(y * np.log(np.clip(p, 1e-12, 1))) + 16 * np.sum(b[1:] ** 2)
        grad = x.T @ (p - y)
        grad[1:] += 32 * b[1:]
        return value, grad.ravel()

    opt = minimize(loss, np.zeros(x.shape[1] * k), jac=True, method="L-BFGS-B", options={"maxiter": 500})
    if not opt.success:
        raise ValueError("fit_failed: " + str(opt.message))
    return softmax(np.log(market(test)) + xt @ opt.x.reshape(x.shape[1], k))


def brier_losses(rows, p):
    return ((p - np.eye(p.shape[1])[[r["target"] for r in rows]]) ** 2).sum(axis=1)


def choose_alpha(rows, base, candidate):
    losses = np.array([brier_losses(rows, (1-a)*base+a*candidate) for a in ALPHAS])
    best = int(losses.mean(axis=1).argmin())
    # Paired date-block standard error; retain smallest indistinguishable weight.
    dates = [timestamp(r["kickoff"]).date() for r in rows]
    for i, alpha in enumerate(ALPHAS):
        diff = losses[i] - losses[best]
        day_means = [diff[np.array([d == day for d in dates])].mean() for day in sorted(set(dates))]
        se = np.std(day_means, ddof=1) / math.sqrt(len(day_means)) if len(day_means) > 1 else 0
        if diff.mean() <= se + 1e-12:
            return alpha
    return ALPHAS[best]


def metrics(rows, p):
    y = np.array([r["target"] for r in rows])
    return {"n": len(rows), "hits": int((p.argmax(axis=1) == y).sum()),
            "hit_rate": float((p.argmax(axis=1) == y).mean()),
            "brier_multiclass_sum": float(brier_losses(rows, p).mean()),
            "log_loss": float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1)).mean())}


def paired_interval(rows, base, candidate, draws=5000):
    """Date-block paired bootstrap, descriptive unadjusted 95% interval."""
    diffs = brier_losses(rows, base) - brier_losses(rows, candidate)
    groups = defaultdict(list)
    for row, value in zip(rows, diffs):
        groups[timestamp(row["kickoff"]).date()].append(value)
    sums = np.array([sum(g) for g in groups.values()])
    counts = np.array([len(g) for g in groups.values()])
    rng = np.random.default_rng(20260909)
    sampled = rng.integers(0, len(sums), (draws, len(sums)))
    estimates = sums[sampled].sum(axis=1) / counts[sampled].sum(axis=1)
    return {"brier_gain": float(diffs.mean()), "ci95_unadjusted": np.quantile(estimates, [.025, .975]).tolist(),
            "date_blocks": len(groups), "bootstrap_draws": draws}


def replay(rows, probabilities, node):
    payload = []
    for row, p, mp in zip(rows, probabilities, market(rows)):
        for i, odd in enumerate(row["odds"]):
            payload.append({"event_key": row["event_id"], "league": row["league"],
                            "kickoff_at": row["kickoff"], "market": "승무패" if len(p)==3 else "승패",
                            "selection_id": f'{row["event_id"]}|{i}', "odds": odd,
                            "market_prob": float(mp[i]), "predicted_hit_prob": float(p[i]),
                            "hit": int(row["target"] == i)})
    proc = subprocess.run([node, str(ROOT / "scripts/context_policy_bridge.mjs")],
                          input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True, check=True)
    picks = json.loads(proc.stdout)
    n = len(picks)
    return {"n": n, "hits": sum(p["hit"] for p in picks),
            "hit_rate": sum(p["hit"] for p in picks)/n if n else None,
            "mean_odds": sum(p["odds"] for p in picks)/n if n else None,
            "unit_roi": sum(p["hit"]*p["odds"]-1 for p in picks)/n if n else None,
            "picks": picks}


def matched_policy(left, right):
    """Outcome-blind diagnostic: same KST date and 0.1-odds-bin counts."""
    pools = [defaultdict(list), defaultdict(list)]
    for pool, picks in zip(pools, (left, right)):
        for p in picks:
            key = (timestamp(p['kickoff_at']).astimezone(timezone(timedelta(hours=9))).date(), math.floor(p['odds']*10+1e-8))
            pool[key].append(p)
    selected = [[], []]
    for key in sorted(pools[0].keys() & pools[1].keys()):
        n = min(len(pools[0][key]), len(pools[1][key]))
        for i in range(2):
            selected[i].extend(sorted(pools[i][key], key=lambda p: (-p['predicted_hit_prob'], p['selection_id']))[:n])
    output = {'method': 'same KST date and 0.1 odds bin; diagnostic not deployable selector'}
    for label, picks in zip(('market', 'selected'), selected):
        n = len(picks)
        output[label] = {'n': n, 'hits': sum(p['hit'] for p in picks),
                         'hit_rate': sum(p['hit'] for p in picks)/n if n else None,
                         'mean_odds': sum(p['odds'] for p in picks)/n if n else None}
    return output


def evaluate(rows, node):
    clean, rejected = validate_rows(rows)
    result = {"production_allowed": False, "rejected": rejected, "leagues": {},
              "scope": "winner-market subset only; not full-site recommendation replay",
              "inference": "exploratory; reused historical data; no multiple-testing promotion"}
    for league in sorted({r["league"] for r in clean}):
        subset = [r for r in clean if r["league"] == league]
        tr, va, te = split_rows(subset)
        entry = {"input_n": len(subset), "train_n": len(tr), "validation_n": len(va), "test_n": len(te),
                 "known_odds_time_n": sum(bool(r.get("odds_as_of")) for r in subset)}
        result["leagues"][league] = entry
        if min(len(tr), len(va), len(te)) < 50 or len(tr) < 200:
            entry["status"] = "insufficient_data"
            continue
        if len({len(r["features"]) for r in subset}) != 1 or len({len(r["odds"]) for r in subset}) != 1:
            entry["status"] = "inconsistent_schema"
            continue
        if any(sum(r['target']==i for r in tr)<5 for i in range(len(tr[0]['odds']))):
            entry['status'] = 'insufficient_training_class_support'
            continue
        alpha = choose_alpha(va, market(va), fit_predict(tr, va))
        base = market(te)
        candidate = fit_predict(tr + va, te)
        selected = (1-alpha)*base + alpha*candidate
        entry.update(status="evaluated", alpha=alpha,
                     test_start=te[0]["kickoff"], test_end=te[-1]["kickoff"],
                     market=metrics(te, base), candidate=metrics(te, candidate), selected=metrics(te, selected),
                     paired=paired_interval(te, base, selected),
                     market_policy=replay(te, base, node), selected_policy=replay(te, selected, node),
                     t30_proof=all(r.get("odds_as_of") and r.get('feature_availability_verified') is True for r in subset))
        entry['matched_policy'] = matched_policy(entry['market_policy']['picks'], entry['selected_policy']['picks'])
        entry['candidate_paired'] = paired_interval(te, base, candidate)
        # Compare probabilities on identical market-favorite picks, so fewer bets
        # cannot masquerade as higher forecast quality.
        fav = base.argmax(axis=1)
        truth = np.array([r["target"] for r in te]) == fav
        entry["same_favorite_probability_brier"] = {
            "n": len(te), "market": float(np.mean((base[np.arange(len(te)),fav]-truth)**2)),
            "selected": float(np.mean((selected[np.arange(len(te)),fav]-truth)**2))}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('output must not overwrite input')
    content = args.input.read_bytes()
    result = evaluate(json.loads(content), args.node)
    result["input_sha256"] = hashlib.sha256(content).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for league, entry in result["leagues"].items():
        print(league, entry["status"], entry.get("alpha"), entry.get("selected", {}))


if __name__ == "__main__":
    main()
