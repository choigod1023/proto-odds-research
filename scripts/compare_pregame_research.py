"""Audit exported research JSONL; compare supplied forecasts, never invent them.

Challenger JSONL schema: pair_id, pair_datahash, model_id, probability,
training_cutoff, predicted_at, features_as_of, provenance (protocol:
temporal_holdout_v1 or walk_forward_v1, artifact_sha256, config_sha256).
Metadata checks are not independent proof of model provenance or profitability.
Latest pair per event/selection is chosen BEFORE looking at outcomes. Replay is
a retrospective latest-snapshot scenario, not a claim about bets actually made.
"""
from collections import Counter, defaultdict
from datetime import timedelta
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_recommendation_shadow import replay, stamp

MAX_ROWS = 2000  # replay enumerates pairs; deliberately bounded local batch
MAX_LINE = 262144
MAX_FILE = 64 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_jsonl(path):
    """Fail on oversized input, not silently report a truncated sample."""
    if Path(path).stat().st_size > MAX_FILE:
        raise ValueError("input exceeds file limit")
    rows, total = [], 0
    with Path(path).open("rb") as stream:
        while line := stream.readline(MAX_LINE + 1):
            total += len(line)
            if len(line) > MAX_LINE or total > MAX_FILE:
                raise ValueError("input exceeds byte limit")
            if line.strip():
                if len(rows) >= MAX_ROWS:
                    raise ValueError("input exceeds row limit; use an explicit bounded cohort")
                rows.append(json.loads(line))
    return rows


def finite(value, low, high=None):
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    number = float(value)
    if not math.isfinite(number) or number < low or (high is not None and number > high):
        raise ValueError("invalid probability/odds")
    return number


def pair_hash(pair):
    payload = {k: pair[k] for k in ("source", "odds", "baseline_decision")}
    if "evidence_refs" in pair:
        payload["evidence_refs"] = pair["evidence_refs"]
    return sha(payload)


def official_result(record, pair, cutoff, official_sources):
    """Only exact selected-market results; no score/selection inference."""
    eligible = {}
    for result in record.get("results", []):
        try:
            if any(result[k] != pair[k] for k in ("event_id", "snapshot_id", "selection_id")):
                continue
            source = result["source"]
            name = source.get("name") if isinstance(source, dict) else source
            if name not in official_sources:
                continue
            at = max(stamp(result["observed_at"]), stamp(result["captured_at"]))
            if not stamp(pair["kickoff"]) <= at <= cutoff:
                continue
            outcome = result["outcome"]
            if outcome.get("selection_id", pair["selection_id"]) != pair["selection_id"]:
                continue
            if outcome.get("result") not in ("hit", "miss", "void"):
                continue
            if name not in eligible or at > eligible[name][0]:
                eligible[name] = (at, outcome)
            elif at == eligible[name][0] and outcome != eligible[name][1]:
                return None, None, "conflicting_result"
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    if not eligible:
        return None, None, "pending_or_unverifiable_result"
    if len({canonical(v[1]) for v in eligible.values()}) != 1:
        return None, None, "conflicting_result"
    return next(iter(eligible.values()))[1]["result"], max(v[0] for v in eligible.values()), None


def metrics(rows):
    settled = [r for r in rows if r.get("result") in ("hit", "miss")]
    n = len(settled)
    return {"opportunities": len(rows), "settled_binary": n,
            "pending": sum(r.get("result") is None for r in rows),
            "void": sum(r.get("result") == "void" for r in rows),
            "hit_rate": sum(r["result"] == "hit" for r in settled) / n if n else None,
            "brier": sum((r["p"] - (r["result"] == "hit")) ** 2 for r in settled) / n if n else None,
            "flat_single_roi": sum(r["o"] * (r["result"] == "hit") - 1 for r in settled) / n if n else None}


def by_sport(rows):
    sports = sorted({r["sport"] for r in rows})
    return {sport: metrics([r for r in rows if r["sport"] == sport]) for sport in sports}


def wallets(rows, cutoff):
    # One wallet across sports. weekly=2 and weekly=3 are alternatives, not additive.
    return {str(w): replay(rows, cutoff, weekly=w) for w in (2, 3)}


