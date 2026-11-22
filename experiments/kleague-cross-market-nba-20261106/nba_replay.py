"""Replay retrospective Betman NBA prices against hash-checked official NBA evidence."""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import fitz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from scripts.collect_basketball_comparison_sources import TEAM_NAMES, injury_records, nba_game_page

PRICE_DIR = ROOT / "experiments/pregame-proto-prices-20261104"
BASKETBALL_DIR = ROOT / "experiments/pregame-basketball-20261104"
NBA_REF = "2df626042f633ba08f381f755ccc18f2068733f6"
NBA_PATH = "experiments/nba-official-information-20261103"
DETAIL = "https://www.betman.co.kr/buyPsblGame/gameInfoInq.do"
SLIP = "https://www.betman.co.kr/main/mainPage/gamebuy/gameSlip.do?gmId=G101&gmTs="
RULES = [
    "https://www.sportstoto.co.kr/terms_allocation.php",
    "https://www.sportstoto.co.kr/proto_rules.php",
    "https://www.sportstoto.co.kr/proto_intro.php",
]
WINNER = ("3", "2", "농구 승패", "일반 승패", 21)
PRODUCT_FIELDS = "betId betTypId betNm betTypNm handi"
OUTCOMES = {"0": "home", "2": "away"}
ET = ZoneInfo("America/New_York")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checked(raw, expected):
    if sha(raw) != expected:
        raise ValueError("body SHA-256 mismatch: " + expected)
    return raw


def fields(row, names):
    return tuple(row.get(name) for name in names.split())


def utc(value):
    dt = datetime.fromisoformat(value)
    if dt.utcoffset() is None:
        raise ValueError("NBA timestamp requires timezone")
    return dt.astimezone(timezone.utc)


def eastern_date(tip):
    return utc(tip).astimezone(ET).date().isoformat()


def props(raw):
    match = re.search(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw, re.S)
    if not match:
        raise ValueError("NBA HTML lacks __NEXT_DATA__")
    return json.loads(match[1])["props"]["pageProps"]


def archive_blob(name):
    return subprocess.check_output(["git", "show", f"{NBA_REF}:{NBA_PATH}/{name}"], cwd=ROOT)


def round_rows(raw, number):
    data = json.loads(raw)
    lottery = data["currentLottery"]
    if (str(data["gmTs"]), lottery["gmId"], str(lottery["gmTs"])) != (number, "G101", number):
        raise ValueError("unexpected official round identity: " + number)
    if lottery["saleProgress"] is not False:
        raise ValueError("open round excluded from historical census: " + number)
    schedule = data["compSchedules"]
    keys = schedule["keys"]
    if len(keys) != len(set(keys)) or any(len(row) != len(keys) for row in schedule["datas"]):
        raise ValueError("malformed source round: " + number)
    rows = [dict(zip(keys, values)) for values in schedule["datas"]]
    seqs = Counter(row["matchSeq"] for row in rows)
    if any(count != 1 for count in seqs.values()):
        raise ValueError("nonunique round matchSeq: " + number)
    return [row for row in rows if fields(row, "itemCode leagueCode leagueName leagueShortName") ==
            ("BK", "BK002", "NBA", "NBA")]


def settlement(row, modern):
    if row["gameResult"] == "4":
        return None, "void_or_placeholder"
    if row["protoStatus"] not in ({"4"} if modern else {"4", "20"}) or row["gameResult"] not in OUTCOMES:
        return None, "unsettled"
    if fields(row, "gameReject buyReject live unsetSchedule unsetEndDate") != ("0", "0", "N", "N", "N"):
        return None, "restricted_or_unscheduled"
    if row["gameSubject"] not in (None, "") or fields(row, "winHandi drawHandi loseHandi drawAllot") != (0, 0, 0, 0):
        return None, "not_two_way_unadjusted"
    if modern and fields(row, "winTxt drawTxt loseTxt") != ("승", "-", "패"):
        return None, "unexpected_selection_labels"
    if not all(type(v) in (float, int) and math.isfinite(v) and 1 < v < 1000
               for v in fields(row, "winAllot loseAllot")):
        return None, "incomplete_price"
    if (type(row["gameDate"]) is not int or row["gameDate"] <= 0 or
            not all(isinstance(v, str) and v and v != "999" for v in fields(row, "homeId awayId")) or
            row["homeId"] == row["awayId"]):
        return None, "unresolved_identity"
    score = re.fullmatch(r"([0-9]+):([0-9]+)", row["mchScore"] or "")
    if not score:
        return None, "unresolved_final_score"
    scores = tuple(map(int, score.groups()))
    win = OUTCOMES[row["gameResult"]]
    if scores[0] == scores[1] or (scores[0] > scores[1]) != (win == "home"):
        return None, "settlement_score_conflict"
    return (list(scores), win), None


