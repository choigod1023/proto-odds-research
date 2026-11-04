"""Offline recorded quotes/native texts/direct official DOM, with timing limits."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ai_decision import event_id, selection_id, offer_id
from fixed_tipster_hybrid import TEAMS
from devig import shin
from prediction_ledger import PredictionLedger
from research_vote_rankings import evaluate, instant, number, select_replayed_rules

KST, ET = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def official_winners(directory, manifest_path):
    result, exclusions = defaultdict(list), Counter()
    for item in json.loads(manifest_path.read_bytes()):
        if not re.fullmatch(r"G101,\d{6}\.json", item["file"]):
            raise ValueError("unsafe official evidence filename")
        raw = (directory / item["file"]).read_bytes()
        if digest(raw) != item["sha256"]:
            raise ValueError("official DOM hash mismatch")
        doc = json.loads(raw)
        parsed = urlsplit(doc["url"])
        query = parse_qs(parsed.query)
        selected = doc["selectedRound"]
        if (parsed.scheme != "https" or parsed.netloc != "www.betman.co.kr"
                or parsed.path != "/main/mainPage/gamebuy/closedGameSlipIFR.do"
                or query.get("gmId") != ["G101"] or len(selected) != 1
                or selected[0]["value"] != "G101," + query["gmTs"][0]
                or doc["url"] != item["url"]
                or instant(doc["received_at"]) != instant(item["received_at"])):
            raise ValueError("official round/URL/receipt mismatch")
        season, round_no = 2000 + int(query["gmTs"][0][:2]), int(query["gmTs"][0][2:])
        for ordinal, row in enumerate(doc["rows"]):
            if row.get("market_text") not in {"야구 승패", "축구 승무패", "농구 승패", "배구 승패"} or row.get("label"):
                continue
            if row.get("result_action") != "결과보기" or not re.fullmatch(r"\d+:\d+", row.get("result_text", "")):
                exclusions["not_confirmed_full_game_score"] += 1
                continue
            sport = {"야구": "bs", "축구": "sc", "농구": "bk", "배구": "vl"}[row["market_text"].split()[0]]
            market = row["market_text"].split()[1]
            n_way = 3 if sport == "sc" else 2
            if len(row.get("options", [])) != n_way or {x["key"] for x in row["options"]} != ({"1", "2", "3"} if n_way == 3 else {"1", "3"}):
                exclusions["invalid_official_roster"] += 1
                continue
            start = datetime.strptime(row["kickoff_text"], "%Y%m%d%H%M%S").replace(tzinfo=KST)
            if start.year != season or not row["rowname"].endswith("_" + str(int(start.timestamp()*1000))):
                raise ValueError("official kickoff/epoch disagreement")
            names = row["game_name"].split(":")
            if len(names) != 2:
                exclusions["ambiguous_names"] += 1
                continue
            game = dict(year=season, round=round_no, sport=sport, league=row["league"],
                        home=names[0], away=names[1],
                        date=start.strftime("%m.%d") + "(" + "월화수목금토일"[start.weekday()] + ") " + start.strftime("%H:%M"))
            label = dict(event_id=event_id(game), kickoff=start.isoformat(), market=market, label="", n_way=n_way,
                         result_text=row["result_text"], result_action=row["result_action"],
                         official_source=dict(url=doc["url"], evidence_sha256=item["sha256"], received_at=doc["received_at"],
                                              row_index=ordinal, game_no=row["game_no"], game_name=row["game_name"],
                                              market_text=row["market_text"], kickoff_text=row["kickoff_text"]))
            result[event_id(game)].append((game, label))
    return result, exclusions


def parse_native(envelope):
    row = envelope["event"]
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if digest(canonical) != envelope["event_hash"]:
        raise ValueError("source event bytes/hash disagreement")
    if not (row.get("event_type") == "first_observed" and row.get("source") == "tailslips_public_html"
            and row.get("eligible_pre_event") is True and row.get("game_started") is False
            and row.get("result") is None and not row.get("is_baseline") and not row.get("is_parlay_leg")
            and row.get("market_type") == "moneyline" and row.get("slug") and row.get("pick_id")):
        raise ValueError("not_eligible_native_pregame_text")
    matched = re.fullmatch(r"([A-Z]+) ML(?: ([+-]\d{3,4}))?(?: \d+(?:\.\d+)?u)?", row.get("selection", ""), re.I)
    if not matched or matched[1] not in {row.get("team_a"), row.get("team_b")}:
        raise ValueError("not_explicit_full_game_winner")
    if " ".join(row["selection"].split()) not in " ".join(row.get("raw_text", "").split()):
        raise ValueError("native_text_does_not_contain_pick")
    if matched[2] and (abs(int(matched[2])) < 100 or row.get("american_odds") not in {None, int(matched[2])}):
        raise ValueError("contradicting_native_price")
    clock = re.search(r"\b(\d{1,2}:\d{2})\s+(AM|PM)\s+ET\b", row.get("game_header", ""))
    if not clock or not row.get("slate_date"):
        raise ValueError("missing_source_schedule")
    header = row["game_header"]
    teams = (re.match(r"^([A-Z]{2,3})\s+\d{1,2}:\d{2}\s+(?:AM|PM)\s+ET\s+([A-Z]{2,3})\b", header, re.I)
             or re.match(r"^([A-Z]{2,3})\s+(?:vs\.?|@)\s+([A-Z]{2,3})\s+\d{1,2}:\d{2}\s+(?:AM|PM)\s+ET\b", header, re.I))
    if not teams or {v.upper() for v in teams.groups()} != {row.get("team_a"), row.get("team_b")}:
        raise ValueError("source_header_teams_disagree")
    scheduled = datetime.strptime(row["slate_date"] + " " + clock[1] + " " + clock[2], "%Y-%m-%d %I:%M %p").replace(tzinfo=ET)
    observed = instant(row["observed_at"])
    if not scheduled-timedelta(days=3) <= observed < scheduled-timedelta(minutes=30):
        raise ValueError("source_receipt_not_before_T30")
    return dict(row, native_record=row, selected_code=matched[1].upper(), scheduled=scheduled, observed=observed,
                source_event_hash=envelope["event_hash"], has_expert_quote=bool(matched[2]))


def load_quotes(ledger_path, official):
    raw = ledger_path.read_bytes()
    if ledger_path.suffix == ".gz":
        raw = gzip.decompress(raw)
    records = [json.loads(line) for line in raw.splitlines() if line]
    verifier = object.__new__(PredictionLedger)
    verifier._database = SimpleNamespace(prediction_records=lambda: records)
    verifier._read_verified_storage()
    latest, held = {}, Counter()
    for record in records:
        if record["record_type"] != "prediction" or record["event_id"] not in official:
            continue
        clocks = [instant(record[k]) for k in ("as_of", "captured_at", "market_observed_at")]
        if max(clocks) >= instant(record["kickoff"])-timedelta(minutes=30):
            continue
        previous = latest.get(record["event_id"])
        if previous is None or (clocks[0], record["ledger_sequence"]) > (instant(previous["as_of"]), previous["ledger_sequence"]):
            latest[record["event_id"]] = record
    quotes = []
    for eid, record in latest.items():
        options = [o for o in record["features"].get("options", []) if o.get("market") in {"승패", "승무패"} and not o.get("label")]
        if len(options) not in {2, 3}:
            held["missing_original_complete_winner_vector"] += 1
            continue
        matched = []
        for game, label in official[eid]:
            if label["market"] != options[0]["market"] or len(options) != label["n_way"] or instant(record["kickoff"]) != instant(label["kickoff"]):
                continue
            no = label["official_source"]["game_no"]
            for candidate in {str(no), str(int(no)), str(int(no)).zfill(3)}:
                if all(selection_id(game, dict(o, **{"선택": o["selection"]})) == o["selection_id"]
                       and offer_id(game, dict(o, **{"선택": o["selection"], "게임번호": candidate})) == o["offer_id"] for o in options):
                    matched.append((game, label))
                    break
        if not matched:
            held["original_offer_fingerprint_not_reproduced"] += 1
            continue
        if len({label["result_text"] for game, label in matched}) != 1:
            held["conflicting_official_scores"] += 1
            continue
        game, label = matched[0]
        if label["n_way"] == 2 and len(set(map(int, label["result_text"].split(":")))) == 1:
            held["two_way_equal_score_without_refund_proof"] += 1
            continue
        quotes.append((record, game, dict(label, snapshot_id=record["snapshot_id"]), options))
    return quotes, held, digest(raw)


def load_observed_quotes(path, official):
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    held, candidates = Counter(), defaultdict(list)
    for row in json.loads(raw):
        eid = row["target_event_id"]
        matched = [(g, l) for g, l in official.get(eid, [])
                   if g["year"] == row["season"] and g["round"] == row["round"]
                   and int(l["official_source"]["game_no"]) == int(row["game_no"])]
        if not matched:
            held["snapshot_has_no_exact_official_product"] += 1
            continue
        if len({l["result_text"] for g, l in official[eid]}) != 1:
            held["conflicting_official_full_game_scores"] += 1
            continue
        game, label = matched[0]
        if label["market"] != "승패" or label["n_way"] != 2 or label["label"]:
            held["official_product_is_not_two_way_winner"] += 1
            continue
        captured_game = dict(year=row["season"], round=row["round"], sport=row["sport"], league=row["league"],
                             home=row["home"], away=row["away"], date=row["date_text"])
        if (event_id(captured_game) != eid or row["market_family"] != "승패"
                or row["market_label"] not in {None, ""} or row["n_way"] != 2):
            held["snapshot_identity_or_product_mismatch"] += 1
            continue
        observed, start = instant(row["observed_at"]), instant(label["kickoff"])
        if not start-timedelta(hours=24) <= observed < start-timedelta(minutes=30):
            held["quote_outside_fixed_24h_to_T30_window"] += 1
            continue
        odds = json.loads(row["odds_json"])
        if len(odds) != 2 or any(number(x) <= 1 for x in odds):
            held["invalid_snapshot_odds"] += 1
            continue
        if len(set(map(int, label["result_text"].split(":")))) == 1:
            held["two_way_equal_score_without_refund_proof"] += 1
            continue
        probability = shin(odds)
        options = []
        for side, price, p in zip(("홈", "원정"), odds, probability):
            option = dict(market="승패", label="", line=None, 선택=side, 게임번호=str(row["game_no"]))
            options.append(dict(market="승패", label="", line=None, selection=side, odds=price,
                                market_probability=round(p, 4), selection_id=selection_id(captured_game, option),
                                offer_id=offer_id(captured_game, option)))
        quote = dict(snapshot_id="observed_quote_"+str(row["id"]), event_id=eid, features={"options": options},
                     record_hash=digest(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()),
                     captured_at=None, as_of=row["observed_at"], market_observed_at=row["observed_at"],
                     kickoff=label["kickoff"], model={}, predictions={}, quote_row=row,
                     capture_kind="quote_observation_not_model_prediction")
        candidates[eid].append((quote, captured_game, dict(label, snapshot_id=quote["snapshot_id"]), options))
    quotes = []
    for eid, rows in candidates.items():
        latest_time = max(instant(r[0]["as_of"]) for r in rows)
        latest = [r for r in rows if instant(r[0]["as_of"]) == latest_time]
        if len({tuple(o["odds"] for o in r[3]) for r in latest}) != 1:
            held["conflicting_simultaneous_quote_vectors"] += 1
            continue
        quotes.append(max(latest, key=lambda r: r[0]["quote_row"]["id"]))
    return quotes, held, digest(raw)


def build(args):
    official, official_held = official_winners(args.official_dom, args.official_manifest)
    snapshots = getattr(args, "quote_snapshots", None)
    if snapshots:
        quotes, quote_held, price_sha = load_observed_quotes(snapshots, official)
        ledger_sha = None
    else:
        quotes, quote_held, ledger_sha = load_quotes(args.ledger, official)
        price_sha = None
    native, rejected = defaultdict(list), Counter()
    raw = args.events.read_bytes()
    if args.events.suffix == ".gz":
        raw = gzip.decompress(raw)
    for line in raw.splitlines():
        envelope = json.loads(line)
        if envelope["stream"] != "pickster_pick_events":
            continue
        try:
            row = parse_native(envelope)
            native[(frozenset((row["team_a"], row["team_b"])), row["scheduled"].date())].append(row)
        except (ValueError, TypeError, KeyError) as exc:
            rejected[str(exc)] += 1
    features, labels = [], []
    no_votes, audit = Counter(), Counter()
    for record, game, label, options in quotes:
        if game["league"] != "MLB":
            continue
        home, away = TEAMS.get(game["home"]), TEAMS.get(game["away"])
        start, cutoff = instant(record["kickoff"]), instant(record["as_of"])
        date = start.astimezone(ET).date()
        candidates = defaultdict(list)
        for row in native.get((frozenset((home, away)), date), []):
            if (abs((row["scheduled"]-start).total_seconds()) > 600
                    or row["observed"] > cutoff or not start-timedelta(days=3) <= row["observed"] < start-timedelta(minutes=30)):
                continue
            candidates[row["slug"]].append(row)
        evidence = []
        for author, values in sorted(candidates.items()):
            first_at = min(row["observed"] for row in values)
            earliest = [row for row in values if row["observed"] == first_at]
            if len({row["selected_code"] for row in earliest}) != 1:
                audit["ambiguous_same_author_first_votes"] += 1
                continue
            row = min(earliest, key=lambda x: x["pick_id"])
            side = "홈" if row["selected_code"] == home else "원정"
            evidence.append(dict(id=row["pick_id"], author="tailslips:"+author, side=side,
                                 observed_at=row["observed_at"], raw_text=row["raw_text"],
                                 original_selection_text=row["selection"], source_event_hash=row["source_event_hash"],
                                 content_hash=digest(row["raw_text"].encode()), evidence_kind="source_native_pick_text",
                                 source_container_url="https://tailslips.com/slate?sport=mlb",
                                 source_schedule=row["scheduled"].isoformat(), source_header=row["game_header"],
                                 source_schedule_difference_seconds=abs((row["scheduled"]-start).total_seconds()),
                                 native_record=row["native_record"],
                                 has_expert_quoted_odds=row["has_expert_quote"]))
        if not evidence:
            no_votes[record["kickoff"][:7]] += 1
            continue
        votes = {side: sum(row["side"] == side for row in evidence) for side in ("홈", "무", "원정")}
        feature = dict(comparison_mode="counterfactual_saved_pregame_votes", snapshot_id=record["snapshot_id"],
                       event_id=record["event_id"], source_record_hash=record["record_hash"], fixture=game,
                       recorded_operating_policy=record["model"].get("selection_policy") or record["model"].get("operating_version"),
                       recorded_operating_selection=record["predictions"].get("selection"), sport=game["sport"],
                       kickoff=record["kickoff"], as_of=record["as_of"], captured_at=record["captured_at"],
                       market_observed_at=record["market_observed_at"], market=label["market"], label="",
                       vote_counts=votes, evidence=evidence, options=options)
        if record.get("capture_kind"):
            feature.update(capture_kind=record["capture_kind"], quote_observed_at=record["as_of"],
                           observed_quote=record["quote_row"], ids_origin="derived_from_observed_quote_columns_not_a_saved_prediction",
                           quote_age_minutes=(start-cutoff).total_seconds()/60,
                           quote_timestamp_basis="legacy_batch_start_not_HTTP_receipt",
                           actual_quote_receipt_before_T30_proven=False)
        try:
            select_replayed_rules(feature)
        except ValueError as exc:
            audit["invalid_frozen_input:"+str(exc)] += 1
            continue
        features.append(feature)
        labels.append(label)
    proof = dict(version="extended-saved-votes-v1", ledger_raw_sha256=ledger_sha, full_ledger_chain_verified=bool(ledger_sha),
                 observed_quote_export_sha256=price_sha, price_evidence="stored_observed_quote_not_model_prediction" if snapshots else "frozen_prediction_ledger_options",
                 source_event_export_raw_sha256=digest(raw), official_manifest_sha256=digest(args.official_manifest.read_bytes()),
                 official_score_exclusions=dict(official_held), quote_exclusions=dict(quote_held),
                 source_event_exclusions=dict(rejected), no_pregame_vote_by_month=dict(no_votes), audit=dict(audit),
                 valid_quote_fixtures_all_sports=len(quotes), native_text_fixture_count=len(features),
                 authors=len({e["author"] for f in features for e in f["evidence"]}),
                 dates=[min(f["kickoff"] for f in features), max(f["kickoff"] for f in features)] if features else [],
                 by_month=dict(Counter(instant(f["kickoff"]).astimezone(KST).strftime("%Y-%m") for f in features)),
                 evidence_kind="saved source-native pick text, not full HTML; stored receipt clocks are not external time certificates",
                 policies_are_counterfactual_replay_not_operating_returns=True,
                 quote_time_basis="legacy_batch_start_not_HTTP_receipt" if snapshots else "prediction_payload_captured_before_T30",
                 protocol_limits_not_proven=["Actual completed odds receipt before T30 (legacy timestamp is request batch start)"] if snapshots else [])
    return features, labels, proof


def reports(features, labels):
    out = {}
    periods = dict(all=lambda f: True,
                   earlier=lambda f: instant(f["kickoff"]).astimezone(KST).date().isoformat() < "2026-10-29")
    for month in sorted({instant(f["kickoff"]).astimezone(KST).strftime("%Y-%m") for f in features}):
        periods[month] = lambda f, month=month: instant(f["kickoff"]).astimezone(KST).strftime("%Y-%m") == month
    if any("quote_age_minutes" in f for f in features):
        periods["fresh_quotes_90min"] = lambda f: f.get("quote_age_minutes", 100000) <= 90
    for name, included in periods.items():
        chosen = [f for f in features if included(f)]
        if not chosen:
            continue
        ids = {f["snapshot_id"] for f in chosen}
        complete = evaluate(chosen, [l for l in labels if l["snapshot_id"] in ids], selector=select_replayed_rules)
        unique = [f for f in chosen if select_replayed_rules(f)["frozen_vote"] is not None]
        ids = {f["snapshot_id"] for f in unique}
        common = evaluate(unique, [l for l in labels if l["snapshot_id"] in ids], selector=select_replayed_rules) if unique else None
        out[name] = dict(all_electorates=complete, common_unique_vote_cohort=common)
    return out


def compact_summary(output):
    return {period: {rule: {k: v for k, v in stats.items() if k not in {"rows", "by_sport"}}
                     for rule, stats in (data["common_unique_vote_cohort"] or {}).get("results", {}).items()}
            for period, data in output.items()}


def replay(directory):
    manifest = json.loads((directory / "artifact-manifest.json").read_bytes())
    values = {}
    for name, expected in manifest["raw_payload_sha256"].items():
        if name not in {"inputs", "labels", "results"}:
            raise ValueError("unexpected artifact name")
        raw = gzip.decompress((directory / (name + ".json.gz")).read_bytes())
        if digest(raw) != expected:
            raise ValueError("artifact payload changed")
        values[name] = json.loads(raw)
    for feature in values["inputs"]:
        for evidence in feature["evidence"]:
            native = parse_native(dict(event=evidence["native_record"], event_hash=evidence["source_event_hash"]))
            if native["pick_id"] != evidence["id"] or native["raw_text"] != evidence["raw_text"]:
                raise ValueError("native original and derived evidence disagree")
    computed = reports(values["inputs"], values["labels"])
    if computed != values["results"]:
        raise ValueError("replay differs from saved study results")
    return compact_summary(computed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("ledger", "events", "official-manifest", "official-dom", "output"):
        parser.add_argument("--"+name, type=Path)
    parser.add_argument("--quote-snapshots", type=Path)
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    if args.replay:
        print(json.dumps(replay(args.replay), ensure_ascii=False))
        return
    if any(getattr(args, key) is None for key in ("ledger", "events", "official_manifest", "official_dom", "output")):
        parser.error("build requires ledger/events/official-manifest/official-dom/output")
    features, labels, proof = build(args)
    output = reports(features, labels)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = dict(raw_payload_sha256={}, price_evidence=proof["price_evidence"],
                    source_event_export_raw_sha256=proof["source_event_export_raw_sha256"],
                    observed_quote_export_sha256=proof["observed_quote_export_sha256"],
                    ledger_raw_sha256=proof["ledger_raw_sha256"], official_manifest_sha256=proof["official_manifest_sha256"])
    for name, value in (("inputs", features), ("labels", labels), ("results", output)):
        raw = encoded(value)
        (args.output / (name + ".json.gz")).write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
        manifest["raw_payload_sha256"][name] = digest(raw)
    (args.output / "artifact-manifest.json").write_bytes(encoded(manifest))
    (args.output / "audit.json").write_bytes(encoded(proof))
    summary = compact_summary(output)
    (args.output / "summary.json").write_bytes(encoded(summary))
    print(json.dumps(dict(audit=proof, summary=summary), ensure_ascii=False))


if __name__ == "__main__":
    main()
