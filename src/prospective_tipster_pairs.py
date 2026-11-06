"""Offline same-product evaluation of prospective public tipster receipts."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import re
import zlib

from scripts.collect_proto_comparison_prices import DETAIL, MAX_BODY_BYTES, normalize_v2
from scripts.collect_tipster_comparison_sources import link
from src.multisport_pregame_study import choose, moment
from src.public_tipster_votes import LEAGUES, kickoff as fixture_kickoff
from src.research_tipster_reposts import RECEIPT_CLOCK, article_evidence, compare_quote_votes

SC_RULE = "regulation_90_plus_stoppage"
RULE_SOURCE = "https://www.sportstoto.co.kr/terms_allocation.php"
SOCCER_PRODUCT = ("SC", "1", "1", "축구 승무패", "승무패")
SIDE_CODES = {"0": "home", "1": "draw", "2": "away"}
SIDE_LABELS = ("승", "무", "패")
FIXTURE_FIELDS = "leagueCode leagueName leagueShortName homeId awayId homeName awayName gameDate"
ARMS = ("existing_vote", "deduplicated_vote")
T30 = timedelta(minutes=30)


def round_id(value):
    if not re.fullmatch(r"[0-9]{6}", value):
        raise ValueError("explicit_six_digit_G101_round_required")
    return value


def milliseconds(value):
    if type(value) is not int:
        raise ValueError("official_millisecond_clock_required")
    return datetime.fromtimestamp(value / 1000, timezone.utc)


def fields(row, names):
    return tuple(row.get(name) for name in names.split())


def soccer_product(row):
    return (fields(row, "itemCode betId betTypId betNm betTypNm") == SOCCER_PRODUCT
            and row.get("gameSubject") in (None, "")
            and fields(row, "handi winHandi drawHandi loseHandi") == (0, 0, 0, 0)
            and fields(row, "live unsetSchedule unsetEndDate") == ("N", "N", "N"))


def fixture_identity(row):
    *names, timestamp = fields(row, FIXTURE_FIELDS)
    if not all(isinstance(v, str) and v.strip() for v in names) or row["homeId"] == row["awayId"]:
        raise ValueError("unverified_official_fixture_identity")
    return milliseconds(timestamp)


def league_name(row):
    short = row.get("leagueShortName")
    return LEAGUES.get(short, short) if isinstance(short, str) else None


def sale_reason(record, row, lottery, observed):
    if not soccer_product(row) or fields(row, "winTxt drawTxt loseTxt") != SIDE_LABELS:
        return "unknown_settlement_terms"
    if row.get("protoStatus") != "2" or fields(row, "gameReject buyReject") != ("0", "0"):
        return "product_not_confirmed_on_sale"
    try:
        fixture_identity(row)
        kickoff = moment(record["kickoff"])
        opened, round_close = (milliseconds(lottery[key]) for key in ("saleStartDate", "saleEndDate"))
        close = milliseconds(row["endDate"])
    except (KeyError, ValueError, OverflowError, OSError):
        return "unverified_fixture_or_sale_clock"
    if (record["timing_basis"] != "actual_receipt" or lottery.get("saleProgress") is not True
            or not opened <= observed < min(close, round_close) or close > kickoff):
        return "outside_verified_sale_window"
    if not kickoff - timedelta(hours=24) <= observed < kickoff - T30:
        return "outside_quote_window"
    return None


def read_snapshot(path, round_number):
    meta = json.loads(path.read_bytes())
    request = meta.get("request_json") or {}
    if (meta.get("http_status") != 200 or meta.get("url") != DETAIL
            or meta.get("timestamp_basis") != RECEIPT_CLOCK or meta.get("legacy_cache")
            or (request.get("gmId"), request.get("gmTs")) != ("G101", round_number)):
        raise ValueError("unverified_explicit_round_receipt")
    body = (path.parent / meta["body_file"]).resolve()
    if not body.is_relative_to(path.parent.resolve()):
        raise ValueError("round_body_path_escapes_archive")
    with gzip.open(body, "rb") as handle:
        raw = handle.read(MAX_BODY_BYTES + 1)
    if len(raw) > MAX_BODY_BYTES or hashlib.sha256(raw).hexdigest() != meta["body_sha256"]:
        raise ValueError("round_body_hash_or_size_mismatch")
    observed = moment(meta["received_at"])
    data = json.loads(raw)
    lottery = data["currentLottery"]
    if (str(data["gmTs"]), lottery["gmId"], str(lottery["gmTs"])) != (round_number, "G101", round_number):
        raise ValueError("official_round_identity_mismatch")
    schedules = data["compSchedules"]
    keys = schedules["keys"]
    if len(keys) != len(set(keys)) or any(len(v) != len(keys) for v in schedules["datas"]):
        raise ValueError("invalid_round_schedule")
    rows = {}
    for values in schedules["datas"]:
        row = dict(zip(keys, values))
        number = row.get("matchSeq")
        if type(number) is not int or number <= 0 or number in rows:
            raise ValueError("invalid_or_duplicate_official_product_id")
        rows[number] = row
    return observed, meta, data, rows


def quote_candidates(snapshot):
    observed, meta, data, rows = snapshot
    catalog, eligible, held = [], [], []
    catalog_numbers = set()
    for record in normalize_v2(data, meta, date.min, date.max, modern_soccer_leagues=True):
        terms = record["payload"]
        product = terms["product_id"]
        number = int(product.rsplit(":", 1)[1])
        row = rows[number]
        reason = sale_reason(record, row, data["currentLottery"], observed)
        if reason == "unknown_settlement_terms":
            held.append((product, reason))
            continue
        terms.update(settlement_rule=SC_RULE, settlement_period="regulation", settlement_rule_source=RULE_SOURCE)
        record["league"] = league_name(row) or record["league"]
        if reason:
            held.append((product, reason))
        else:
            terms["sale_evidence"] = dict(
                row_status=row["protoStatus"], round_status=data["currentLottery"].get("saleStatus"),
                product_close=milliseconds(row["endDate"]).isoformat(),
                round_close=milliseconds(data["currentLottery"]["saleEndDate"]).isoformat())
            eligible.append(record)
        catalog.append(record)
        catalog_numbers.add(number)
    # Keep other exact soccer winner rows in the linkage universe even if a
    # suspension, unpublished price or void prevented their normalization.
    for number, row in rows.items():
        if not soccer_product(row) or number in catalog_numbers:
            continue
        try:
            kickoff = fixture_identity(row)
        except (ValueError, KeyError, OverflowError, OSError):
            continue
        product = f"G101:{data['gmTs']}:{number}"
        catalog.append(dict(kind="price", sport="sc", league=league_name(row), home=row["homeName"],
                            away=row["awayName"], kickoff=kickoff.isoformat(),
                            payload=dict(product_id=product, settlement_rule=SC_RULE, period="full",
                                         market="winner_3way", line=None)))
        held.append((product, "missing_usable_price_roster"))
    return catalog, eligible, held


def link_archive(catalog, articles, at, audit):
    votes = defaultdict(dict)
    for meta, article, _ in articles.values():
        observed = moment(meta["received_at"])
        if not at - timedelta(days=3) <= observed <= at or article["sport"] != "sc":
            continue
        try:
            vote = link(article, meta, catalog)
        except (ValueError, KeyError, StopIteration) as exc:
            audit[str(exc)] += 1
            continue
        votes[vote["payload"]["product_id"]][vote["record_id"]] = dict(vote, receipt_verified=True)
    return votes


def replay_quote(quote, votes, articles, audit):
    chosen, reasons = choose([dict(quote, receipt_verified=True), *votes.values()])
    audit.update(reasons.get("vote", {}))
    if not chosen or not chosen["vote_record_ids"]:
        return None, "no_eligible_vote"
    baseline = [votes[rid] for rid in chosen["vote_record_ids"]]
    comparison, reason = compare_quote_votes(quote, baseline, articles, chosen["odds"])
    if comparison:
        comparison["vote_receipts"] = [
            {key: row[key] for key in ("record_id", "source_url", "received_at", "body_sha256")}
            for row in baseline
        ]
    return comparison, reason


def settlement(snapshots, quote):
    kickoff = moment(quote["kickoff"])
    later = [s for s in snapshots if s[0] > kickoff]
    if not later:
        return dict(status="pending", reason="no_postkickoff_official_receipt")
    latest = max(s[0] for s in later)
    candidates = [s for s in later if s[0] == latest]
    if len({s[1]["body_sha256"] for s in candidates}) != 1:
        return dict(status="pending", reason="conflicting_latest_result_receipts")
    at, meta, _, rows = candidates[0]
    number = int(quote["payload"]["product_id"].rsplit(":", 1)[1])
    source = quote["payload"]["source_identity"]
    row = rows.get(number, {})
    evidence = dict(received_at=at.isoformat(), source_url=DETAIL, body_sha256=meta["body_sha256"])
    if not soccer_product(row) or fields(row, FIXTURE_FIELDS) != fields(source, FIXTURE_FIELDS):
        return dict(evidence, status="pending", reason="official_product_or_fixture_mismatch")
    if row.get("protoStatus") != "4":
        return dict(evidence, status="pending", reason="official_product_result_not_final")
    code = row.get("gameResult")
    labels = fields(row, "winTxt drawTxt loseTxt")
    # Archived G101 void rows carry code 4, blank labels and zero payouts.
    if (code == "4" and labels == (None, None, None)
            and fields(row, "winAllot drawAllot loseAllot") == (0, 0, 0)):
        return dict(evidence, status="void", result_code=code)
    score = re.fullmatch(r"([0-9]+):([0-9]+)", str(row.get("mchScore")))
    if (not score or code not in SIDE_CODES or labels != SIDE_LABELS
            or fields(row, "gameReject buyReject") != ("0", "0")):
        return dict(evidence, status="pending", reason="unverified_official_result_or_void")
    home, away = map(int, score.groups())
    winner = "home" if home > away else "away" if home < away else "draw"
    if winner != SIDE_CODES[code]:
        return dict(evidence, status="pending", reason="official_result_score_disagreement")
    return dict(evidence, status="settled", winner=winner, score=[home, away], result_code=code)


def index_frozen_picks(records):
    index = defaultdict(list)
    for row in records:
        if row.get("record_type") != "prediction":
            continue
        game = row.get("features", {}).get("fixture") or {}
        try:
            kickoff = fixture_kickoff(game)
            if moment(row["kickoff"]) != kickoff:
                continue
            key = (*fields(game, "sport league home away"), moment(row["kickoff"]).isoformat(), int(game["round"]))
            index[key].append(row)
        except (KeyError, TypeError, ValueError):
            continue
    return index


def frozen_pick(event, index):
    # Loaded only for the optional local ledger path. Use the exact operating
    # identity functions and saved featured pick, with no model replay.
    from ai_decision import event_id, offer_id, selection_id
    from featured_pick import VERSION

    _, round_number, number = event["product"]["product_id"].split(":")
    sport, _, kickoff, _, _ = event["fixture"]
    official = event["official_fixture"]
    key = (sport, league_name(official), official["homeName"], official["awayName"],
           kickoff, int(round_number[2:]))
    at = moment(event["as_of"])
    available = []
    for row in index.get(key, []):
        try:
            as_of, captured = (moment(row[field]) for field in ("as_of", "captured_at"))
        except (KeyError, TypeError, ValueError):
            return dict(status="pending", reason="unorderable_frozen_prediction")
        if as_of <= at and captured <= at:
            available.append((as_of, captured, row["ledger_sequence"], row))
    if not available:
        return dict(status="pending", reason="no_frozen_prediction_by_quote")
    if len({row["event_id"] for *_, row in available}) != 1:
        return dict(status="pending", reason="ambiguous_frozen_fixture")
    as_of, _, _, row = max(available, key=lambda entry: entry[:3])
    game = row["features"]["fixture"]
    pick = row.get("predictions", {}).get("featured_pick") or {}
    try:
        market_time = moment(row["market_observed_at"])
        valid_round = game["year"] == int("20" + round_number[:2])
        number_text = pick.get("game_no", "")
        same_number = isinstance(number_text, str) and number_text.isascii() and number_text.isdigit() and int(number_text) == int(number)
    except (KeyError, TypeError, ValueError):
        return dict(status="pending", reason="invalid_frozen_metadata")
    if (not valid_round or not same_number or row["event_id"] != event_id(game)
            or market_time > as_of or as_of > moment(row["captured_at"])
            or fields(pick, "version market label line settlement_rule") != (VERSION, "승무패", "", None, "official_winner")):
        return dict(status="pending", reason="frozen_product_or_clock_mismatch")
    roster = {}
    for label, side in (("홈", "home"), ("무", "draw"), ("원정", "away")):
        option = {"market": "승무패", "label": "", "line": None, "선택": label, "게임번호": number_text}
        sid, oid = selection_id(game, option), offer_id(game, option)
        matches = [o for o in row["features"].get("options", [])
                   if fields(o, "selection_id offer_id market label line selection odds")
                   == (sid, oid, "승무패", "", None, label, event["odds"][side])]
        if len(matches) != 1:
            return dict(status="pending", reason="frozen_offer_or_price_roster_mismatch")
        roster[label] = (side, sid, oid)
    side, sid, oid = roster.get(pick.get("selection"), (None, None, None))
    if side is None or fields(pick, "selection_id offer_id odds") != (sid, oid, event["odds"][side]):
        return dict(status="pending", reason="frozen_selection_or_price_mismatch")
    return dict(status="verified", selection=side, snapshot_id=row["snapshot_id"], record_hash=row["record_hash"],
                captured_at=row["captured_at"], offer_id=oid)


def paired_metrics(events, baseline):
    arms = (baseline, "deduplicated_vote")
    paired = [e for e in events if all(e.get(arm, {}).get("selection") is not None for arm in arms)]
    graded = [e for e in paired if e["result"]["status"] in ("settled", "void")]
    decisive = sum(e["result"]["status"] == "settled" for e in graded)
    metrics = {}
    for arm in arms:
        hits, pnl = 0, Decimal(0)
        for event in graded:
            result = event["result"]
            if result["status"] == "void":
                continue
            side = event[arm]["selection"]
            hit = side == result["winner"]
            hits += hit
            pnl += Decimal(str(event["odds"][side])) - 1 if hit else -1
        metrics[arm] = dict(hits=hits, net_units=str(pnl),
                            accuracy_pct=100 * hits / decisive if decisive else None,
                            roi_pct=float(100 * pnl / len(graded)) if graded else None)
    delta = {}
    for key in ("accuracy_pct", "roi_pct"):
        first, second = (metrics[arm][key] for arm in arms)
        delta[key] = second - first if first is not None else None
    return dict(paired_products=len(paired), graded_products=len(graded), decisive_products=decisive,
                void_products=len(graded) - decisive, pending_products=len(paired) - len(graded),
                paired_product_ids=[e["product"]["product_id"] for e in paired],
                arms=metrics, deduplicated_minus_baseline_pp=delta)


def evaluate(round_number, *, capture_dirs=(), article_roots=(), price_roots=(),
             evaluated_at, ledger_records=None):
    """Replay the first eligible quote per product from local archives.

    Optional ledger_records must already be hash-chain verified. The CLI reads
    the explicit local export through the repository's PredictionLedger.
    """
    round_id(round_number)
    evaluated_at = moment(evaluated_at)
    capture_dirs = list(capture_dirs)
    roots = [path / "article-evidence" for path in capture_dirs if (path / "article-evidence").is_dir()]
    articles, article_audit = article_evidence([*roots, *article_roots])
    articles = {key: entry for key, entry in articles.items() if moment(key[2]) <= evaluated_at}
    snapshots, receipt_audit, holds, vote_audit = [], [], {}, Counter()
    price_dirs = [capture / "price-evidence" / capture.name for capture in capture_dirs]
    paths = {root.resolve() / f"round-{round_number}.json" for root in [*price_dirs, *price_roots]}
    for path in sorted(paths):
        capture_id = path.parent.name
        try:
            snapshot = read_snapshot(path, round_number)
            observed, meta, _, _ = snapshot
            if observed > evaluated_at:
                raise ValueError("receipt_after_evaluation_cutoff")
            catalog, quotes, pending = quote_candidates(snapshot)
        except (KeyError, TypeError, ValueError, OSError, EOFError, OverflowError, zlib.error) as exc:
            reason = "official_receipt_missing" if isinstance(exc, FileNotFoundError) else str(exc)
            receipt_audit.append(dict(capture=capture_id, status="pending", reason=reason))
            continue
        snapshots.append((snapshot, catalog, quotes))
        receipt_audit.append(dict(capture=capture_id, status="verified", received_at=observed.isoformat(),
                                  body_sha256=meta["body_sha256"], eligible_quotes=len(quotes)))
        for product, reason in pending:
            holds.setdefault(product, set()).add(reason)

    events, chosen_quotes, linked = {}, {}, {}
    quotes_by_product, receipt_hashes = defaultdict(list), defaultdict(set)
    for snapshot, catalog, quotes in snapshots:
        receipt_hashes[snapshot[0]].add(snapshot[1]["body_sha256"])
        for quote in quotes:
            quotes_by_product[quote["payload"]["product_id"]].append((snapshot[0], quote, catalog))
    for product, entries in sorted(quotes_by_product.items()):
        for at, quote, catalog in sorted(entries, key=lambda entry: (entry[0], entry[1]["record_id"])):
            if len(receipt_hashes[at]) != 1:
                holds.setdefault(product, set()).add("conflicting_quote_receipts")
                continue
            receipt = (at, quote["body_sha256"])
            if receipt not in linked:
                linked[receipt] = link_archive(catalog, articles, at, vote_audit)
            votes = linked[receipt].get(product, {})
            comparison, reason = replay_quote(quote, votes, articles, vote_audit)
            if reason:
                holds.setdefault(product, set()).add(reason)
                continue
            events[product] = dict(comparison, odds=quote["payload"]["odds"], quote_body_sha256=quote["body_sha256"],
                                   quote_source_url=DETAIL, settlement_rule_source=RULE_SOURCE,
                                   sale_evidence=quote["payload"]["sale_evidence"],
                                   official_fixture=quote["payload"]["source_identity"])
            chosen_quotes[product] = quote
            break

    raw_snapshots = [snapshot for snapshot, _, _ in snapshots]
    operating = index_frozen_picks(ledger_records) if ledger_records is not None else None
    for product, event in events.items():
        if operating is not None:
            event["frozen_operating_pick"] = frozen_pick(event, operating)
        event["result"] = settlement(raw_snapshots, chosen_quotes[product])
    rows = list(events.values())
    frozen = dict(status="pending", reason="verified_operating_pick_not_supplied")
    if operating is not None:
        frozen = paired_metrics(rows, "frozen_operating_pick")
        frozen["verification_counts"] = dict(Counter(
            e["frozen_operating_pick"].get("reason", "verified") for e in rows))
    existing = paired_metrics(rows, "existing_vote")
    status = "no_eligible_pairs"
    if existing["graded_products"] or frozen.get("graded_products"):
        status = "graded_pairs_available"
    elif existing["pending_products"] or frozen.get("pending_products"):
        status = "pending_official_settlement"
    return dict(schema="prospective-tipster-pairs-v1", round=round_number, evaluated_at=evaluated_at.isoformat(),
                status=status,
                decision_rule="first_eligible_quote_24h_to_strict_T30_before_product_close",
                article_audit=article_audit, price_receipts=receipt_audit, vote_link_attempt_counts=dict(vote_audit),
                held_products={key: sorted(value) for key, value in sorted(holds.items()) if key not in events},
                comparison_products=len(rows), comparison_fixtures=len({e["fixture"] for e in rows}),
                repost_products=sum(bool(e["reposts"]) for e in rows),
                abstentions={arm: sum(e[arm]["selection"] is None for e in rows) for arm in ARMS},
                paired_vs_existing_vote=existing,
                paired_vs_frozen_operating_pick=frozen,
                roi_basis="hypothetical one unit per paired product, same quote, void refunded, before tax",
                events=rows)