def read_betman(manifest, source_roots, evidence):
    archived = []
    nba_sources = []
    for source in manifest["sources"]:
        root = source_roots[source["source_root_index"]].resolve()
        path = (root / source["body_file"]).resolve()
        if not path.is_relative_to(root) or source["source_url"] != DETAIL:
            raise ValueError("unexpected manifest evidence")
        archived.append((source["round"], "manifest_v2",
                         checked(gzip.decompress(path.read_bytes()), source["body_sha256"]), source))
    meta, raw = evidence["betman-260131"]
    if meta["url"] != DETAIL or (meta["request_json"]["gmId"], meta["request_json"]["gmTs"]) != ("G101", "260131"):
        raise ValueError("unexpected round receipt")
    archived.append(("260131", "additional_closed_round", raw, meta))
    if len({item[0] for item in archived}) != len(archived):
        raise ValueError("duplicate Betman rounds")
    counts, exclusions, products = Counter(), Counter(), Counter()
    modern_rows, legacy_rows = [], []
    for number, origin, raw, meta in archived:
        rows = round_rows(raw, number)
        if not rows:
            continue
        digest = sha(raw)
        nba_sources.append(dict(round=number, archive=origin, body_sha256=digest, raw_rows=len(rows)))
        for row in rows:
            code = fields(row, PRODUCT_FIELDS)
            products[code] += 1
            counts[origin + "_raw_nba"] += 1
            modern = code == WINNER
            if not modern and code != (None, None, None, None, 0):
                continue
            family = "modern" if modern else "legacy"
            counts[origin + "_" + family + "_raw"] += 1
            terms, error = settlement(row, modern)
            if error:
                exclusions[origin + "_" + family + "_" + error] += 1
                continue
            scores, win = terms
            tip = datetime.fromtimestamp(row["gameDate"] / 1000, timezone.utc).isoformat()
            result = dict(product_id=f"G101:{number}:{row['matchSeq']}", round=number, match_seq=row["matchSeq"],
                          archive=origin, source_url=DETAIL, public_round_url=SLIP + number,
                          body_sha256=digest, received_at=meta["received_at"], tipoff_utc=tip,
                          eastern_date=eastern_date(tip), home_id=row["homeId"], away_id=row["awayId"],
                          home_name=row["homeName"], away_name=row["awayName"],
                          source_product=dict(zip(PRODUCT_FIELDS.split(), code)),
                          odds={"home": row["winAllot"], "away": row["loseAllot"]},
                          final_score=scores, winner=win, game_result_code=row["gameResult"],
                          proto_status_code=row["protoStatus"], timing_basis="retrospective",
                          execution_price_verified=False)
            (modern_rows if modern else legacy_rows).append(result)
            counts[origin + "_" + family + "_settled_with_price"] += 1
    inventory = [dict(zip(PRODUCT_FIELDS.split(), code), raw_rows=count) for code, count in products.most_common()]
    return modern_rows, legacy_rows, dict(counts), dict(exclusions), inventory, nba_sources


def nba_row(game, meta, tip_field, kind):
    gid = str(game["gameId"])
    tip = utc(game[tip_field]).isoformat()
    h, a = game["homeTeam"], game["awayTeam"]
    home, away = fields(h, "teamTricode teamId"), fields(a, "teamTricode teamId")
    if home[0] not in TEAM_NAMES or away[0] not in TEAM_NAMES or home[0] == away[0]:
        raise ValueError("NBA team identity unresolved: " + gid)
    score = [h["score"], a["score"]] if game["gameStatus"] == 3 else None
    if score and (any(type(v) is not int or v < 0 for v in score) or score[0] == score[1]):
        raise ValueError("invalid final NBA score: " + gid)
    return dict(game_id=gid, tipoff_utc=tip, eastern_date=eastern_date(tip), home=home[0], away=away[0],
                home_team_id=home[1], away_team_id=away[1], final_score=score, source_kind=kind,
                url=meta["url"], body_sha256=meta["sha256"], received_at=meta["received_at"])


