"""Collect official MLB/EPL/K League results; no production dependencies.

Example (Python 3.11+, IANA tzdata required, e.g. pip install tzdata on Windows):
  python scripts/collect_official_international.py --games-csv PATH --output PATH

Output is a JSON array, filtered by KST kickoff year (default 2025). MLB includes
regular season and postseason (R,F,D,L,W), not spring training or All-Star games.
EPL reads both overlapping seasons. Cache contains original response bytes and a
URL/SHA256 manifest. --offline replays that cache without HTTP. Existing artifacts
are never replaced with different content unless --replace-output is supplied
(only normalized output and manifest; raw response cache is immutable).

MLB gamePk is the identity, NEVER date/team pair: doubleheaders remain separate.
Resumed games retain original kickoff and final score; postponed schedule entries
are excluded. kickoff is the source's scheduled kickoff, not measured first pitch.
Consumers must use kickoff AND game_number for same-day MLB matching and reject
ambiguous matches; this collector does not match/merge results into betting rows.
kickoff_is_tbd marks MLB placeholder times: do not use them for exact time matching.
K League 1/2 are read monthly; league/year/meetSeq/gameId form the identity.
Promotion/relegation playoffs (meetSeq=3) are a different competition and excluded.
K League 2's own semifinal/playoff games (meetSeq=2) are included.
Official website backends have no assumed public SLA or redistribution license.
"""

import argparse
import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

