"""Capture contemporaneous NBA starting fives and exact G101 winner prices."""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

import requests

from scripts.collect_basketball_comparison_sources import TEAM_NAMES, nba_game_page
from scripts.collect_proto_comparison_prices import DETAIL, MAX_BODY_BYTES
from src.prospective_tipster_pairs import fields, milliseconds

NBA_SCHEDULE = "https://www.nba.com/games?date="
NBA_LINEUPS = "https://stats.nba.com/js/data/leaders/00_daily_lineups_{}.json"
ROUND_HINT = "https://proto-odds-web.fly.dev/api/live-odds"
EASTERN = ZoneInfo("America/New_York")
KST = timezone(timedelta(hours=9))
BETMAN_CODES = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "PHO": "PHX", "SA": "SAS"}
POSITIONS = {"PG", "SG", "SF", "PF", "C"}
PRICE_FIELDS = "leagueCode homeId awayId gameDate betId betTypId"
MAX_GAP = timedelta(minutes=3)
MIN_SETTLED = 50


def utc_now():
    return datetime.now(timezone.utc)


def instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("timestamp needs a timezone")
    return parsed.astimezone(timezone.utc)


def fetch(url, payload=None):
    headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
    if payload is not None:
        headers.update({"Referer": "https://www.betman.co.kr/main/mainPage/gamebuy/gameSlip.do",
                        "X-Requested-With": "XMLHttpRequest"})
    with requests.request("POST" if payload else "GET", url, json=payload, headers=headers,
                          timeout=(5, 10), stream=True, allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code} from {url}")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                raise ValueError("source response too large")
        received = utc_now()
    return receipt(url, received, bytes(body))


def receipt(url, received, raw):
    return {"url": url, "received_at": received.isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(), "status": 200}, raw


def schedule(snapshot, day):
    meta, raw = snapshot
    if meta["url"] != NBA_SCHEDULE + day.isoformat():
        raise ValueError("wrong NBA schedule date")
    records = nba_game_page(dict(meta, body_sha256=meta["sha256"]), raw)
    fixtures = {}
    for row in records:
        if row["kind"] != "fixture":
            continue
        game_id = row["payload"]["source_fixture_id"]
        if game_id in fixtures:
            raise ValueError("duplicate NBA fixture")
        fixtures[game_id] = row
    return fixtures


def confirmed(game, fixture):
    if game.get("gameStatus") != 1:
        return None
    starters = {}
    active = {}
    payload = fixture["payload"]
    for side in ("home", "away"):
        team = game[side + "Team"]
        if (team["teamAbbreviation"], str(team["teamId"])) != (
                payload[side + "_tricode"], str(payload[side + "_team_id"])):
            return None
        available = [p for p in team["players"] if p.get("rosterStatus") == "Active"
                     and p.get("lineupStatus") == "Confirmed" and p.get("teamId") == team["teamId"]
                     and type(p.get("personId")) is int and p["personId"] > 0]
        five = [p for p in available if p.get("position") in POSITIONS]
        ids = [p["personId"] for p in available]
        if len(five) != 5 or {p["position"] for p in five} != POSITIONS or len(set(ids)) != len(ids):
            return None
        starters[side] = [{k: p.get(k) for k in ("personId", "playerName", "position", "timestamp")}
                          for p in five]
        active[side] = ids
    if set(active["home"]) & set(active["away"]):
        return None
    return starters, active


def product(row):
    return (fields(row, "itemCode leagueCode leagueName betId betTypId betNm betTypNm") ==
            ("BK", "BK002", "NBA", "3", "2", "농구 승패", "일반 승패")
            and fields(row, "live unsetSchedule unsetEndDate") == ("N", "N", "N")
            and fields(row, "winHandi drawHandi loseHandi") == (0, 0, 0)
            and row.get("gameSubject") in (None, ""))


def betman_round(snapshot, round_id):
    meta, raw = snapshot
    if meta["url"] != DETAIL or not re.fullmatch(r"[0-9]{6}", round_id):
        raise ValueError("invalid Betman receipt")
    doc = json.loads(raw)
    lottery = doc["currentLottery"]
    if (str(doc["gmTs"]), lottery["gmId"], str(lottery["gmTs"])) != (round_id, "G101", round_id):
        raise ValueError("different Betman round")
    keys = doc["compSchedules"]["keys"]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate Betman columns")
    rows = {}
    for values in doc["compSchedules"]["datas"]:
        row = dict(zip(keys, values, strict=True))
        number = row["matchSeq"]
        if type(number) is not int or number <= 0 or number in rows:
            raise ValueError("invalid or duplicate Betman product")
        rows[number] = row
    return lottery, rows


def quotes(snapshot, round_id):
    lottery, rows = betman_round(snapshot, round_id)
    observed = instant(snapshot[0]["received_at"])
    if lottery.get("saleProgress") is not True or lottery.get("saleStatus") != "SaleProgress":
        return []
    opening, close = (milliseconds(lottery[k]) for k in ("saleStartDate", "saleEndDate"))
    selected = []
    for number, row in rows.items():
        if (not product(row) or fields(row, "protoStatus gameReject buyReject") != ("2", "0", "0")
                or row.get("gameResult") is not None or fields(row, "winTxt drawTxt loseTxt") != ("승", "-", "패")
                or row.get("drawAllot") != 0):
            continue
        start = milliseconds(row["gameDate"])
        deadline = min(milliseconds(row["endDate"]), close, start)
        odds = fields(row, "winAllot loseAllot")
        if not opening <= observed < deadline or not all(type(x) in (int, float) and math.isfinite(x) and x > 1 for x in odds):
            continue
        teams = tuple(BETMAN_CODES.get(v, v) for v in fields(row, "homeId awayId"))
        if teams[0] == teams[1] or any(t not in TEAM_NAMES for t in teams):
            continue
        selected.append((teams, start, dict(round=round_id, number=number, odds=list(odds),
                        deadline=deadline.isoformat(), identity=list(fields(row, PRICE_FIELDS)))))
    return selected


