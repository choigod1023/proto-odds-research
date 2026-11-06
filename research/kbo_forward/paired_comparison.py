"""Compare #612 KBO revisions against the exact saved T-30 featured pick.

Run from the repository root:
    python -m research.kbo_forward.paired_comparison --state <collector-state> \
        --operating-ledger <read-only complete pregame JSONL or JSONL.gz export>

The challenger is the saved frozen M0 baseline for the revised lineup, using
the existing ledger's two-way decision rule. The opt-in runner saves revisions
under lineup-m0-captures. Both sides use the same exact
Betman product, full odds and official KBO final score for paper grading.
Totals and handicaps require their own prospective model capture contract.
"""
import argparse
import base64
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from . import ledger
from .lineup_m0 import CAPTURE_DIR, capture_path, revision_identity
from .lineup_revisions import CUTOFF, MAX_GAP, replay
from .official import LINEUP, SCHEDULE, SourceError, parse_lineup
from research.m1_service.observer import _verify_archives, _verify_market_bytes

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ai_decision import event_id, offer_id, selection_id
from featured_pick import VERSION as FEATURED_VERSION
from prediction_ledger import PredictionLedger
from public_tipster_votes import team

SIDES = {"홈": "home", "원정": "away"}


class Excluded(ValueError):
    """A missing or unproven input for a comparison arm."""


def read_operating_export(path):
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("incomplete operating ledger export")
    records = [json.loads(line) for line in raw.splitlines()]
    # The normal constructor can open the production DB from the environment.
    # Use the same chain verifier against only the supplied in-memory export.
    verifier = object.__new__(PredictionLedger)
    verifier._database = SimpleNamespace(prediction_records=lambda: records)
    return verifier._read_verified_storage()


def event_key(event):
    return event["season"], event["official_game_id"]


def fixture_key(event):
    return (event["season"], ledger.MODEL_TEAM_NAMES[event["home_id"]],
            ledger.MODEL_TEAM_NAMES[event["away_id"]], ledger.moment(event["kickoff_at"]))


def operating_index(records):
    fixtures, by_event = defaultdict(set), defaultdict(list)
    for record in records:
        if record["record_type"] != "prediction":
            continue
        fixture = record["features"].get("fixture") or {}
        if (fixture.get("sport"), fixture.get("league")) == ("bs", "KBO"):
            key = (fixture.get("year"), team(fixture.get("home"), "KBO"),
                   team(fixture.get("away"), "KBO"), ledger.moment(record["kickoff"]))
            fixtures[key].add(record["event_id"])
            by_event[record["event_id"]].append(record)
    return fixtures, by_event


def existing_pick(row, index):
    fixtures, by_event = index
    event_ids = fixtures.get(fixture_key(row["event"]), set())
    if not event_ids:
        raise Excluded("operating_fixture_missing")
    if len(event_ids) != 1:
        raise Excluded("operating_fixture_ambiguous")
    event, = event_ids
    market = row["market"]
    kickoff = ledger.moment(row["event"]["kickoff_at"])
    cutoff = kickoff - CUTOFF
    eligible = []
    for record in by_event[event]:
        observed, as_of, captured = (
            ledger.moment(record[k]) for k in ("market_observed_at", "as_of", "captured_at")
        )
        if observed <= as_of <= captured < cutoff:
            eligible.append((as_of, record["ledger_sequence"], captured, record))
    if not eligible:
        raise Excluded("operating_pre_t30_revision_missing")
    _, _, captured, record = max(eligible)
    if ledger.moment(record["kickoff"]) != kickoff:
        raise Excluded("operating_frozen_revision_fixture_mismatch")
    if captured >= min(ledger.moment(market[k]) for k in ("close_at", "round_close_at")):
        raise Excluded("operating_capture_after_market_close")
    pick = record["predictions"].get("featured_pick") or {}
    if pick.get("version") != FEATURED_VERSION:
        raise Excluded("operating_frozen_featured_pick_missing")
    fixture = record["features"]["fixture"]
    _, round_id, number = market["market_id"].split(":")
    selection = SIDES.get(pick.get("selection"))
    if (record["event_id"] != event_id(fixture)
            or fixture["round"] != int(round_id[2:]) or pick.get("game_no") != number
            or pick.get("market") != "승패" or pick.get("label") != "" or pick.get("line") is not None
            or pick.get("settlement_rule") != "official_winner" or selection is None):
        raise Excluded("operating_pick_different_offer_or_terms")
    options = record["features"].get("options") or []
    for label, side in SIDES.items():
        option = {"market": "승패", "label": "", "line": None, "선택": label, "게임번호": number}
        ids = (selection_id(fixture, option), offer_id(fixture, option))
        saved = [o for o in options if (o.get("selection_id"), o.get("offer_id")) == ids]
        if len(saved) != 1 or saved[0].get("odds") != market["odds"][side]:
            raise Excluded("operating_offer_roster_or_odds_mismatch")
        if side == selection and (ids != (pick.get("selection_id"), pick.get("offer_id"))
                                  or pick.get("odds") != market["odds"][side]):
            raise Excluded("operating_pick_identity_or_odds_mismatch")
    return {"pick": selection, "odds": pick["odds"], "snapshot_id": record["snapshot_id"],
            "record_hash": record["record_hash"], "captured_at": record["captured_at"]}


