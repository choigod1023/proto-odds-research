"""일본 공식 사이트의 NPB 선발·순위와 J리그 순위를 수집한다.

네이버 해외스포츠 API는 일본 리그의 선발/시즌 코드가 늦게 채워지거나 비는
경우가 있다. 경기 자체를 못 찾았다는 이유로 화면의 팀 정보까지 모두 지우지
않도록 일본 주관 단체의 공개 페이지를 독립적인 fallback으로 사용한다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

KST = ZoneInfo("Asia/Seoul")
NPB_STARTERS_URL = "https://npb.jp/announcement/starter/"
NPB_STATS_URL = "https://npb.jp/bis/{season}/stats/std_{group}.html"
JLEAGUE_STANDINGS_URL = {
    "J1리그": "https://www.jleague.jp/j1/standings/",
    "J2리그": "https://www.jleague.jp/j2/standings/",
}
HTML_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 Chrome/120 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ja,en;q=0.8,ko;q=0.7",
}

# 일본 공식 표기 → 프로젝트가 사용하는 canonical 한글 팀명.
NPB_TEAM_KO = {
    "読売ジャイアンツ": "요미우리",
    "横浜DeNAベイスターズ": "요코하마",
    "阪神タイガース": "한신",
    "中日ドラゴンズ": "주니치",
    "広島東洋カープ": "히로시마",
    "東京ヤクルトスワローズ": "야쿠르트",
    "福岡ソフトバンクホークス": "소프트뱅크",
    "北海道日本ハムファイターズ": "닛폰햄",
    "オリックス・バファローズ": "오릭스",
    "東北楽天ゴールデンイーグルス": "라쿠텐",
    "埼玉西武ライオンズ": "세이부",
    "千葉ロッテマリーンズ": "지바롯데",
}
NPB_PROTO_CANON = {
    "소프트뱅": "소프트뱅크", "요코베이": "요코하마", "히로카프": "히로시마",
}

# 스포츠토토가 실제로 쓰는 축약명으로 바로 변환한다. 두 언어 문자열의 fuzzy
# match는 오탐 가능성이 높으므로 검증된 2026 J1/J2 40개 구단을 명시한다.
JLEAGUE_TEAM_KO = {
    "柏レイソル": "가시와R", "鹿島アントラーズ": "가시마A",
    "サンフレッチェ広島": "산프히로", "ＦＣ町田ゼルビア": "마치다Z",
    "横浜Ｆ・マリノス": "요코마리", "セレッソ大阪": "C오사카",
    "水戸ホーリーホック": "미토홀리", "ヴィッセル神戸": "비셀고베",
    "ファジアーノ岡山": "오카야마", "ガンバ大阪": "G오사카",
    "ＦＣ東京": "FC도쿄", "アビスパ福岡": "후쿠오카",
    "川崎フロンターレ": "가와사키", "名古屋グランパス": "나고야G",
    "清水エスパルス": "시미즈S", "Ｖ・ファーレン長崎": "V바렌나",
    "東京ヴェルディ": "도쿄베르", "京都サンガF.C.": "교토상가",
    "浦和レッズ": "우라와R", "ジェフユナイテッド千葉": "제프유나",
    "ＲＢ大宮アルディージャ": "RB오미야", "湘南ベルマーレ": "쇼난벨마",
    "カターレ富山": "K도야마", "栃木シティ": "도치기시",
    "藤枝ＭＹＦＣ": "후지에다", "モンテディオ山形": "야마가타",
    "横浜ＦＣ": "요코FC", "アルビレックス新潟": "A니가타",
    "大分トリニータ": "오이타T", "サガン鳥栖": "사간도스",
    "ベガルタ仙台": "V센다이", "ブラウブリッツ秋田": "B아키타",
    "ジュビロ磐田": "J이와타", "北海道コンサドーレ札幌": "C삿포로",
    "いわきＦＣ": "이와키", "ヴァンラーレ八戸": "하치노헤",
    "テゲバジャーロ宮崎": "미야자키", "徳島ヴォルティス": "도쿠시마",
    "ヴァンフォーレ甲府": "V고후", "ＦＣ今治": "이마바리",
}


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _number(value, integer: bool = False):
    text = _clean(value).replace(",", "")
    if not text or text in ("-", "--"):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if integer else number


def _proto_start(value: str, now: datetime) -> datetime | None:
    match = re.search(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})", str(value or ""))
    if not match:
        return None
    month, day, hour, minute = map(int, match.groups())
    candidates = []
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            candidates.append(datetime(year, month, day, hour, minute, tzinfo=KST))
        except ValueError:
            pass
    return min(candidates, key=lambda x: abs((x - now).total_seconds())) if candidates else None


def _dated(month: int, day: int, hour: int, minute: int, now: datetime) -> datetime:
    candidates = [datetime(year, month, day, hour, minute, tzinfo=KST)
                  for year in (now.year - 1, now.year, now.year + 1)]
    return min(candidates, key=lambda x: abs((x - now).total_seconds()))


def _html(session: requests.Session, url: str) -> str:
    response = session.get(url, headers=HTML_HEADERS, timeout=30)
    response.raise_for_status()
    # 두 공식 사이트 모두 UTF-8이지만 간혹 text/html 헤더에 charset이 빠진다.
    return response.content.decode("utf-8", errors="replace")


def parse_npb_starters(html: str, now: datetime | None = None) -> list[dict]:
    """NPB `予告先発投手` 페이지를 경기 단위로 변환한다."""
    now = now or datetime.now(KST)
    soup = BeautifulSoup(html, "lxml")
    heading = soup.select_one(".contents h4") or soup.find("h4")
    date_match = re.search(r"(\d{1,2})月(\d{1,2})日", _clean(heading.get_text(" ") if heading else ""))
    if not date_match:
        return []
    month, day = map(int, date_match.groups())
    out = []
    for unit in soup.select(".starting_wrap_cl .unit, .starting_wrap_pl .unit"):
        sides = {}
        teams = {}
        for side, css in (("home", ".team_left"), ("away", ".team_right")):
            box = unit.select_one(css)
            logo = box.find("img", alt=True) if box else None
            name = box.select_one("span") if box else None
            if not logo or not name:
                break
            raw_team = _clean(logo.get("alt"))
            teams[side] = NPB_TEAM_KO.get(raw_team, raw_team)
            link = name.find_parent("a")
            player_match = re.search(r"/players/(\d+)\.html", link.get("href", "") if link else "")
            sides[side] = {
                "name": _clean(name.get_text(" ")), "announced": True,
                "player_id": player_match.group(1) if player_match else None,
            }
        if len(sides) != 2:
            continue
        info = unit.select_one(".info")
        time_match = re.search(r"(\d{1,2}):(\d{2})", _clean(info.get_text(" ") if info else ""))
        if not time_match:
            continue
        hour, minute = map(int, time_match.groups())
        start = _dated(month, day, hour, minute, now)
        out.append({
            "league": "NPB", "game_datetime": start.isoformat(),
            "home_team": teams["home"], "away_team": teams["away"],
            "starters": sides,
        })
    return out


def parse_npb_standings(html: str) -> dict[str, dict]:
    """NPB 센트럴/퍼시픽 팀 승패표 한 페이지를 파싱한다."""
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for row in soup.select("tr.ststats"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 7:
            continue
        raw_team = _clean(cells[0].get_text(" "))
        team = NPB_TEAM_KO.get(raw_team, raw_team)
        if team in out:  # PC/모바일 표가 중복될 수 있다.
            continue
        out[team] = {
            "rank": len(out) + 1,
            "played": _number(cells[1].get_text(" "), True),
            "wins": _number(cells[2].get_text(" "), True),
            "losses": _number(cells[3].get_text(" "), True),
            "draws": _number(cells[4].get_text(" "), True),
            "pct": _number(cells[5].get_text(" ")),
            "games_behind": _number(cells[6].get_text(" ")),
        }
    return out


def parse_jleague_standings(html: str) -> dict[str, dict]:
    """J.LEAGUE.jp의 서버 렌더링 순위표를 프로젝트 팀명으로 바꾼다."""
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for row in soup.select("tr.o-table__row"):
        rank_cell = row.select_one(".o-table__cell--ranking")
        club_cell = row.select_one(".o-table__cell--club")
        club_link = club_cell.select_one("a") if club_cell else None
        if not rank_cell or not club_link:
            continue
        raw_team = _clean(club_link.get_text(" "))
        team = JLEAGUE_TEAM_KO.get(raw_team, raw_team)
        if team in out:
            continue

        def cell(name: str):
            found = row.select_one(f".o-table__cell--{name}")
            return _clean(found.get_text(" ") if found else "")

        played = _number(cell("match"), True)
        scored = _number(cell("goal-scored"), True)
        conceded = _number(cell("goal-lost"), True)
        form_cell = row.select_one(".o-table__cell--past-games")
        form = "".join(x.get_text(strip=True) for x in form_cell.select(".o-table__game-state")) if form_cell else ""
        out[team] = {
            "rank": _number(rank_cell.get_text(" "), True),
            "points": _number(cell("point"), True), "played": played,
            "wins": _number(cell("win"), True), "draws": _number(cell("draw"), True),
            "losses": _number(cell("loss"), True),
            "goals_for": scored, "goals_against": conceded,
            "goals_per_game": round(scored / played, 2) if played and scored is not None else None,
            "conceded_per_game": round(conceded / played, 2) if played and conceded is not None else None,
            "last_five": form or None,
        }
    return out


def collect_jleague_standings(session: requests.Session,
                              leagues: set[str]) -> dict[str, dict[str, dict]]:
    out = {}
    for league in sorted(leagues & set(JLEAGUE_STANDINGS_URL)):
        try:
            out[league] = parse_jleague_standings(_html(session, JLEAGUE_STANDINGS_URL[league]))
        except requests.RequestException:
            out[league] = {}
    return out


def jleague_record_for(table: dict[str, dict], proto_team: str) -> dict | None:
    """공식 팀명을 스포츠토토 축약명으로 바꾼 표에서 현재 팀 기록을 찾는다."""
    return table.get(str(proto_team or ""))


def _load_npb_proto_games(picks_path: Path, now: datetime) -> list[dict]:
    try:
        doc = json.loads(picks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out, seen = [], set()
    for game in doc.get("live", []):
        if game.get("sport") != "bs" or game.get("league") != "NPB":
            continue
        start = _proto_start(game.get("date", ""), now)
        key = (game.get("date"), game.get("home"), game.get("away"))
        if not start or key in seen:
            continue
        seen.add(key)
        out.append({**game, "_start": start})
    return out


def _npb_canon(team: str) -> str:
    return NPB_PROTO_CANON.get(str(team or ""), str(team or ""))


def collect_npb_games(picks_path: Path, session: requests.Session,
                      now: datetime | None = None) -> list[dict]:
    """판매 중 NPB 경기마다 공식 선발(발표 시)과 양 팀 순위를 만든다."""
    now = now or datetime.now(KST)
    standings = {}
    for group in ("c", "p"):
        try:
            standings.update(parse_npb_standings(
                _html(session, NPB_STATS_URL.format(season=now.year, group=group))))
        except requests.RequestException:
            continue
    try:
        announced = parse_npb_starters(_html(session, NPB_STARTERS_URL), now)
    except requests.RequestException:
        announced = []

    starter_index = {}
    for game in announced:
        start = datetime.fromisoformat(game["game_datetime"])
        key = (start.date().isoformat(), game["home_team"], game["away_team"])
        starter_index[key] = game

    out = []
    stamp = now.isoformat(timespec="seconds")
    for proto in _load_npb_proto_games(picks_path, now):
        home, away = _npb_canon(proto.get("home")), _npb_canon(proto.get("away"))
        key = (proto["_start"].date().isoformat(), home, away)
        official = starter_index.get(key) or {}
        teams = {}
        if standings.get(home):
            teams["home"] = standings[home]
        if standings.get(away):
            teams["away"] = standings[away]
        # 일본 공식 자료를 하나도 못 받았으면 네이버/직전 캐시가 이기게 둔다.
        if not official and not teams:
            continue
        out.append({
            "league": "NPB", "game_id": None,
            "game_datetime": proto["_start"].isoformat(),
            "home_team": proto.get("home"), "away_team": proto.get("away"),
            "starters": official.get("starters") or {}, "teams": teams,
            "unavailable": {}, "lineups": {},
            "source": "NPB.jp 일본야구기구 공식 선발·순위",
            "source_url": NPB_STARTERS_URL, "updated_at": stamp,
        })
    return out