def read_nba(evidence):
    fixtures, boxes = {}, {}
    sources = []

    def add(row):
        gid = row["game_id"]
        old = fixtures.get(gid)
        keys = "tipoff_utc home away home_team_id away_team_id"
        if old and (fields(old, keys) != fields(row, keys) or
                    old["final_score"] and row["final_score"] and old["final_score"] != row["final_score"]):
            raise ValueError("official NBA sources conflict: " + gid)
        if not old or (not old["final_score"] and row["final_score"]):
            fixtures[gid] = row

    for name, (meta, raw) in sorted(evidence.items()):
        if not re.fullmatch(r"games-\d{4}-\d{2}-\d{2}", name):
            continue
        if meta["url"] != "https://www.nba.com/games?date=" + name[6:]:
            raise ValueError("unexpected daily NBA URL")
        nba_game_page(dict(meta, body_sha256=meta["sha256"]), raw)
        page = props(raw)
        cards = [card["cardData"] for module in page["gameCardFeed"]["modules"] for card in module["cards"]]
        expected = page["allGamesInCurrentYear"].get(name[6:10], {}).get(name[6:])
        if expected != len(cards) or len({g["gameId"] for g in cards}) != len(cards):
            raise ValueError("incomplete NBA daily schedule: " + name)
        sources.append(dict(url=meta["url"], fixtures=len(cards), body_sha256=meta["sha256"]))
        for game in cards:
            row = nba_row(game, meta, "gameTimeUtc", "official_daily_game_cards")
            if row["eastern_date"] != name[6:]:
                raise ValueError("official NBA calendar date mismatch: " + name)
            add(row)

    schedule = next(row for row in json.loads(archive_blob("provider/requests.json"))
                    if row["name"] == "official-team-schedule")
    raw = checked(gzip.decompress(archive_blob("provider/" + schedule["file"])), schedule["body_sha256"])
    schedule["sha256"] = schedule["body_sha256"]
    games = props(raw)["scheduleData"]["schedule"]
    for game in games:
        if str(game["gameId"]).startswith(("002", "004")):
            add(nba_row(game, schedule, "gameTimeUTC", "official_nuggets_schedule_archive"))

    def add_box(raw, meta, expected_id):
        game = props(raw)["game"]
        row = nba_row(game, meta, "gameTimeUTC", "official_box_score")
        if row["game_id"] != expected_id or not row["final_score"] or type(game["period"]) is not int or game["period"] < 4:
            raise ValueError("incomplete official box: " + row["game_id"])
        teams = {}
        for side in ("home", "away"):
            team = game[side + "Team"]
            periods = team["periods"]
            if (list(range(1, game["period"] + 1)) != [p["period"] for p in periods] or
                    sum(p["score"] for p in periods) != team["score"]):
                raise ValueError("official box period/total conflict")
            starters = []
            for player in team["players"]:
                if player["position"]:
                    stats = player["statistics"]
                    if not re.fullmatch(r"\d+:[0-5]\d", stats["minutes"]):
                        raise ValueError("invalid official starter minutes")
                    starters.append({"player_id": player["personId"], "position": player["position"],
                                     "minutes": stats["minutes"], "points": stats["points"]})
            if len({p["player_id"] for p in starters}) != 5 or len(starters) != 5:
                raise ValueError("incomplete official starters")
            teams[side] = dict(period_points=[p["score"] for p in periods], starters=starters,
                               team_statistics=team["statistics"])
        add(row)
        if row["game_id"] in boxes:
            raise ValueError("duplicate official box")
        boxes[row["game_id"]] = dict(url=row["url"], body_sha256=row["body_sha256"],
                                     received_at=row["received_at"], periods=game["period"], teams=teams)

    for meta in json.loads(archive_blob("manifest.json"))["requests"]:
        match = re.fullmatch(r"raw/box-(\d{10})\.html", meta["file"])
        if match and meta["status"] == 200:
            add_box(checked(archive_blob(meta["file"]), meta["sha256"]), meta, match[1])
    for name, (meta, raw) in sorted(evidence.items()):
        if re.fullmatch(r"box-\d{10}", name):
            if meta["url"] != "https://www.nba.com/game/" + name[4:] + "/box-score":
                raise ValueError("unexpected NBA box URL")
            add_box(raw, meta, name[4:])
    return fixtures, boxes, sources, {"url": schedule["url"], "raw_rows": len(games),
                                      "body_sha256": schedule["sha256"], "git_ref": NBA_REF}


