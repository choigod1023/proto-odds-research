"""일본 공식 사이트의 NPB 선발·순위와 J리그 순위를 수집한다.

네이버 해외스포츠 API는 일본 리그의 선발/시즌 코드가 늦게 채워지거나 비는
경우가 있다. 경기 자체를 못 찾았다는 이유로 화면의 팀 정보까지 모두 지우지
않도록 일본 주관 단체의 공개 페이지를 독립적인 fallback으로 사용한다.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from runtime_db import load_artifact
from bs4 import BeautifulSoup
try:
    from .npb_lineups import (collect_npb_official_lineups, collect_npb_pitcher_stats,
                              collect_recent_npb_lineups, find_npb_player_stats)
except ImportError:  # python src/player_info.py처럼 스크립트로 실행할 때
    from npb_lineups import (collect_npb_official_lineups,
                             collect_npb_pitcher_stats,
                             collect_recent_npb_lineups,
                             find_npb_player_stats)

KST = ZoneInfo("Asia/Seoul")
NPB_STARTERS_URL = "https://npb.jp/announcement/starter/"
NPB_PLAYER_URL = "https://npb.jp/bis/players/{player_id}.html"
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

# NPB 선수 프로필의 가나 독음을 화면용 한글 표기로 바꾼다. 일본어 원문은
# native_name에 남겨 원자료 대조가 가능하게 하고, 화면의 name만 한글로 쓴다.
_KANA_KO = {
    "あ": "아", "い": "이", "う": "우", "え": "에", "お": "오",
    "か": "카", "き": "키", "く": "쿠", "け": "케", "こ": "코",
    "が": "가", "ぎ": "기", "ぐ": "구", "げ": "게", "ご": "고",
    "さ": "사", "し": "시", "す": "스", "せ": "세", "そ": "소",
    "ざ": "자", "じ": "지", "ず": "즈", "ぜ": "제", "ぞ": "조",
    "た": "다", "ち": "치", "つ": "츠", "て": "데", "と": "토",
    "だ": "다", "ぢ": "지", "づ": "즈", "で": "데", "ど": "도",
    "な": "나", "に": "니", "ぬ": "누", "ね": "네", "の": "노",
    "は": "하", "ひ": "히", "ふ": "후", "へ": "헤", "ほ": "호",
    "ば": "바", "び": "비", "ぶ": "부", "べ": "베", "ぼ": "보",
    "ぱ": "파", "ぴ": "피", "ぷ": "푸", "ぺ": "페", "ぽ": "포",
    "ま": "마", "み": "미", "む": "무", "め": "메", "も": "모",
    "や": "야", "ゆ": "유", "よ": "요",
    "ら": "라", "り": "리", "る": "루", "れ": "레", "ろ": "로",
    "わ": "와", "ゐ": "이", "ゑ": "에", "を": "오", "ゔ": "브",
    "ぁ": "아", "ぃ": "이", "ぅ": "우", "ぇ": "에", "ぉ": "오",
    "きゃ": "캬", "きゅ": "큐", "きょ": "쿄",
    "ぎゃ": "갸", "ぎゅ": "규", "ぎょ": "교",
    "しゃ": "샤", "しゅ": "슈", "しょ": "쇼",
    "じゃ": "자", "じゅ": "주", "じょ": "조",
    "ちゃ": "차", "ちゅ": "추", "ちょ": "초",
    "にゃ": "냐", "にゅ": "뉴", "にょ": "뇨",
    "ひゃ": "햐", "ひゅ": "휴", "ひょ": "효",
    "びゃ": "뱌", "びゅ": "뷰", "びょ": "뵤",
    "ぴゃ": "퍄", "ぴゅ": "퓨", "ぴょ": "표",
    "みゃ": "먀", "みゅ": "뮤", "みょ": "묘",
    "りゃ": "랴", "りゅ": "류", "りょ": "료",
    "ふぁ": "파", "ふぃ": "피", "ふぇ": "페", "ふぉ": "포",
    "てぃ": "티", "でぃ": "디", "とぅ": "투", "どぅ": "두",
    "ちぇ": "체", "しぇ": "셰", "じぇ": "제",
    "うぃ": "위", "うぇ": "웨", "うぉ": "워",
    "ゔぁ": "바", "ゔぃ": "비", "ゔぇ": "베", "ゔぉ": "보",
}
_NAME_KANA_KO = {
    "あんどれ": "안드레", "じゃくそん": "잭슨",
    "そたに": "소타니", "たつき": "타츠키", "ゆうたろう": "유타로",
}

NPB_NAME_RULE_VERSION = 2
def parse_npb_player_reading(html: str) -> str | None:
    """NPB 선수 프로필의 공식 가나 독음을 반환한다."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one("#pc_v_kana")
    reading = _clean(node.get_text(" ") if node else "")
    return reading or None

def _hiragana(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = re.sub(r"\([^)]*\)", "", value)
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in value)