def match_records(day, schedule_snapshot, lineup_snapshot, price_snapshots):
    fixtures = schedule(schedule_snapshot, day)
    meta, body = lineup_snapshot
    if meta["url"] != NBA_LINEUPS.format(day.strftime("%Y%m%d")):
        raise ValueError("wrong NBA lineup date")
    by_fixture = defaultdict(list)
    for round_id, snapshot in price_snapshots.items():
        for teams, kickoff, quote in quotes(snapshot, round_id):
            by_fixture[teams, kickoff].append((quote, snapshot))
    games = json.loads(body)["games"]
    ids = Counter(g["gameId"] for g in games)
    for game in games:
        game_id = game["gameId"]
        fixture = fixtures.get(game_id)
        if not fixture or ids[game_id] != 1 or not re.fullmatch(r"00[23][0-9]{7}", game_id):
            continue
        lineup = confirmed(game, fixture)
        if lineup is None:
            continue
        kickoff = instant(fixture["kickoff"])
        teams = tuple(fixture["payload"][side + "_tricode"] for side in ("home", "away"))
        offers = by_fixture[teams, kickoff]
        if len(offers) != 1:
            continue
        quote, price_snapshot = offers[0]
        evidence = {"schedule": schedule_snapshot, "lineup": lineup_snapshot, "price": price_snapshot}
        received = {k: instant(s[0]["received_at"]) for k, s in evidence.items()}
        paired = max(received.values())
        if (paired >= instant(quote["deadline"]) or paired > utc_now()
                or abs(received["lineup"] - received["price"]) > MAX_GAP):
            continue
        starters, active = lineup
        yield dict(game_id=game_id, kickoff=kickoff.isoformat(), teams=list(teams),
                   paired_at=paired.isoformat(), starters=starters, active=active, **quote,
                   receipts={k: s[0] for k, s in evidence.items()}), evidence


def database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS bodies (sha256 TEXT PRIMARY KEY, gzip BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS captures (game_id TEXT PRIMARY KEY, record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS forecasts (game_id TEXT PRIMARY KEY, record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settlements (game_id TEXT PRIMARY KEY, record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, meta TEXT NOT NULL, body BLOB NOT NULL);
    """)
    return db


def save(db, row, evidence):
    row = dict(row, stored_at=utc_now().isoformat())
    with db:
        inserted = db.execute("INSERT OR IGNORE INTO captures VALUES (?, ?)",
                              (row["game_id"], json.dumps(row, ensure_ascii=False))).rowcount
        if inserted:
            for meta, raw in evidence.values():
                db.execute("INSERT OR IGNORE INTO bodies VALUES (?, ?)",
                           (meta["sha256"], gzip.compress(raw, compresslevel=3, mtime=0)))
    return bool(inserted)


def fetch_round(round_id):
    return fetch(DETAIL, {"gmId": "G101", "gmTs": round_id, "gameYear": "20" + round_id[:2],
                          "_sbmInfo": {"debugMode": "false"}})


def cached_schedule(db, day):
    url = NBA_SCHEDULE + day.isoformat()
    stored = db.execute("SELECT meta, body FROM cache WHERE key = ?", (url,)).fetchone()
    if stored:
        meta = json.loads(stored[0])
        if timedelta(0) <= utc_now() - instant(meta["received_at"]) < timedelta(minutes=20):
            return meta, gzip.decompress(stored[1])
    meta, raw = fetch(url)
    schedule((meta, raw), day)
    with db:
        db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                   (url, json.dumps(meta), gzip.compress(raw, compresslevel=3)))
        db.execute("DELETE FROM cache WHERE key >= ? AND key < ?",
                   (NBA_SCHEDULE, NBA_SCHEDULE + (day - timedelta(days=1)).isoformat()))
    return meta, raw


def collect(db):
    now = utc_now()
    day = now.astimezone(EASTERN).date()
    source = cached_schedule(db, day)
    seen = {r[0] for r in db.execute("SELECT game_id FROM captures")}
    upcoming = {g: f for g, f in schedule(source, day).items() if g not in seen
                and now < instant(f["kickoff"]) <= now + timedelta(minutes=45)}
    if not upcoming:
        return {"nearby_uncaptured": 0, "captured": 0}
    lineup = fetch(NBA_LINEUPS.format(day.strftime("%Y%m%d")))
    count = sum(1 for g in json.loads(lineup[1])["games"] if g["gameId"] in upcoming
                and confirmed(g, upcoming[g["gameId"]]))
    if not count:
        return {"nearby_uncaptured": len(upcoming), "confirmed": 0, "captured": 0}
    _, hints = fetch(ROUND_HINT)
    rounds = sorted({r for r in json.loads(hints)["rounds"] if type(r) is int and 1 <= r < 1000})[-3:]
    prices = {}
    for number in rounds:
        round_id = f"{now.astimezone(KST).year % 100:02d}{number:04d}"
        try:
            prices[round_id] = fetch_round(round_id)
        except (requests.RequestException, ValueError) as exc:
            print(f"Betman {round_id}: {type(exc).__name__}")
    saved = sum(save(db, row, evidence) for row, evidence in match_records(day, source, lineup, prices))
    return {"nearby_uncaptured": len(upcoming), "confirmed": count, "captured": saved}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    with database(args.db) as db:
        print(json.dumps(collect(db), ensure_ascii=False))


if __name__ == "__main__":
    main()