def independent_aliases(modern, fixtures):
    anchored = defaultdict(list)
    for game in fixtures.values():
        if not game["final_score"]:
            continue
        for side in ("home", "away"):
            anchored[(game["tipoff_utc"], side, game[side], tuple(game["final_score"]))].append(game)
    witnesses = defaultdict(dict)
    for row in modern:
        for side, other in (("home", "away"), ("away", "home")):
            unknown, anchor = row[side + "_id"], row[other + "_id"]
            if unknown in TEAM_NAMES or anchor not in TEAM_NAMES:
                continue
            games = anchored[(row["tipoff_utc"], other, anchor, tuple(row["final_score"]))]
            if len(games) == 1:
                game = games[0]
                proof = dict(product_id=row["product_id"], round=row["round"], game_id=game["game_id"],
                             tipoff_utc=game["tipoff_utc"], eastern_date=game["eastern_date"],
                             shared_opponent=anchor, final_score=game["final_score"],
                             betman_team_name=row[side + "_name"], official_tricode=game[side],
                             nba_url=game["url"], betman_url=row["public_round_url"],
                             betman_body_sha256=row["body_sha256"], nba_body_sha256=game["body_sha256"])
                witnesses[unknown][game["game_id"]] = proof
    verified = {}
    for code, values in sorted(witnesses.items()):
        evidence = sorted(values.values(), key=lambda w: (w["tipoff_utc"], w["game_id"]))
        tricodes = {proof["official_tricode"] for proof in evidence}
        if len(tricodes) == 1 and len({proof["eastern_date"] for proof in evidence}) >= 2:
            verified[code] = dict(official_tricode=evidence[0]["official_tricode"], witnesses=evidence)
    return verified


def match_prices(rows, fixtures, aliases, boxes, injuries):
    index = defaultdict(list)
    for game in fixtures.values():
        index[fields(game, "tipoff_utc home away")].append(game)
    for row in rows:
        teams, proofs = [], {}
        for code in fields(row, "home_id away_id"):
            if code in TEAM_NAMES:
                teams.append(code)
                continue
            witness = next((w for w in aliases.get(code, {}).get("witnesses", [])
                            if w["eastern_date"] != row["eastern_date"] and w["round"] != row["round"]), None)
            if witness:
                proofs[code] = witness["product_id"]
                teams.append(witness["official_tricode"])
        if len(teams) != 2:
            row["nba_join"] = "unproven_team_alias"
            continue
        games = index[(row["tipoff_utc"], *teams)]
        if len(games) != 1:
            row["nba_join"] = "no_exact_schedule_match" if not games else "ambiguous_exact_schedule_match"
            continue
        game = games[0]
        row["official_nba"] = game
        row["alias_witness_product_ids"] = proofs
        if not game["final_score"]:
            row["nba_join"] = "exact_schedule_without_final"
        elif game["final_score"] != row["final_score"]:
            row["nba_join"] = "final_score_mismatch"
        else:
            row["nba_join"] = "exact_schedule_and_final"
            gid = game["game_id"]
            row["official_box"] = boxes.get(gid)
            row["official_pregame_injury"] = injuries.get(gid)