def revised_m0(row, captures, state):
    market, after = row["market"], row["after"]
    revision = revision_identity(row["event"], after["lineup"])
    path = capture_path(state, revision, market["market_id"])
    try:
        sidecar = ledger.read(path)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise Excluded(f"m0_revision_ledger_unverified: {exc}") from exc
    if path.is_file() and (len(sidecar) != 1 or sidecar[0]["type"] != "capture"
                           or sidecar[0]["input"].get("lineup_revision") != revision):
        raise Excluded("m0_revision_ledger_identity_mismatch")
    priced = sidecar or [c for c in captures if c["type"] == "capture"]
    if not priced:
        raise Excluded("m0_only_unpriced_capture" if captures else "m0_frozen_capture_missing")
    matched = [c for c in priced if c["input"]["market"]["market_id"] == market["market_id"]]
    if len(matched) != 1:
        raise Excluded("m0_exact_offer_ambiguous" if matched else "m0_exact_offer_capture_missing")
    capture, = matched
    payload = capture["input"]
    if (event_key(payload["event"]) != event_key(row["event"])
            or fixture_key(payload["event"]) != fixture_key(row["event"])):
        raise Excluded("m0_fixture_mismatch")
    started, frozen = (ledger.moment(capture[k]) for k in ("received_at", "frozen_at"))
    if not started <= frozen <= ledger.moment(row["event"]["kickoff_at"]) - CUTOFF:
        raise Excluded("m0_capture_time_invalid_or_after_t30")
    if frozen < ledger.moment(after["lineup"]["received_at"]):
        raise Excluded("m0_frozen_before_revised_lineup")
    if frozen >= min(ledger.moment(market[k]) for k in ("close_at", "round_close_at")):
        raise Excluded("m0_capture_after_market_close")
    saved = payload["market"]
    if (any(saved.get(k) != market[k] for k in
            ("kind", "settlement_rule", "line", "source_product", "odds"))
            or any(ledger.moment(saved[k]) != ledger.moment(market[k])
                   for k in ("close_at", "round_close_at"))):
        raise Excluded("m0_market_terms_or_odds_mismatch")
    price = payload["evidence"]["odds"]
    quote_at = ledger.moment(price["received_at"])
    if not timedelta(0) <= started - quote_at <= MAX_GAP:
        raise Excluded("m0_price_receipt_stale_or_after_capture")
    try:
        if (capture["input_sha256"] != ledger.digest(ledger.canonical(payload))
                or capture["contract_sha256"] != ledger.contract_sha()
                or capture["source_policy"] != ledger.POLICY):
            raise ValueError("input or contract digest/policy mismatch")
        ledger.validate_model_snapshot(payload, started)
        if any(payload["row"]["history_coverage"][s]["status"] != "complete" for s in SIDES.values()):
            raise ValueError("as-of team/player history coverage incomplete")
        official = state / "official"
        _verify_archives(payload, official, started)
        _verify_market_bytes(payload, state / "prices/price-evidence")
        ref = payload["evidence"]["announcement"]["body"]
        raw = gzip.decompress((official / ref["relative_file"]).read_bytes())
        if ledger.digest(raw) != ref["archived_canonical_bundle_sha256"]:
            raise ValueError("announcement archive digest mismatch")
        bundle = json.loads(raw)
        lineup, = [r for r in bundle["responses"] if r["receipt"]["url"] == LINEUP]
        schedule, = [r for r in bundle["responses"] if r["receipt"]["url"] == SCHEDULE]
        game, = [g for g in json.loads(base64.b64decode(schedule["body_base64"]))["game"]
                 if g["G_ID"] == row["event"]["official_game_id"]]
        batting = parse_lineup(base64.b64decode(lineup["body_base64"]), game)
        ledger.validate_forecasts(capture["models"], payload["row"])
        replayed, model_hashes = ledger.infer_frozen(payload["row"])
        q = capture["models"]["baseline"]["probabilities_2way"]
        if capture["model_sha256"] != model_hashes or any(
            abs(q[k] - replayed["baseline"]["probabilities_2way"][k]) > 1e-9 for k in q
        ):
            raise ValueError("frozen model digest or prediction differs")
    except (ValueError, KeyError, TypeError, OSError, SourceError) as exc:
        raise Excluded(f"m0_archived_input_or_model_unverified: {exc}") from exc
    if (ledger.moment(lineup["receipt"]["finished_at"]) < ledger.moment(after["lineup"]["received_at"])
            or any(payload["row"]["roster"][s]["starter"] != after["starters"][s]["player_id"]
                   or [p["name"] for p in batting[s]] != [p["name"] for p in after["batting_order"][s]]
                   for s in q)):
        raise Excluded("m0_revised_roster_receipt_mismatch")
    pick = max(("home", "away"), key=q.get)
    return {"pick": pick, "odds": market["odds"][pick], "probability": q[pick],
            "record_hash": capture["hash"], "frozen_at": capture["frozen_at"],
            "model_sha256": model_hashes["baseline"],
            "source": "lineup_revision_ledger" if sidecar else "original_m0_ledger"}