def compare(records, challengers=(), *, as_of, official_sources=("proto_official",)):
    if len(records) > MAX_ROWS or len(challengers) > MAX_ROWS:
        raise ValueError("cohort exceeds local replay limit")
    cutoff = stamp(as_of)
    excluded, warnings, latest, identities = Counter(), Counter(), {}, {}
    for record in records:
        try:
            pair = record["pair"]
            if type(pair["id"]) is not int or pair["id"] <= 0 or pair_hash(pair) != pair["datahash"]:
                raise ValueError("invalid pair identity/hash")
            signature = canonical(record)
            if pair["id"] in identities:
                if identities[pair["id"]] != signature:
                    raise RuntimeError("conflicting duplicate pair ID")
                continue
            identities[pair["id"]] = signature
            for key in ("event_id", "selection_id", "snapshot_id"):
                if not isinstance(pair[key], str) or not pair[key]:
                    raise ValueError("missing identity")
            captured, observed, kickoff = (stamp(pair[k]) for k in ("captured_at", "observed_at", "kickoff"))
            if observed > captured or captured > cutoff or captured >= kickoff - timedelta(minutes=30):
                raise ValueError("not available before cutoff/freeze")
            key = pair["event_id"], pair["selection_id"]
            rank = captured, observed, pair["id"], pair["datahash"]
            if key not in latest or rank > latest[key][0]:
                latest[key] = (rank, record)
        except (ValueError, KeyError, TypeError, AttributeError):
            excluded["invalid_or_unavailable_pair"] += 1
    rows, pairs = [], {}
    # No result-based fallback to an older pair, even if the latest pair is pending.
    for _rank, record in (latest[key] for key in sorted(latest)):
        pair = record["pair"]
        try:
            base = pair["baseline_decision"]
            p, odds = finite(base["probability"], 0, 1), finite(pair["odds"]["decimal"], 1.000001)
            if not isinstance(base["sport"], str) or not base["sport"]:
                raise ValueError("missing sport")
            result, at, flag = official_result(record, pair, cutoff, set(official_sources))
            if flag:
                warnings[flag] += 1
            warnings.update(record.get("incomplete_flags", []))
            row = {"id": str(pair["id"]), "event": pair["event_id"], "sport": base["sport"],
                   "t": stamp(pair["captured_at"]), "k": stamp(pair["kickoff"]), "s": at,
                   "p": p, "o": odds, "result": result, "result_source": "official" if result else None,
                   "round": str(base["round"]) if base.get("round") is not None else None,
                   "policy": "latest-captured-research-scenario"}
            rows.append(row)
            pairs[pair["id"]] = pair, row
        except (ValueError, KeyError, TypeError, AttributeError):
            excluded["unsupported_baseline_schema"] += 1
    # Missing round cannot be assumed compatible for two-leg placement.
    eligible_rows = [r for r in rows if r["round"] is not None]
    warnings["missing_round_wallet_excluded"] = len(rows) - len(eligible_rows)
    supplied, valid, rejections, model_keys = Counter(), defaultdict(list), defaultdict(Counter), set()
    for prediction in challengers:
        model = str(prediction.get("model_id") or "unknown")
        supplied[model] += 1
        try:
            pair, row = pairs[prediction["pair_id"]]
            if prediction["pair_datahash"] != pair["datahash"]:
                raise ValueError("pair_hash_mismatch")
            key = model, pair["id"]
            if key in model_keys:
                raise RuntimeError("multiple challenger forecasts for same model/pair; ranking forbidden")
            model_keys.add(key)
            probability = finite(prediction["probability"], 0, 1)
            training, predicted, features = (stamp(prediction[k]) for k in
                                             ("training_cutoff", "predicted_at", "features_as_of"))
            if not (training < stamp(pair["observed_at"]) and features <= stamp(pair["observed_at"])
                    and max(training, features, row["t"]) <= predicted <= cutoff
                    and predicted < row["k"] - timedelta(minutes=30)):
                raise ValueError("temporal_provenance_failed")
            provenance = prediction["provenance"]
            if provenance.get("protocol") not in ("temporal_holdout_v1", "walk_forward_v1"):
                raise ValueError("unsupported_model_provenance_unverified")
            for name in ("artifact_sha256", "config_sha256"):
                digest = provenance[name]
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("unsupported_model_provenance_unverified")
            valid[model].append((dict(row, t=predicted), dict(row, t=predicted, p=probability)))
        except ValueError as exc:
            rejections[model][str(exc)] += 1
        except (KeyError, TypeError, AttributeError):
            rejections[model]["missing_or_unmatched_provenance"] += 1
    model_reports = {}
    for model in sorted(supplied):
        ordered = sorted(valid[model], key=lambda ab: (ab[0]["event"], ab[0]["id"]))
        baseline = [a for a, b in ordered]
        challenger = [b for a, b in ordered]
        bw = wallets([r for r in baseline if r["round"] is not None], cutoff) if baseline else None
        cw = wallets([r for r in challenger if r["round"] is not None], cutoff) if challenger else None
        deltas = {str(w): (cw[str(w)]["roi"] - bw[str(w)]["roi"]
                           if cw and cw[str(w)]["roi"] is not None and bw[str(w)]["roi"] is not None
                           else None) for w in (2, 3)}
        model_reports[model] = {"supplied": supplied[model], "accepted": len(challenger),
            "rejected": dict(rejections[model]), "provenance_status": "self_reported_not_independently_verified",
            "matched_baseline_by_sport": by_sport(baseline), "challenger_by_sport": by_sport(challenger),
            "matched_baseline_wallets": bw, "challenger_wallets": cw, "roi_delta": deltas}
    return {"as_of": cutoff.isoformat(), "mode": "descriptive_latest_snapshot_replay_not_verified_profit",
            "selected_pairs": len(rows), "excluded": dict(excluded), "warnings": dict(warnings),
            "selected_pair_refs": [{"id": p["id"], "datahash": p["datahash"],
                                    "event_id": p["event_id"], "selection_id": p["selection_id"]}
                                   for p, r in pairs.values()],
            "baseline_by_sport": by_sport(rows), "baseline_wallets": wallets(eligible_rows, cutoff),
            "models": model_reports, "improvement_roi": None,
            "no_challenger_reason": "no_external_challenger_supplied" if not challengers else None,
            "limitations": ["No automatic best-model selection or production promotion.",
                "Forecast metadata is self-reported, not independent provenance verification.",
                "Same selection probabilities change Brier, not that selection's hit outcome.",
                "Latest pregame pair selection ignores outcomes; this is not historical live execution.",
                "Pending opportunities remain in wallets; settled-only ROI is not final cohort ROI.",
                "Each model and weekly frequency is an alternative wallet; never sum them.",
                "One shared sports budget per wallet: 10000 won/two legs, monthly loss cap 50000 including pending.",
                "Export holds latest result revisions; earlier as-of corrections cannot be reconstructed; remain pending."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--challenger", type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare(read_jsonl(args.export), read_jsonl(args.challenger) if args.challenger else [], as_of=args.as_of)
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
