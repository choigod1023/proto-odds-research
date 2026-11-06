"""Offline comparison of one frozen K League pick and same-response Proto markets."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import timedelta
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
import zlib

from ai_decision import event_id, offer_id, selection_id
from bets import SEL_NAMES, winner_index
from devig import market_probabilities, multiplicative
from featured_pick import VERSION
from live_market_refresh import KST, _aware_timestamp, _game_kickoff
from multi_market_recommendations import official_options
from odds_live import LINE, _clean_team
from prediction_ledger import PredictionLedger
from prediction_runtime import _latest_predictions
from snapshot import (KLEAGUES, KLEAGUE_RECEIPT_STREAM, MAX_RECEIPT_BODY, OUT,
                      RECEIPT_BODY_DIR, kleague_snapshot_rows, receipt_body_slot)
from wisetoto import VOID_RESULTS, _decode, parse_rows


RESULTS = {
    "승무패": ("홈승", "무승부", "홈패"),
    "핸디캡": ("핸디승", "핸디무", "핸디패"),
    "언더오버": ("언더", "오버"),
}


def _fixture(row, year, rnd, date):
    if row.get("sport") != "sc" or row.get("league") not in KLEAGUES:
        return None
    if type(year) is not int or type(rnd) is not int:
        return None
    home, away = (_clean_team(row.get(team)) for team in ("home", "away"))
    kickoff = _game_kickoff(dict(year=year, round=rnd, date=date))
    if home and away and home != away and kickoff is not None:
        return year, rnd, row["league"], kickoff.replace(tzinfo=KST), home, away
    return None


def _product(row):
    family, ways = row.get("market"), row.get("n_way")
    if family not in RESULTS or ways != len(RESULTS[family]):
        return None
    number, label = str(row.get("game_no") or ""), row.get("label") or ""
    if not (number.isascii() and number.isdigit()):
        return None
    if family == "승무패":
        return (number, family, label, None, "official_winner") if label == "" else None
    match = LINE.search(label)
    if match is None:
        return None
    line = float(match.group())
    option = dict(market=family, label=label, line=line, n_way=ways)
    try:
        rule = official_options([option] * ways, "sc", "K League")[0]["settlement_rule"]
    except (ValueError, OverflowError):
        return None
    return number, family, label, line, rule


def _receipt_evidence(receipt, instant, bodies):
    """Reparse retained exact bytes when the bounded local slot still holds them."""
    if bodies is None:
        return "parsed_only"
    digest = receipt["body_sha256"]
    archived_body = bodies / (digest + ".html.gz")
    path = archived_body if archived_body.is_file() else receipt_body_slot(bodies, digest)
    try:
        with gzip.open(path, "rb") as handle:
            body = handle.read(MAX_RECEIPT_BODY + 1)
    except FileNotFoundError:
        return "parsed_only"
    except (OSError, EOFError, zlib.error):
        return "raw_unreadable"
    if len(body) > MAX_RECEIPT_BODY or hashlib.sha256(body).hexdigest() != receipt["body_sha256"]:
        return "parsed_only"  # A different body may have reused this slot.
    html = _decode(SimpleNamespace(content=body, text=body.decode("utf-8", errors="replace")))
    parsed = parse_rows(html, receipt["year"], receipt["round"])
    rows = kleague_snapshot_rows(parsed, instant.isoformat(timespec="seconds"))
    return "raw_verified" if rows == receipt["rows"] else "raw_row_mismatch"


def _observations(receipts, counts, bodies):
    observed = defaultdict(list)
    for receipt in receipts:
        instant = _aware_timestamp(receipt.get("received_at"))
        year, rnd = receipt.get("year"), receipt.get("round")
        digest = receipt.get("body_sha256")
        if (receipt.get("timestamp_basis") != "http_response_body_completed"
                or instant is None or type(year) is not int or type(rnd) is not int
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(receipt.get("rows"), list)):
            counts["unverified_receipt"] += 1
            continue
        counts["body_completion_receipts"] += 1
        evidence = _receipt_evidence(receipt, instant, bodies)
        counts[evidence] += 1
        groups = defaultdict(list)
        rows = receipt["rows"]
        game_numbers = Counter(row.get("game_no") for row in rows)
        for row in rows:
            if (row.get("year"), row.get("round")) != (year, rnd):
                continue
            fixture = _fixture(row, year, rnd, row.get("date_text"))
            if fixture is not None:
                groups[fixture].append(dict(row, ambiguous=game_numbers[row["game_no"]] != 1))
        for fixture, rows in groups.items():
            observed[fixture].append((instant, digest, evidence, rows))
    return observed


def _products(rows):
    markets = {}
    terms = set()
    for row in rows:
        product = _product(dict(row, market=row.get("market_family"), label=row.get("market_label")))
        if product is None:
            continue
        _, family, _, line, _ = product
        if row["ambiguous"] or (family, line) in terms:
            return {}
        terms.add((family, line))
        if row.get("result") != "경기전":
            continue
        try:
            prices = [float(value) for value in row["odds"].split(",")]
            if len(prices) != len(RESULTS[family]):
                continue
            method = market_probabilities if len(prices) == 3 else multiplicative
            probabilities = method(prices)
        except (ValueError, ArithmeticError):
            continue
        markets[product] = (prices, probabilities)
    return markets if {product[1] for product in markets} == RESULTS.keys() else {}


def _official_results(documents):
    """Use saved Proto result rows only. Published prices are irrelevant here."""
    results = {}
    for document in documents:
        published = _aware_timestamp(document.get("generated_at"))
        if published is None:
            continue
        year = published.astimezone(KST).year
        for rnd, bucket in document.get("markets", {}).items():
            batch = defaultdict(list)
            for row in bucket.values():
                fixture = _fixture(row, year, int(rnd), row.get("date"))
                product = _product(row)
                if (fixture is not None and product
                        and timedelta(0) < published - fixture[3] <= timedelta(days=90)):
                    batch[fixture, product].append(row.get("result"))
            for key, values in batch.items():
                # The feed may republish a carried-forward result. Its document
                # time cannot order conflicting official product outcomes.
                family = key[1][1]
                result = values[0]
                if len(values) == 1 and result not in RESULTS[family] and result not in VOID_RESULTS:
                    continue
                previous_time, prior = results.get(key, (published, set()))
                prior.add(result if len(values) == 1 else None)
                results[key] = max(published, previous_time), prior
    return results


def _compare_products(markets, results, fixture, pick, game):
    rows, selected = [], None
    for product, (prices, probabilities) in markets.items():
        number, family, label, line, rule = product
        published, outcomes = results.get((fixture, product), (None, set()))
        if len(outcomes) != 1:
            return None, "official_result_missing_or_ambiguous"
        result = next(iter(outcomes))
        if result not in RESULTS[family] and result not in VOID_RESULTS:
            return None, "official_result_missing_or_ambiguous"
        names = SEL_NAMES[family, len(prices)]
        winner = None if result in VOID_RESULTS else names[winner_index(len(prices), result)]
        favorite = names[max(range(len(prices)), key=probabilities.__getitem__)]
        favorite_hit = favorite == winner if winner is not None else None
        row = dict(game_no=number, market=family, label=label, line=line, period="regulation",
                   settlement_rule=rule, odds=dict(zip(names, prices)),
                   probabilities=dict(zip(names, probabilities)), official_result=result,
                   result_published_at=published.isoformat(), winner=winner,
                   market_favorite=favorite, market_favorite_hit=favorite_hit)
        rows.append(row)
        if (str(pick.get("game_no")), pick.get("market"), pick.get("label") or "",
                pick.get("line"), pick.get("settlement_rule")) != product:
            continue
        side = pick.get("selection")
        option = {"게임번호": number, "market": family, "label": label, "line": line, "선택": side}
        if (side not in names or pick.get("odds") != row["odds"][side]
                or pick.get("offer_id") != offer_id(game, option)
                or pick.get("selection_id") != selection_id(game, option)):
            return None, "frozen_offer_or_price_mismatch"
        selected = dict(game_no=number, selection=side, odds=row["odds"][side],
                        hit=side == winner if winner is not None else None,
                        market_favorite_hit=favorite_hit)
    if selected is None:
        return None, "frozen_offer_or_price_mismatch"
    return dict(products=rows, frozen_pick=selected), None


def compare(receipts, ledger_records, result_documents, *, bodies=None):
    """One row per fixture using the same response, frozen offer and official products."""
    counts = Counter()
    observations = _observations(receipts, counts, bodies)
    results = _official_results(result_documents)
    predictions = defaultdict(list)
    for event, prediction in _latest_predictions(ledger_records).items():
        game = prediction.get("features", {}).get("fixture") or {}
        fixture = _fixture(game, game.get("year"), game.get("round"), game.get("date"))
        if fixture is not None and event_id(game) == event:
            predictions[fixture[2:]].append((event, prediction, game, fixture))
    counts["ledger_fixtures"] = len(predictions)
    rows = []
    for matches in predictions.values():
        if len(matches) != 1:
            counts["ambiguous_ledger_fixture"] += 1
            continue
        event, prediction, game, fixture = matches[0]
        pick = prediction.get("predictions", {}).get("featured_pick") or {}
        if pick.get("version") != VERSION:
            counts["no_frozen_pick"] += 1
            continue
        kickoff = _aware_timestamp(prediction.get("kickoff"))
        as_of, captured, market_time = (_aware_timestamp(prediction.get(field))
                                        for field in ("as_of", "captured_at", "market_observed_at"))
        if (kickoff != fixture[3] or None in (as_of, captured, market_time)
                or not market_time <= as_of <= captured < kickoff - timedelta(minutes=30)):
            counts["invalid_frozen_timing"] += 1
            continue
        available = [entry for entry in observations.get(fixture, ()) if entry[0] <= market_time]
        if not available:
            counts["no_verified_pregame_receipt"] += 1
            continue
        latest = max(available, key=lambda entry: entry[0])
        instant, digest, evidence, prices = latest
        if any(other[0] == instant and other != latest for other in available):
            counts["ambiguous_receipt"] += 1
            continue
        if evidence in {"raw_row_mismatch", "raw_unreadable"}:
            counts[f"ineligible_{evidence}"] += 1
            continue
        markets = _products(prices)
        if not markets:
            counts["incomplete_or_ambiguous_three_markets"] += 1
            continue
        comparison, reason = _compare_products(markets, results, fixture, pick, game)
        if reason:
            counts[reason] += 1
            continue
        rows.append(dict(comparison, league=game["league"], event_id=event, year=game["year"],
                         round=game["round"], kickoff=prediction["kickoff"],
                         prediction_snapshot_id=prediction["snapshot_id"],
                         captured_at=prediction["captured_at"], received_at=instant.isoformat(),
                         body_sha256=digest, receipt_evidence=evidence))
    decided = [row["frozen_pick"] for row in rows if row["frozen_pick"]["hit"] is not None]
    rates = {arm: sum(pick[field] for pick in decided) / len(decided)
             for arm, field in (("frozen_pick", "hit"), ("same_offer_market_favorite", "market_favorite_hit"))
             } if decided else None
    return dict(eligible_fixtures=len(rows), decided_fixtures=len(decided),
                counts=dict(counts), hit_rates=rates, rows=rows)


def _receipts(paths, database):
    if database:
        # An explicit local SQLite copy, opened without RuntimeDatabase initialization.
        uri = database.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            for (payload,) in connection.execute(
                    "SELECT payload_json FROM event_records WHERE stream=?", (KLEAGUE_RECEIPT_STREAM,)):
                yield json.loads(payload)
        return
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                yield json.loads(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--receipts", type=Path, nargs="+", help="Saved K League receipt JSONL shards")
    inputs.add_argument("--receipt-db", type=Path, help="Local SQLite copy holding the receipt stream")
    parser.add_argument("--receipt-bodies", type=Path, help="Bounded local raw-body gzip directory")
    parser.add_argument("--ledger", type=Path, required=True, help="Exported immutable prediction ledger JSONL")
    parser.add_argument("--results", type=Path, nargs="+", required=True, help="Saved Proto live_odds JSON files")
    args = parser.parse_args()
    if os.environ.get("PROODD_DB_PATH"):
        parser.error("unset PROODD_DB_PATH to read the explicit local inputs")
    if not args.ledger.is_file():
        parser.error("--ledger must be an existing file")
    paths = args.receipts or sorted(OUT.glob(f"{KLEAGUE_RECEIPT_STREAM}_*.jsonl"))
    bodies = args.receipt_bodies
    if bodies is None and args.receipts is None and args.receipt_db is None:
        bodies = OUT / RECEIPT_BODY_DIR
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in args.results]
    ledger = PredictionLedger(args.ledger).records()
    report = compare(_receipts(paths, args.receipt_db), ledger, documents, bodies=bodies)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
