"""Offline, standard-library-only calibration experiment; never a promotion path.

Consumes a minimized audit dataset, not a production database. Logistic offsets
are fit to EARLIER kickoff days only. Labels were reconciled later, so this is
not an as-of historical replay or a pristine future holdout. Choices stay fixed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path

KST = timezone(timedelta(hours=9))
LEVELS = ("global", "sport", "league", "market")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed


def sigmoid(value):
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1 + exp)


def logit(p):
    return math.log(p) - math.log1p(-p)


def key(row, level):
    if level == "global":
        return (level,)
    parts = [level, row["sport"]]
    if level in ("league", "market"):
        parts.append(row["league"])
    if level == "market":
        parts.append(row["market"])
    return tuple(parts)


def validate_protocol(protocol):
    if protocol.get("schema") != "retrospective-calibration-protocol-v1":
        raise ValueError("unsupported protocol")
    start, split, end = (date.fromisoformat(protocol[k]) for k in
                         ("train_start", "evaluation_start", "evaluation_end"))
    if not start < split <= end:
        raise ValueError("invalid chronological split")
    if protocol.get("operating_changes_allowed") is not False:
        raise ValueError("offline-only protocol required")
    if protocol.get("freeze_minutes", 0) < 30:
        raise ValueError("must preserve T-30")
    names = set()
    for candidate in protocol["candidates"]:
        name, levels = candidate["name"], candidate["levels"]
        if name in names or name == "baseline" or not name:
            raise ValueError("duplicate/reserved candidate name")
        names.add(name)
        if not levels or tuple(levels) != LEVELS[:len(levels)]:
            raise ValueError("candidate levels must be a hierarchy prefix")
        half = candidate["half_life_days"]
        if half is not None and (not math.isfinite(half) or half <= 0):
            raise ValueError("invalid half life")
    for level in LEVELS:
        penalty = protocol["ridge"][level]
        if not math.isfinite(penalty) or penalty <= 0:
            raise ValueError("positive ridge penalty required")
    if not 0 < protocol["alpha"] < 1:
        raise ValueError("invalid alpha")
    if (type(protocol["max_iterations"]) is not int
            or not 0 < protocol["max_iterations"] <= 10000
            or not 0 < protocol["tolerance"] < 1):
        raise ValueError("invalid optimizer settings")


def prepare(dataset, protocol):
    validate_protocol(protocol)
    if dataset.get("schema") != "recent-calibration-dataset-v1":
        raise ValueError("unsupported dataset")
    cutoff = instant(dataset["cutoff"])
    identities, events, accepted, exclusions = {}, {}, [], Counter()
    required = ("id", "event", "sport", "league", "market", "selection")
    for row in dataset["rows"]:
        if row.get("source") != "official" or row.get("state") not in ("hit", "miss"):
            exclusions["non_official_or_unsettled"] += 1
            continue
        if any(not isinstance(row.get(k), str) or not row[k] for k in required):
            raise ValueError("missing prediction identity/scope")
        p, odds = row["probability"], row["odds"]
        if (isinstance(p, bool) or not isinstance(p, (int, float))
                or not math.isfinite(p) or not 0 < p < 1
                or isinstance(odds, bool) or not isinstance(odds, (int, float))
                or not math.isfinite(odds) or odds <= 1):
            raise ValueError("invalid saved probability/odds")
        kickoff, capture = instant(row["kickoff"]), instant(row["capturedAt"])
        if kickoff.astimezone(KST).date().isoformat() != row["day"]:
            raise ValueError("KST day mismatch")
        if capture >= kickoff - timedelta(minutes=protocol["freeze_minutes"]):
            exclusions["not_before_freeze"] += 1
            continue
        if kickoff > cutoff:
            exclusions["future"] += 1
            continue
        if not protocol["train_start"] <= row["day"] <= protocol["evaluation_end"]:
            exclusions["outside_window"] += 1
            continue
        # A canonical event must not silently change revision, choice or label.
        fingerprint = digest({k: row.get(k) for k in (
            *required, "label", "probability", "odds", "state", "day", "kickoff", "capturedAt")})
        if row["id"] in identities or row["event"] in events:
            if (identities.get(row["id"]) != fingerprint
                    or events.get(row["event"]) != fingerprint):
                raise ValueError("conflicting prediction or duplicate event revision")
            exclusions["identical_duplicate"] += 1
            continue
        identities[row["id"]] = events[row["event"]] = fingerprint
        accepted.append(dict(row))
    accepted.sort(key=lambda r: (r["kickoff"], r["id"]))
    train = [r for r in accepted if r["day"] < protocol["evaluation_start"]]
    evaluation = [r for r in accepted if r["day"] >= protocol["evaluation_start"]]
    if not train or not evaluation:
        raise ValueError("nonempty earlier training and later evaluation required")
    return train, evaluation, dict(exclusions)


def fit(train, candidate, protocol):
    """Joint ridge-logistic fit by coordinate Newton, with no evaluation inputs."""
    anchor = date.fromisoformat(protocol["evaluation_start"])
    half = candidate["half_life_days"]
    weights = [1.0 if half is None else 2 ** (
        -(anchor - date.fromisoformat(r["day"])).days / half) for r in train]
    # Preserve effective regularization scale while changing relative recency.
    scale = len(train) / sum(weights)
    weights = [w * scale for w in weights]
    columns = defaultdict(list)
    for i, row in enumerate(train):
        for level in candidate["levels"]:
            columns[key(row, level)].append(i)
    columns = dict(sorted(columns.items()))
    beta = {k: 0.0 for k in columns}
    z = [logit(r["probability"]) for r in train]
    y = [int(r["state"] == "hit") for r in train]
    for iteration in range(protocol["max_iterations"]):
        change = 0.0
        for k, indexes in columns.items():
            penalty = protocol["ridge"][k[0]]
            gradient, curvature = -penalty * beta[k], penalty
            for i in indexes:
                p = sigmoid(z[i])
                gradient += weights[i] * (y[i] - p)
                curvature += weights[i] * p * (1 - p)
            step = max(-1.0, min(1.0, gradient / curvature))
            beta[k] += step
            for i in indexes:
                z[i] += step
            change = max(change, abs(step))
        if change < protocol["tolerance"]:
            break
    else:
        raise ValueError("calibration optimizer failed to converge")
    return {"coefficients": [{"key": list(k), "value": value} for k, value in beta.items()],
            "levels": candidate["levels"], "half_life_days": half,
            "iterations": iteration + 1, "train_n": len(train),
            "train_hash": digest(train)}


def predict(row, model):
    beta = {tuple(item["key"]): item["value"] for item in model["coefficients"]}
    offset = sum(beta.get(key(row, level), 0.0) for level in model["levels"])
    return min(1 - 1e-9, max(1e-9, sigmoid(logit(row["probability"]) + offset)))


def losses(p, y):
    return (p - y) ** 2, -(y * math.log(p) + (1-y) * math.log1p(-p))


def scores(rows, probabilities):
    if len(rows) != len(probabilities) or not rows:
        raise ValueError("aligned nonempty predictions required")
    pairs = [losses(p, int(r["state"] == "hit")) for r, p in zip(rows, probabilities)]
    return {"n": len(rows), "brier": sum(x[0] for x in pairs) / len(rows),
            "log_loss": sum(x[1] for x in pairs) / len(rows),
            "mean_probability": sum(probabilities) / len(rows)}


def paired_diagnostic(rows, probabilities):
    """Day-cluster sign-flip sensitivity test, not proof of causal improvement.

    Two-sided exact enumeration; requires symmetric independent day-cluster
    contrasts under the null. With only two dates p cannot be below 0.5.
    Refuse excessive enumeration instead of silently switching to iid games.
    """
    by_day = defaultdict(lambda: [0.0, 0.0, 0])
    for row, p in zip(rows, probabilities):
        y = int(row["state"] == "hit")
        base, candidate = losses(row["probability"], y), losses(p, y)
        values = by_day[row["day"]]
        for i in (0, 1):
            values[i] += base[i] - candidate[i]  # Positive means improvement.
        values[2] += 1
    days = sorted(by_day)
    n = len(rows)
    result = {"clusters": len(days), "method": "two_sided_exact_KST_day_sign_flip",
              "assumptions": "independent symmetric day-cluster contrasts; diagnostic only",
              "by_day": [{"day": d, "n": by_day[d][2],
                          "brier_gain": by_day[d][0] / by_day[d][2],
                          "log_loss_gain": by_day[d][1] / by_day[d][2]} for d in days]}
    for i, metric in enumerate(("brier", "log_loss")):
        delta = sum(by_day[d][i] for d in days) / n
        p_value = None
        if 2 <= len(days) <= 16:
            contrasts = [by_day[d][i] for d in days]
            observed = abs(sum(contrasts))
            extreme = sum(abs(sum(s*v for s, v in zip(signs, contrasts))) >= observed-1e-12
                          for signs in itertools.product((-1, 1), repeat=len(days)))
            p_value = extreme / 2 ** len(days)
        result[metric + "_gain"] = delta
        result[metric + "_p"] = p_value
    return result


def holm(values):
    """Multiplicity adjustment across all candidate × metric diagnostics."""
    valid = sorted((v, k) for k, v in values.items() if v is not None)
    result, last = {k: None for k in values}, 0.0
    for i, (p, k) in enumerate(valid):
        last = max(last, min(1.0, (len(valid)-i) * p))
        result[k] = last
    return result


def experiment(dataset, protocol):
    train, evaluation, exclusions = prepare(dataset, protocol)
    base = scores(evaluation, [r["probability"] for r in evaluation])
    output, p_values, candidate_probabilities = {}, {}, {}
    for candidate in protocol["candidates"]:
        name = candidate["name"]
        model = fit(train, candidate, protocol)
        probabilities = [predict(r, model) for r in evaluation]
        candidate_probabilities[name] = probabilities
        diagnostic = paired_diagnostic(evaluation, probabilities)
        output[name] = {"model": model, "metrics": scores(evaluation, probabilities),
                        "paired": diagnostic}
        for metric in ("brier", "log_loss"):
            p_values[name + ":" + metric] = diagnostic[metric + "_p"]
    adjusted = holm(p_values)
    for name, result in output.items():
        result["paired"]["holm_adjusted_p"] = {
            metric: adjusted[name + ":" + metric] for metric in ("brier", "log_loss")}
    groups = {}
    for dimension in ("day", "sport", "league", "market"):
        groups[dimension] = {}
        for group in sorted({r[dimension] for r in evaluation}):
            indexes = [i for i, r in enumerate(evaluation) if r[dimension] == group]
            rows = [evaluation[i] for i in indexes]
            groups[dimension][group] = {
                "baseline": scores(rows, [r["probability"] for r in rows]),
                "candidates": {name: scores(rows, [ps[i] for i in indexes])
                               for name, ps in candidate_probabilities.items()}}
    hit = sum(r["state"] == "hit" for r in evaluation)
    roi = sum(r["odds"]-1 if r["state"] == "hit" else -1 for r in evaluation)/len(evaluation)
    return {"schema": "recent-calibration-report-v1", "classification": "exploratory_retrospective",
            "promotion_allowed": False, "pristine_future": False,
            "conclusion": "not_established_requires_preregistered_future_validation",
            "protocol_hash": digest(protocol), "dataset_hash": digest(dataset),
            "source_provenance": dataset.get("provenance", {}), "cutoff": dataset["cutoff"],
            "train": {"n": len(train), "days": dict(Counter(r["day"] for r in train))},
            "evaluation": {"n": len(evaluation), "days": dict(Counter(r["day"] for r in evaluation)),
                           "db_verified": sum(r.get("dbVerified") is True for r in evaluation),
                           "fixed_pick_hit": hit, "fixed_pick_hit_rate": hit/len(evaluation),
                           "fixed_pick_roi": roi},
            "exclusions": exclusions, "baseline": base, "candidates": output, "groups": groups,
            "predictions": [{"id": r["id"], "event": r["event"], "day": r["day"],
                             "state": r["state"], "baseline": r["probability"],
                             "candidates": {name: ps[i] for name, ps in candidate_probabilities.items()}}
                            for i, r in enumerate(evaluation)],
            "limitations": [
                "Evaluation outcomes were inspected before experiment design; not pristine.",
                "Historical result availability is unverified; date split is not an as-of replay.",
                "Only two evaluation dates in the captured sample; day-cluster inference is weak.",
                "Official-result coverage is incomplete and nonrandom; all recorded picks, not daily highlights.",
                "Fixed selected-pick calibration only; no new choices, ROI gains, xG or starter effects tested.",
                "No hyperparameter selection or operational promotion from this experiment."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    # Exclusive creation also protects files unrelated to this experiment.
    if args.output.resolve() in {args.input.resolve(), args.protocol.resolve()}:
        parser.error("output cannot overwrite an input")
    dataset = json.loads(args.input.read_text(encoding="utf-8"))
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    report = experiment(dataset, protocol)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"train": report["train"], "evaluation": report["evaluation"],
                      "baseline": report["baseline"],
                      "candidates": {k: {"metrics": v["metrics"], "paired": v["paired"]}
                                     for k, v in report["candidates"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