def read_injuries(evidence):
    schedule_meta, schedule = evidence["prior-games-2026-11-04"]
    pdf_meta, pdf = evidence["prior-injury-2026-11-04"]
    manifest = json.loads((BASKETBALL_DIR / "season-refresh-records.manifest.json").read_bytes())
    for meta, raw in ((schedule_meta, schedule), (pdf_meta, pdf)):
        original = next(r for r in manifest["requests"] if r["url"] == meta["url"])
        checked(raw, original["body_sha256"])
        if fields(original, "received_at timestamp_basis status") != fields(meta, "received_at timestamp_basis status"):
            raise ValueError("prior NBA receipt does not match published manifest")
    schedule_meta["body_sha256"] = schedule_meta["sha256"]
    pdf_meta["body_sha256"] = pdf_meta["sha256"]
    fixtures = [r for r in nba_game_page(schedule_meta, schedule) if r["kind"] == "fixture"]
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        raw_text = "\n".join(p.get_text() for p in doc)
    injuries = injury_records(pdf_meta, raw_text, fixtures)
    by_id = {row["payload"]["source_fixture_id"]: row["payload"] for row in fixtures}
    original = [json.loads(line) for line in (BASKETBALL_DIR / "season-refresh-records.jsonl").read_bytes().splitlines()]
    published = {r["record_id"]: r for r in original if r["league"] == "NBA" and r["kind"] == "lineup"}
    if {r["record_id"]: r for r in injuries} != published:
        raise ValueError("official injury PDF replay differs from published archive")
    reports = {}
    for row in injuries:
        if not utc(schedule_meta["received_at"]) <= utc(row["received_at"]) < utc(row["kickoff"]):
            raise ValueError("official injury receipt is outside pregame order")
        payload = row["payload"]
        game_id = payload["source_fixture_id"]
        source_fixture = by_id[game_id]
        reports[game_id] = dict(url=row["source_url"], body_sha256=row["body_sha256"],
            received_at=row["received_at"], report_header=payload["report_header_claim"],
            timestamp_basis=row["timestamp_basis"], fixture_record_id=payload["fixture_record_id"],
            tipoff_utc=utc(row["kickoff"]).isoformat(), players=payload["players"],
            home_team_id=source_fixture["home_team_id"], away_team_id=source_fixture["away_team_id"])
    return reports


def read_bundle(path):
    with ZipFile(path) as archive:
        receipts = json.loads(archive.read("receipts.json"))
        evidence = {}
        for meta in receipts:
            if meta["status"] != 200:
                continue
            name = meta["name"]
            if name in evidence:
                raise ValueError("duplicate bundled receipt")
            raw = checked(archive.read(name), meta["sha256"])
            if len(raw) != meta["bytes"]:
                raise ValueError("body size mismatch")
            evidence[name] = meta, raw
    return receipts, evidence


def summarize(rows):
    linked = [r for r in rows if r["nba_join"] == "exact_schedule_and_final"]
    box_rows = [r for r in linked if r["official_box"]]
    injury_rows = [r for r in linked if r["official_pregame_injury"]]
    return dict(settled_with_price=len(rows), exact_schedule=sum("official_nba" in r for r in rows),
                exact_final=len(linked), unique_nba_game_ids=len({r["official_nba"]["game_id"] for r in linked}),
                with_official_box=len(box_rows), with_official_injury=len(injury_rows),
                with_both=sum(bool(r["official_box"]) for r in injury_rows),
                injury_player_status_rows=sum(len(r["official_pregame_injury"]["players"]) for r in injury_rows),
                overtime_boxes=sum(r["official_box"]["periods"] > 4 for r in box_rows),
                alias_joined=sum(bool(r["alias_witness_product_ids"]) for r in linked),
                join_status=dict(Counter(r["nba_join"] for r in rows)))


