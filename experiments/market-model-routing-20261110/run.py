"""Evaluate frozen monthly model selection on chronological, matched offers."""

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
POLICIES = ("market", "reference", "accuracy", "guarded")
MIN_HISTORY = 40
EVIDENCE_LAG = timedelta(days=7)


def prepare(rows):
    seen = set()
    for original in rows:
        r = dict(original)
        gid, day, y, odds = r["id"], r["day"], r["y"], r["odds"]
        if gid in seen:
            raise ValueError(f"Duplicate offer identity: {gid}")
        seen.add(gid)
        if date.fromisoformat(day).isoformat() != day or len(odds) not in (2, 3):
            raise ValueError(f"Invalid date or outcome count: {gid}")
        if type(y) is not int or not 0 <= y < len(odds) or not all(math.isfinite(o) and o > 1 for o in odds):
            raise ValueError(f"Invalid settlement or odds: {gid}")
        r["reference"] = r.get("reference", "market")
        if not {"market", r["reference"]} <= r["prob"].keys():
            raise ValueError(f"Missing market or reference probability: {gid}")
        expected = np.reciprocal(np.asarray(odds, dtype=float))
        if not np.allclose(r["prob"]["market"], expected / expected.sum(), atol=1e-8, rtol=1e-8):
            raise ValueError(f"Incorrect market probability: {gid}")
        stats = {}
        for model, p in r["prob"].items():
            if len(p) != len(odds) or not all(math.isfinite(v) and 0 <= v <= 1 for v in p) or not math.isclose(sum(p), 1, abs_tol=1e-6):
                raise ValueError(f"Invalid model probability: {gid}/{model}")
            pick = max(range(len(p)), key=p.__getitem__)
            hit = int(pick == y)
            stats[model] = {"pick": pick, "hit": hit, "net": odds[pick] * hit - 1,
                            "logloss": -math.log(max(p[y], 1e-12))}
        r["stats"] = stats
        r["group"] = (r["sport"], r["league"], r["market"], r["period"], len(odds))
        yield r


def candidate_score(history, model, reference, guarded, comparisons):
    paired = [(r["day"], r["stats"][model], r["stats"][reference])
              for r in history if {model, reference} <= r["stats"].keys()]
    n = len(paired)
    if n < MIN_HISTORY:
        return None
    gain = sum(a["hit"] - b["hit"] for _, a, b in paired)
    if gain <= 0:
        return None
    loss = math.fsum(a["logloss"] - b["logloss"] for _, a, b in paired)
    changed = sum(a["pick"] != b["pick"] for _, a, b in paired)
    if guarded:
        if gain < 2 or changed < 5 or loss > 0:
            return None
        days = Counter()
        for day, a, b in paired:
            days[day] += a["hit"] - b["hit"]
        plus, minus = sum(v > 0 for v in days.values()), sum(v < 0 for v in days.values())
        if binomtest(plus, plus + minus, alternative="greater").pvalue > 0.10 / comparisons:
            return None
    return (-gain / n, loss / n, model, n, changed)


def choose(history, target, guarded):
    ref, line = target["reference"], target["line"]
    models = sorted(target["prob"].keys() - {ref})
    comparisons = max(1, len(models) * (2 if line is not None else 1))
    scopes = [("league_product", history)]
    if line is not None:
        scopes.insert(0, ("same_line", [r for r in history if r["line"] == line]))
    for scope, sample in scopes:
        scores = [s for model in models if (s := candidate_score(sample, model, ref, guarded, comparisons)) is not None]
        if scores:
            gain, _, model, n, changed = min(scores)
            return {"model": model, "scope": scope, "history_n": n, "history_changed": changed,
                    "history_accuracy_gain": -gain}
    return {"model": ref, "scope": "reference"}


def replay(rows):
    groups = defaultdict(list)
    for r in prepare(rows):
        groups[r["group"]].append(r)
    for offers in groups.values():
        offers.sort(key=lambda r: (r["day"], r["id"]))
        memo = {}
        for r in offers:
            if not "2025-01-01" <= r["day"] <= "2026-12-31":
                continue
            month = r["day"][:7]
            cutoff = (date.fromisoformat(month + "-01") - EVIDENCE_LAG).isoformat()
            key = (month, r["reference"], r["line"], tuple(sorted(r["prob"])))
            if key not in memo:
                history = [x for x in offers if x["day"] <= cutoff]
                memo[key] = {"accuracy": choose(history, r, False), "guarded": choose(history, r, True)}
            decision = {"market": {"model": "market", "scope": "market"},
                        "reference": {"model": r["reference"], "scope": "reference"}, **memo[key]}
            entry = {k: v for k, v in r.items() if k not in ("stats", "prob")}
            entry["cutoff"] = cutoff
            entry["policies"] = {
                policy: {**choice, **r["stats"][choice["model"]], "prob": r["prob"][choice["model"]]}
                for policy, choice in decision.items()
            }
            yield entry