def compare(replay, operating, m0_records, state):
    if replay["schema"] != "kbo-lineup-revisions-v1":
        raise ValueError("unsupported #612 replay schema")
    indexed, captures = operating_index(operating or []), defaultdict(list)
    for rec in m0_records:
        if rec["type"] in {"capture", "unpriced_forecast"}:
            captures[event_key(rec["input"]["event"])].append(rec)

    # Multiple lineup changes can produce multiple #612 rows per offer. Fix the
    # last eligible revision before looking for either arm or grading the result.
    latest = {}
    for row in sorted(replay["rows"], key=lambda r: ledger.moment(r["after"]["lineup"]["received_at"])):
        latest[event_key(row["event"]), row["market"]["market_id"]] = row
    matched, excluded = [], []
    reasons = Counter()
    for row in latest.values():
        ev, market = row["event"], row["market"]
        identity = {"event": ev, "market_id": market["market_id"], "kind": market["kind"]}
        missing, arms = {}, {}
        if market["kind"] != "full_game_2way":
            missing["product"] = "m0_prospective_product_unsupported"
        else:
            for name, fn in (
                ("existing_pick", lambda: existing_pick(row, indexed)),
                ("revised_m0", lambda: revised_m0(row, captures[event_key(ev)], state)),
            ):
                try:
                    arms[name] = fn()
                except Excluded as exc:
                    missing[name] = ("operating_ledger_export_missing"
                                     if name == "existing_pick" and operating is None else str(exc))
        if missing:
            reasons.update(reason.split(": ", 1)[0] for reason in missing.values())
            excluded.append(identity | {"missing": missing})
            continue
        result = row["official_result"]
        home, away = result["home_score"], result["away_score"]
        for arm in arms.values():
            grade = "refund" if home == away else "hit" if arm["pick"] == (
                "home" if home > away else "away"
            ) else "miss"
            arm.update(grade=grade, net_units=str(
                Decimal(str(arm["odds"])) - 1 if grade == "hit" else Decimal(0 if grade == "refund" else -1)
            ))
        matched.append(identity | {"market": market, "official_result": result, "arms": arms,
                                   "prior_lineup": row["before"]["lineup"],
                                   "revised_lineup": row["after"]["lineup"]})
    metrics = {}
    for name in ("existing_pick", "revised_m0"):
        grades = [r["arms"][name] for r in matched]
        n = len(grades)
        hits = sum(a["grade"] == "hit" for a in grades)
        decided = sum(a["grade"] != "refund" for a in grades)
        net = sum((Decimal(a["net_units"]) for a in grades), Decimal(0))
        metrics[name] = {"n": n, "hits": hits, "refunds": n - decided,
                         "hit_rate": hits / decided if decided else None, "net_units": str(net),
                         "unit_stake_paper_roi": float(net / n) if n else None}
    return {"schema": "kbo-revised-m0-vs-frozen-featured-v1", "source_counts": replay["counts"],
            "status": "compared" if matched else "missing_pair_inputs" if latest else "no_612_eligible_rows",
            "operating_export_supplied": operating is not None,
            "revision_capture_directory_present": (state / CAPTURE_DIR).is_dir(),
            "m0_priced_captures": sum(r["type"] == "capture" for r in m0_records),
            "m0_unpriced_forecasts": sum(r["type"] == "unpriced_forecast" for r in m0_records),
            "unique_eligible_offers": len(latest), "paired_offers": len(matched),
            "paired_games": len({event_key(r["event"]) for r in matched}),
            "exclusion_counts": dict(reasons), "excluded": excluded, "metrics": metrics, "rows": matched}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--operating-ledger", type=Path)
    parser.add_argument("--as-of", type=ledger.moment, default=datetime.now(timezone.utc))
    args = parser.parse_args()
    operating = read_operating_export(args.operating_ledger) if args.operating_ledger else None
    m0 = ledger.read(args.state / "paired-ledger.jsonl")
    output = compare(replay(args.state, args.as_of), operating, m0, args.state)
    print(json.dumps(output | {"as_of": args.as_of.isoformat()}, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