def _append_final(parts: list[str], jong: int) -> None:
    for index in range(len(parts) - 1, -1, -1):
        if not parts[index] or parts[index].isspace():
            continue
        last = parts[index][-1]
        code = ord(last) - 0xAC00
        if 0 <= code < 11172 and code % 28 == 0:
            parts[index] = parts[index][:-1] + chr(ord(last) + jong)
        return

def _last_vowel(parts: list[str]) -> int | None:
    for part in reversed(parts):
        if not part or part.isspace():
            continue
        code = ord(part[-1]) - 0xAC00
        return (code // 28) % 21 if 0 <= code < 11172 else None
    return None

def kana_to_hangul(reading: str) -> str:
    """일본인명 독음과 외국인 선수의 가타카나 이름을 한글로 전사한다."""
    text = _hiragana(reading).replace("・", " ").replace("=", " ")
    for kana, korean in _NAME_KANA_KO.items():
        text = text.replace(kana, korean)
    parts: list[str] = []
    index = 0
    while index < len(text):
        ch = text[index]
        if ch in " .·/":
            if parts and not parts[-1].isspace():
                parts.append(" ")
            index += 1
            continue
        if ch == "ー":
            index += 1
            continue
        if ch == "ん":
            following = text[index + 1:index + 2]
            _append_final(parts, 16 if following in "ばびぶべぼぱぴぷぺぽまみむめも" else 4)
            index += 1
            continue
        if ch == "っ":
            following = text[index + 1:index + 2]
            if following in "かきくけこがぎぐげご":
                _append_final(parts, 1)
            elif following in "ぱぴぷぺぽばびぶべぼ":
                _append_final(parts, 17)
            else:
                _append_final(parts, 19)
            index += 1
            continue
        token = text[index:index + 2]
        if token in _KANA_KO:
            mapped, consumed = _KANA_KO[token], 2
        else:
            mapped, consumed = _KANA_KO.get(ch), 1
        # しょう·ゆう처럼 う가 앞 음절의 장음을 나타내면 한글에서 반복하지 않는다.
        if ch == "う" and _last_vowel(parts) in (8, 12, 17):
            index += 1
            continue
        if mapped:
            parts.append(mapped)
        elif not ("ぁ" <= ch <= "ゖ"):
            parts.append(ch)
        index += consumed
    return _clean("".join(parts))

def localize_npb_starters(games: list[dict], session: requests.Session,
                          name_cache: dict | None = None) -> list[dict]:
    """선수 ID별 공식 독음을 캐시하고 NPB 선발의 화면 이름을 한글로 바꾼다."""
    cache = name_cache if name_cache is not None else {}
    for game in games:
        for starter in (game.get("starters") or {}).values():
            player_id = str(starter.get("player_id") or "")
            native = _clean(starter.get("name"))
            if not player_id or not native:
                continue
            saved = cache.get(player_id) or {}
            if (saved.get("native_name") == native and saved.get("name_ko") and
                    saved.get("rule_version") == NPB_NAME_RULE_VERSION):
                korean = saved["name_ko"]
            else:
                try:
                    reading = parse_npb_player_reading(_html(
                        session, NPB_PLAYER_URL.format(player_id=player_id)))
                except requests.RequestException:
                    reading = None
                korean = kana_to_hangul(reading or "")
                if korean:
                    cache[player_id] = {
                        "native_name": native, "reading": reading, "name_ko": korean,
                        "rule_version": NPB_NAME_RULE_VERSION,
                        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
                    }
            if korean:
                starter["native_name"] = native
                starter["name"] = korean
    return games


def localize_npb_lineup_players(players: list[dict], session: requests.Session,
                                name_cache: dict | None = None) -> list[dict]:
    """박스스코어 타자도 선발투수와 같은 공식 독음 캐시로 한글화한다."""
    proxy = {str(index): player for index, player in enumerate(players)}
    localize_npb_starters([{"starters": proxy}], session, name_cache)
    return players


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
            player_id = player_match.group(1) if player_match else None
            sides[side] = {
                "name": _clean(name.get_text(" ")), "announced": True,
                "player_id": player_id,
                "profile_url": (NPB_PLAYER_URL.format(player_id=player_id)
                                if player_id else None),
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
    doc = load_artifact("picks_v2", picks_path) or {}
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


def _projected_lineup(entry: dict, starter: dict | None) -> list[dict]:
    """최근 타순을 복사하고 센트럴리그 투수 슬롯만 오늘 예고 선발로 바꾼다."""
    players = [{**player} for player in (entry.get("players") or [])]
    for index, player in enumerate(players):
        if player.get("position") != "투수":
            continue
        replacement = {
            "order": player.get("order"), "position": "투수",
            "name": "선발 투수 발표 대기", "player_id": None,
            "profile_url": None,
        }
        if starter and starter.get("name"):
            player_id = starter.get("player_id")
            replacement.update({
                "name": starter["name"], "native_name": starter.get("native_name"),
                "player_id": player_id,
                "profile_url": (f"https://npb.jp/bis/players/{player_id}.html"
                                if player_id else None),
            })
        players[index] = replacement
        break
    return players


def collect_npb_games(picks_path: Path, session: requests.Session,
                      now: datetime | None = None,
                      name_cache: dict | None = None) -> list[dict]:
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
    localize_npb_starters(announced, session, name_cache)

    starter_index = {}
    for game in announced:
        start = datetime.fromisoformat(game["game_datetime"])
        key = (start.date().isoformat(), game["home_team"], game["away_team"])
        starter_index[key] = game

    proto_games = _load_npb_proto_games(picks_path, now)
    target_teams = {
        _npb_canon(team) for proto in proto_games
        for team in (proto.get("home"), proto.get("away"))
    }
    target_teams.discard("")
    try:
        pitching_stats = collect_npb_pitcher_stats(
            session, now.year, target_teams, _html)
    except (requests.RequestException, ValueError):
        pitching_stats = {}
    for game in announced:
        starters = game.get("starters") or {}
        for side, team in (("home", game.get("home_team")), ("away", game.get("away_team"))):
            starter = starters.get(side) or {}
            if not starter.get("native_name"):
                continue
            stats = find_npb_player_stats(
                starter["native_name"], pitching_stats.get(team) or {})
            if stats:
                starter["stats"] = stats
                starter["stats_source"] = "NPB.jp 공식 시즌 투수 기록"

    try:
        official_lineups = collect_npb_official_lineups(
            session, now, target_teams, NPB_TEAM_KO, _html)
    except (requests.RequestException, ValueError):
        official_lineups = {}
    try:
        recent_lineups = collect_recent_npb_lineups(
            session, now, target_teams, NPB_TEAM_KO, _html)
    except (requests.RequestException, ValueError):
        recent_lineups = {}
    for entry in recent_lineups.values():
        localize_npb_lineup_players(entry.get("players") or [], session, name_cache)
    for entry in official_lineups.values():
        localize_npb_lineup_players(entry.get("players") or [], session, name_cache)

    out = []
    stamp = now.isoformat(timespec="seconds")
    for proto in proto_games:
        home, away = _npb_canon(proto.get("home")), _npb_canon(proto.get("away"))
        key = (proto["_start"].date().isoformat(), home, away)
        official = starter_index.get(key) or {}
        teams = {}
        if standings.get(home):
            teams["home"] = standings[home]
        if standings.get(away):
            teams["away"] = standings[away]
        lineups, reference_dates, source_urls, side_states = {}, {}, {}, {}
        official_starters = official.get("starters") or {}
        is_today = proto["_start"].date() == now.date()
        for side, team in (("home", home), ("away", away)):
            official_entry = (official_lineups.get(team) or {}) if is_today else {}
            entry = official_entry or recent_lineups.get(team) or {}
            if not entry.get("players"):
                continue
            if official_entry:
                lineups[side] = [{**player} for player in entry["players"]]
                side_states[side] = "official_today"
            else:
                lineups[side] = _projected_lineup(entry, official_starters.get(side))
                side_states[side] = "projected_recent"
            reference_dates[side] = entry.get("reference_date")
            source_urls[side] = entry.get("source_url")
        official_today = (len(side_states) == 2 and
                          all(state == "official_today" for state in side_states.values()))
        has_official = any(state == "official_today" for state in side_states.values())
        if official_today:
            state = "official_today"
            label = "NPB 오늘 공식 선발 타순"
            caveat = "NPB 공식 박스스코어에 공개된 오늘 1~9번 선발 타순"
        elif has_official:
            state = "mixed_official_projected"
            label = "NPB 공식·최근 경기 혼합 타순"
            caveat = "공개된 팀은 오늘 공식 타순, 미공개 팀은 최근 완료 경기 기준"
        else:
            state = "projected_from_recent_official"
            label = "NPB 최근 공식 선발 타순 기반 예상 라인업"
            caveat = "오늘 실제 선발 타순이 아니라 최근 완료 경기 기준"
        lineup_status = ({
            "state": state, "label": label, "side_states": side_states,
            "reference_dates": reference_dates, "source_urls": source_urls,
            "official_today": official_today, "caveat": caveat,
        } if lineups else {})
        # 일본 공식 자료를 하나도 못 받았으면 네이버/직전 캐시가 이기게 둔다.
        if not official and not teams and not lineups:
            continue
        out.append({
            "league": "NPB", "game_id": None,
            "game_datetime": proto["_start"].isoformat(),
            "home_team": proto.get("home"), "away_team": proto.get("away"),
            "starters": official.get("starters") or {}, "teams": teams,
            "unavailable": {}, "lineups": lineups, "lineup_status": lineup_status,
            "source": "NPB.jp 일본야구기구 공식 선발·순위·당일/최근 타순·투타 기록",
            "source_url": NPB_STARTERS_URL, "updated_at": stamp,
        })
    return out