def metrics(rows, policy):
    outcomes = [r["policies"][policy] for r in rows]
    n = len(outcomes)
    hits = sum(x["hit"] for x in outcomes)
    return {"n": n, "hits": hits, "accuracy": hits / n,
            "roi": math.fsum(x["net"] for x in outcomes) / n,
            "logloss": math.fsum(x["logloss"] for x in outcomes) / n,
            "switched_model": sum(x["model"] != r["policies"]["reference"]["model"]
                                  for r, x in zip(rows, outcomes, strict=True)),
            "changed_pick": sum(x["pick"] != r["policies"]["reference"]["pick"]
                                for r, x in zip(rows, outcomes, strict=True)),
            "models": dict(sorted(Counter(x["model"] for x in outcomes).items()))}


def paired_intervals(rows, policy):
    daily = defaultdict(lambda: [0, 0.0, 0.0])
    for r in rows:
        group = daily[r["day"]]
        a, b = (r["policies"][p] for p in (policy, "reference"))
        group[0] += 1
        group[1] += a["hit"] - b["hit"]
        group[2] += a["net"] - b["net"]
    if len(daily) < 2:
        return None
    values = np.asarray([daily[d] for d in sorted(daily)])
    n = len(values)
    width = min(n, 7)
    rng = np.random.default_rng(20261110)
    starts = rng.integers(0, n, size=(1000, math.ceil(n / width)))
    blocks = ((starts[:, :, None] + np.arange(width)) % n).reshape(1000, -1)[:, :n]
    sums = values[blocks].sum(axis=1)
    intervals = np.percentile(sums[:, 1:] / sums[:, :1], [2.5, 97.5], axis=0)
    return {"accuracy": intervals[:, 0].tolist(), "roi": intervals[:, 1].tolist()}


def summarize(entries):
    groupings = defaultdict(list)
    for r in entries:
        sport, league, market, period, outcomes = r["group"]
        for key in ("all", sport, f"year/{r['day'][:4]}", f"{sport}/year/{r['day'][:4]}",
                    f"{sport}/{market}", f"{sport}/{league}/{market}/{period}/{outcomes}"):
            groupings[key].append(r)
    return {key: {
        "range": [min(r["day"] for r in rows), max(r["day"] for r in rows)],
        "policies": {p: metrics(rows, p) for p in POLICIES},
        "paired_95": {p: paired_intervals(rows, p) for p in ("accuracy", "guarded")}
        if key in ("all", "sc", "bs", "bk", "vl") else {},
    } for key, rows in sorted(groupings.items())}


def main():
    import kbl_archive
    import primary_archive
    import side_archive

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    audit = {}
    for source in (primary_archive, side_archive, kbl_archive):
        source_rows = list(source.rows(ROOT))
        audit[source.__name__] = dict(sorted(Counter(f"{r['sport']}/{r['market']}" for r in source_rows).items()))
        rows.extend(source_rows)
    entries = sorted(replay(rows), key=lambda r: (r["day"], r["id"]))
    body = b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode() for r in entries)
    summary = {"input_rows": audit, "ledger_sha256_uncompressed": hashlib.sha256(body).hexdigest(),
               "primary_source_audit": primary_archive.audit_counts(ROOT),
               "primary_source_hashes": primary_archive.audit_metadata(ROOT),
               "model_choices": {
                   policy: dict(Counter(r["policies"][policy]["scope"] for r in entries))
                   for policy in ("accuracy", "guarded")
               },
               "groups": summarize(entries)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "ledger.jsonl.gz").write_bytes(gzip.compress(body, mtime=0))
    (args.out / "summary.json").write_bytes(
        (json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    )
    for name in ("all", "sc", "bs", "bk", "vl"):
        if name in summary["groups"]:
            print(name, [(p, m["hits"], m["n"], round(m["roi"] * 100, 2), m["changed_pick"])
                         for p, m in summary["groups"][name]["policies"].items()])


if __name__ == "__main__":
    main()