def prior_coverage(rows, fixtures, boxes, daily):
    daily_dates = {source["url"][-10:] for source in daily}
    targets = {row["official_nba"]["game_id"]: row["official_nba"] for row in rows
               if row.get("official_pregame_injury")}
    coverage = {}
    for gid, target in sorted(targets.items()):
        tip = utc(target["tipoff_utc"])
        target_day = date.fromisoformat(target["eastern_date"])
        missing = [day for offset in range(1, 8)
                   if (day := (target_day - timedelta(days=offset)).isoformat()) not in daily_dates]
        teams = {}
        for team in (target["home"], target["away"]):
            candidates = sorted((game for game in fixtures.values()
                                 if team in (game["home"], game["away"])
                                 and timedelta(0) < tip - utc(game["tipoff_utc"]) <= timedelta(days=7)),
                                key=lambda game: game["tipoff_utc"], reverse=True)
            teams[team] = dict(latest_observed_schedule=candidates[0] if candidates else None,
                               prior_box_ids=[game["game_id"] for game in candidates if game["game_id"] in boxes])
        coverage[gid] = dict(missing_complete_league_days=missing, teams=teams)
    return dict(target_games=len(coverage), complete_prior_seven_eastern_dates=sum(
                    not item["missing_complete_league_days"] for item in coverage.values()),
                both_teams_with_any_prior_schedule=sum(all(team["latest_observed_schedule"]
                    for team in item["teams"].values()) for item in coverage.values()),
                both_teams_with_any_prior_box=sum(all(team["prior_box_ids"]
                    for team in item["teams"].values()) for item in coverage.values()),
                games=coverage)


def run(source_roots, bundle):
    manifest = json.loads((PRICE_DIR / "manifest-v2.json").read_bytes())
    source_roots = [Path(p) for p in (source_roots or manifest["source_roots"])]
    if len(source_roots) != len(manifest["source_roots"]):
        raise ValueError("supply both original source roots in manifest order")
    receipts, evidence = read_bundle(bundle)
    modern, legacy, source_counts, exclusions, products, sources = read_betman(manifest, source_roots, evidence)
    fixtures, boxes, daily, nuggets = read_nba(evidence)
    aliases = independent_aliases(modern, fixtures)
    injuries = read_injuries(evidence)
    match_prices(modern, fixtures, aliases, boxes, injuries)
    match_prices(legacy, fixtures, aliases, boxes, injuries)
    for row in modern + legacy:
        report = row.get("official_pregame_injury")
        keys = "tipoff_utc home_team_id away_team_id"
        if report and fields(report, keys) != fields(row["official_nba"], keys):
            raise ValueError("injury schedule and final identity disagree")
    normalized = checked(gzip.decompress((PRICE_DIR / "normalized-price-records-v2.jsonl.gz").read_bytes()),
                         manifest["normalized_sha256"])
    v2 = [r for r in map(json.loads, normalized.splitlines()) if r["league"] == "NBA"]
    legacy_linked = [r for r in legacy if r["nba_join"] == "exact_schedule_and_final"]
    legacy_v2 = {r["payload"]["product_id"] for r in v2}
    stats = {archive: summarize([r for r in modern if r["archive"] == archive])
             for archive in ("manifest_v2", "additional_closed_round")}
    return dict(summary=dict(manifest_rounds=len(manifest["sources"]), rounds_with_nba=len(sources),
                             v2_nba_rows=len(v2), v2_rule_counts=dict(Counter(r["payload"]["settlement_rule"] for r in v2)),
                             v2_with_legacy_exact_final=sum(r["product_id"] in legacy_v2 for r in legacy_linked),
                             source_counts=source_counts, exclusions=exclusions,
                             modern_by_archive=stats, modern_total=summarize(modern),
                             legacy_screened_separately=summarize(legacy),
                             official_fixture_ids=len(fixtures), official_final_ids=sum(bool(g["final_score"]) for g in fixtures.values()),
                             official_box_ids=len(boxes), official_injury_ids=len(injuries),
                             verified_execution_prices=0), rule_sources=RULES,
                modern_market_rule="full_game_winner_2way_including_overtime",
                archived_nba_ref=NBA_REF, daily_sources=daily, official_team_schedule=nuggets,
                betman_sources=sources, product_inventory=products, bundled_receipts=receipts,
                aliases=aliases, prior_seven_day_coverage=prior_coverage(modern, fixtures, boxes, daily),
                modern_prices=modern, legacy_individual_final_score_witnesses=legacy_linked)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--source-root", action="append", help="Repeat twice in original manifest order to relocate raw rounds")
    cli.add_argument("--bundle", type=Path, default=HERE / "nba_http_evidence.zip")
    cli.add_argument("--output", type=Path, default=HERE / "nba_results.json")
    args = cli.parse_args()
    result = run(args.source_root, args.bundle)
    args.output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