KST = timezone(timedelta(hours=9))
EPL_URL = "https://sdp-prem-prod.premier-league-prod.pulselive.com/api/v2/matches"
MLB_URL = "https://statsapi.mlb.com/api/v1/schedule"
KLEAGUE_URL = "https://www.kleague.com/getScheduleList.do"
MLB_TYPES = {"R", "F", "D", "L", "W"}
ALIASES = {
    "K리그1": {"서울": "FC서울", "안양": "FC안양", "강원": "강원FC",
               "광주": "광주FC", "김천": "김천상무", "대구": "대구FC",
               "대전": "대전하나", "수원FC": "수원FC", "울산": "울산HDFC",
               "전북": "전북현대", "제주": "제주SKFC", "포항": "포항스틸"},
    "K리그2": {"경남": "경남FC", "김포": "김포FC", "부산": "부산아이",
               "부천": "부천FC", "서울E": "서울이랜", "성남": "성남FC",
               "수원": "수원삼성", "안산": "안산그리", "인천": "인천유나",
               "전남": "전남드래", "천안": "천안시티", "충남아산": "충남아산",
               "충북청주": "충북청주", "화성": "화성FC"},
    "MLB": dict(zip([
        "Arizona Diamondbacks", "Athletics", "Atlanta Braves", "Baltimore Orioles",
        "Boston Red Sox", "Chicago Cubs", "Chicago White Sox", "Cincinnati Reds",
        "Cleveland Guardians", "Colorado Rockies", "Detroit Tigers", "Houston Astros",
        "Kansas City Royals", "Los Angeles Angels", "Los Angeles Dodgers",
        "Miami Marlins", "Milwaukee Brewers", "Minnesota Twins", "New York Mets",
        "New York Yankees", "Philadelphia Phillies", "Pittsburgh Pirates",
        "San Diego Padres", "San Francisco Giants", "Seattle Mariners",
        "St. Louis Cardinals", "Tampa Bay Rays", "Texas Rangers", "Toronto Blue Jays",
        "Washington Nationals",
    ], [
        "애리다이", "애슬레틱", "애틀브레", "볼티오리", "보스레드", "시카컵스",
        "시카화이", "신시레즈", "클리가디", "콜로로키", "디트타이", "휴스애스",
        "캔자로얄", "LA에인절", "LA다저스", "마이말린", "밀워브루", "미네트윈",
        "뉴욕메츠", "뉴욕양키", "필라필리", "피츠파이", "샌디파드", "샌프자이",
        "시애매리", "세인카디", "탬파레이", "텍사레인", "토론블루", "워싱내셔",
    ], strict=True)),
    "EPL": dict(zip([
        "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton and Hove Albion",
        "Burnley", "Chelsea", "Crystal Palace", "Everton", "Fulham", "Ipswich Town",
        "Leeds United", "Leicester City", "Liverpool", "Manchester City",
        "Manchester United", "Newcastle United", "Nottingham Forest", "Southampton",
        "Sunderland", "Tottenham Hotspur", "West Ham United", "Wolverhampton Wanderers",
    ], [
        "아스널", "A빌라", "본머스", "브렌트퍼", "브라이턴", "번리", "첼시",
        "크리스털", "에버턴", "풀럼", "입스위치", "리즈U", "레스터C", "리버풀",
        "맨체스C", "맨체스U", "뉴캐슬U", "노팅엄F", "사우샘프", "선덜랜드",
        "토트넘", "웨스트햄", "울버햄튼",
    ], strict=True)),
}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def save_once(path, data, replace=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            if not replace:
                raise ValueError(f"Refusing to overwrite different content: {path}")
            path.write_bytes(data)
    else:
        with path.open("xb") as stream:
            stream.write(data)


class Cache:
    def __init__(self, directory, offline=False):
        self.directory = Path(directory)
        self.offline = offline
        self.manifest = []

    def get(self, url, body=None):
        request_data = json_bytes(body) if body is not None else None
        identity = url.encode() + (request_data or b"")
        path = self.directory / (hashlib.sha256(identity).hexdigest() + ".json")
        if path.exists():
            raw = path.read_bytes()
        elif self.offline:
            raise FileNotFoundError(f"Missing cached response: {url}")
        else:
            for attempt in range(3):
                try:
                    request = urllib.request.Request(url, data=request_data, headers={
                        "User-Agent": "official-results-research/1.0", "Content-Type": "application/json; charset=utf-8"})
                    with urllib.request.urlopen(request, timeout=45) as response:
                        raw = response.read()
                    break
                except urllib.error.HTTPError as error:
                    if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
                except (urllib.error.URLError, TimeoutError):
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
            json.loads(raw)  # never cache an HTML/error response as JSON
            save_once(path, raw)
        self.manifest.append({"url": url, "method": "POST" if body is not None else "GET", "body": body,
                              "file": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
        return json.loads(raw)


def vocabulary(path, year):
    names = defaultdict(set)
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["year"] != str(year) or row["league"] not in ALIASES:
                continue
            for side in ("home", "away"):
                # Source rows may contain a leading result score or trailing handicap.
                name = re.sub(r"^\d+\s+", "", row[side].strip())
                name = re.sub(r"\s+[+-]?\d+(?:\.\d+)?$", "", name)
                names[row["league"]].add(name)
    return names


def result_row(league, ident, kickoff, home, away, hs, aws, url, names):
    aliases = ALIASES[league]
    for team in (home, away):
        if team not in aliases or aliases[team] not in names[league]:
            raise ValueError(f"Unverified {league} alias: {team}")
    for score in (hs, aws):
        if type(score) is not int or score < 0:
            raise ValueError(f"Invalid score for {league}:{ident}: {score}")
    if kickoff.tzinfo is None:
        raise ValueError("Naive kickoff")
    local = kickoff.astimezone(KST)
    return {
        "sport": "bs" if league == "MLB" else "sc", "league": league,
        "date": local.date().isoformat(), "kickoff": local.isoformat(),
        "home": aliases[home], "away": aliases[away], "home_score": hs, "away_score": aws,
        "official_id": f"{league.lower()}:{ident}", "source_url": url,
        "raw_team_names": {"home": home, "away": away},
    }


def normalize_mlb(payload, url, names, year):
    results = {}
    for day in payload["dates"]:
        for game in day["games"]:
            if game["gameType"] not in MLB_TYPES:
                continue
            if game["status"]["detailedState"] not in ("Final", "Completed Early"):
                continue
            kickoff = datetime.fromisoformat(game.get("resumedFrom") or game["gameDate"])
            if kickoff.astimezone(KST).year != year:
                continue
            home, away = game["teams"]["home"], game["teams"]["away"]
            row = result_row("MLB", game["gamePk"], kickoff, home["team"]["name"],
                             away["team"]["name"], home.get("score"), away.get("score"), url, names)
            number, dh = game["gameNumber"], game["doubleHeader"]
            if type(number) is not int or number not in (1, 2) or dh not in ("N", "Y", "S"):
                raise ValueError(f"Unknown MLB game numbering: {game['gamePk']}")
            row.update(game_number=number, doubleheader=dh, game_type=game["gameType"],
                       official_date=game["officialDate"],
                       kickoff_is_tbd=bool(game["status"].get("startTimeTBD")))
            old = results.get(row["official_id"])
            if old is not None and old != row:
                raise ValueError(f"Conflicting duplicate MLB result: {row['official_id']}")
            results[row["official_id"]] = row
    return list(results.values())


def collect_epl(cache, names, year):
    rows = []
    ids = set()
    for season in (year - 1, year):
        cursor = None
        cursors = set()
        for _ in range(100):
            params = {"competition": 8, "season": season, "_limit": 100}
            if cursor:
                params["_next"] = cursor
            url = EPL_URL + "?" + urllib.parse.urlencode(params)
            payload = cache.get(url)
            for game in payload["data"]:
                if game["competitionId"] != "8" or game["season"] != str(season):
                    raise ValueError("EPL endpoint ignored competition/season filter")
                if game["matchId"] in ids:
                    raise ValueError(f"Duplicate EPL page result: {game['matchId']}")
                ids.add(game["matchId"])
                kickoff = datetime.fromisoformat(game["kickoff"]).replace(tzinfo=ZoneInfo(game["kickoffTimezoneString"]))
                if kickoff.astimezone(KST).year != year or game["period"] != "FullTime":
                    continue
                home, away = game["homeTeam"], game["awayTeam"]
                rows.append(result_row("EPL", game["matchId"], kickoff, home["name"], away["name"],
                                       home.get("score"), away.get("score"), url, names))
            cursor = payload["pagination"].get("_next")
            if not cursor:
                break
            if cursor in cursors:
                raise ValueError("Repeated EPL pagination cursor")
            cursors.add(cursor)
        else:
            raise ValueError("EPL pagination limit exceeded")
    return rows


def validate(rows, year):
    ids = set()
    groups = defaultdict(list)
    for row in rows:
        if row["official_id"] in ids:
            raise ValueError("Duplicate official_id")
        ids.add(row["official_id"])
        kickoff = datetime.fromisoformat(row["kickoff"])
        if kickoff.utcoffset() != timedelta(hours=9) or kickoff.year != year or kickoff.date().isoformat() != row["date"]:
            raise ValueError("Invalid KST date")
        if row["league"] == "MLB":
            groups[(row["official_date"], row["home"], row["away"])].append(row)
    for group in groups.values():
        if len(group) > 1:
            if len(group) != 2 or {r["game_number"] for r in group} != {1, 2} or any(r["doubleheader"] == "N" for r in group):
                raise ValueError(f"Ambiguous same-day MLB identity: {[r['official_id'] for r in group]}")
    return {"rows": len(rows), "by_league": dict(Counter(r["league"] for r in rows)),
            "mlb_game_types": dict(Counter(r["game_type"] for r in rows if r["league"] == "MLB")),
            "mlb_doubleheader_pairs": sum(len(g) == 2 for g in groups.values())}


def collect_kleague(cache, names, year):
    rows, ids = [], set()
    for league_id in (1, 2):
        league = f"K리그{league_id}"
        for month in range(1, 13):
            body = {"leagueId": str(league_id), "teamId": "", "ticketStatus": "",
                    "year": str(year), "month": f"{month:02}", "ticketYn": "N"}
            payload = cache.get(KLEAGUE_URL, body)
            if payload.get("resultCode") != "200":
                raise ValueError(f"K League request failed: {body}: {payload.get('resultMsg')}")
            for game in payload["data"]["scheduleList"]:
                if game["year"] != year or game["leagueId"] != league_id:
                    raise ValueError("K League endpoint ignored requested league/year")
                if game["meetSeq"] != league_id:
                    continue  # Promotion playoffs have their own IDs, restarting at 1.
                if game["endYn"] != "Y" or game["gameStatus"] != "FE":
                    continue
                kickoff = datetime.strptime(game["gameDate"] + " " + game["gameTime"], "%Y.%m.%d %H:%M").replace(tzinfo=KST)
                if kickoff.year != year or kickoff.month != month:
                    raise ValueError("K League endpoint ignored requested month")
                row = result_row(league, f"{year}:{game['meetSeq']}:{game['gameId']}", kickoff,
                                 game["homeTeamName"], game["awayTeamName"],
                                 game["homeGoal"], game["awayGoal"], KLEAGUE_URL, names)
                row["source_params"] = body
                if row["official_id"] in ids:
                    raise ValueError("Duplicate K League game identity")
                ids.add(row["official_id"])
                rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--games-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--replace-output", action="store_true")
    args = parser.parse_args()
    cache = Cache(args.cache_dir or args.output.parent / "official-international-cache", args.offline)
    names = vocabulary(args.games_csv, args.year)
    # One day of padding captures UTC games crossing the KST year boundary.
    params = {"sportId": 1, "startDate": f"{args.year - 1}-12-31",
              "endDate": f"{args.year}-12-31", "gameTypes": ",".join(sorted(MLB_TYPES))}
    url = MLB_URL + "?" + urllib.parse.urlencode(params)
    rows = (normalize_mlb(cache.get(url), url, names, args.year)
            + collect_epl(cache, names, args.year) + collect_kleague(cache, names, args.year))
    rows.sort(key=lambda r: (r["kickoff"], r["league"], r["official_id"]))
    summary = validate(rows, args.year)
    if any(summary["by_league"].get(league, 0) < 100 for league in ALIASES):
        raise ValueError("Insufficient annual coverage")
    manifest = {"year": args.year, "games_csv_sha256": hashlib.sha256(args.games_csv.read_bytes()).hexdigest(),
                "output_sha256": hashlib.sha256(json_bytes(rows)).hexdigest(),
                "summary": summary, "sources": cache.manifest}
    save_once(cache.directory / "manifest.json", json_bytes(manifest), args.replace_output)
    save_once(args.output, json_bytes(rows), args.replace_output)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
