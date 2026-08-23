"""NPB 공식 박스스코어에서 최근 선발 타순과 시즌 타격 기록을 만든다."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

NPB_ORIGIN = "https://npb.jp"
NPB_DAILY_URL = "https://npb.jp/bis/{season}/games/gm{date}.html"
NPB_TEAM_BATTING_URL = "https://npb.jp/bis/{season}/stats/idb1_{code}.html"

TEAM_STATS_CODE = {
    "한신": "t", "요코하마": "db", "요미우리": "g", "주니치": "d",
    "히로시마": "c", "야쿠르트": "s", "소프트뱅크": "h", "닛폰햄": "f",
    "오릭스": "b", "라쿠텐": "e", "세이부": "l", "지바롯데": "m",
}
POSITION_KO = {
    "投": "투수", "捕": "포수", "一": "1루수", "二": "2루수",
    "三": "3루수", "遊": "유격수", "左": "좌익수", "中": "중견수",
    "右": "우익수", "指": "지명타자", "DH": "지명타자",
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


def parse_npb_daily_box_links(html: str, day) -> list[str]:
    """하루 경기결과 페이지에서 해당 날짜 1군 경기 box.html 주소만 뽑는다."""
    soup = BeautifulSoup(html, "lxml")
    prefix = f"/scores/{day.year}/{day:%m%d}/"
    out = []
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "")
        absolute = urljoin(NPB_ORIGIN, href)
        match = re.search(rf"{re.escape(prefix)}[^/?#]+/", absolute)
        if not match:
            continue
        box_url = absolute[:match.end()].rstrip("/") + "/box.html"
        if box_url not in out:
            out.append(box_url)
    return out


def _position(value: str) -> str:
    raw = _clean(value).strip("()（）")
    if raw in POSITION_KO:
        return POSITION_KO[raw]
    # 선발행은 보통 한 자리지만 겸임 표기가 오면 마지막 수비 위치를 사용한다.
    for char in reversed(raw):
        if char in POSITION_KO:
            return POSITION_KO[char]
    return raw


def parse_npb_box_lineups(html: str, team_name_ko: dict[str, str]) -> dict[str, list[dict]]:
    """투타성적 표에서 타순 번호가 있는 최초 출전 선수 1~9번만 반환한다."""
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for table in soup.find_all("table"):
        first = table.find("tr")
        header = _clean(first.get_text(" ") if first else "")
        if "守備" not in header or "選手" not in header or "打数" not in header:
            continue
        heading = table.find_previous(["h3", "h4", "h5"])
        raw_team = _clean(heading.get_text(" ") if heading else "")
        team = team_name_ko.get(raw_team)
        if not team:
            continue
        players = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 8:
                continue
            order_text = _clean(cells[0].get_text(" "))
            if not re.fullmatch(r"[1-9]", order_text):
                continue
            link = cells[2].find("a", href=True)
            name = _clean(link.get_text(" ") if link else cells[2].get_text(" "))
            player_match = re.search(r"/players/(\d+)\.html", link.get("href", "") if link else "")
            player_id = player_match.group(1) if player_match else None
            players.append({
                "order": int(order_text), "position": _position(cells[1].get_text(" ")),
                "name": name, "native_name": name, "player_id": player_id,
                "profile_url": (f"https://npb.jp/bis/players/{player_id}.html"
                                if player_id else None),
                "last_game": {
                    "at_bats": _number(cells[3].get_text(" "), True),
                    "runs": _number(cells[4].get_text(" "), True),
                    "hits": _number(cells[5].get_text(" "), True),
                    "rbi": _number(cells[6].get_text(" "), True),
                    "stolen_bases": _number(cells[7].get_text(" "), True),
                },
            })
        if len(players) >= 9:
            out[team] = sorted(players, key=lambda player: player["order"])[:9]
    return out


def _name_key(value: str) -> str:
    return re.sub(r"[\s・･.*＊+＋]", "", _clean(value)).casefold()


def parse_npb_batting_stats(html: str, season: int) -> dict[str, dict]:
    """구단별 개인 타격표를 등록명 → 주요 시즌 지표로 변환한다."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.tablefix2") or soup.find("table")
    if not table:
        return {}
    out = {}
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 23:
            continue
        native_name = _clean(cells[0].get_text(" "))
        name_key = _name_key(native_name)
        if not name_key:
            continue
        obp, slg = _number(cells[22].get_text(" ")), _number(cells[21].get_text(" "))
        out[name_key] = {
            "native_name": native_name,
            "season": season, "games": _number(cells[1].get_text(" "), True),
            "plate_appearances": _number(cells[2].get_text(" "), True),
            "at_bats": _number(cells[3].get_text(" "), True),
            "hits": _number(cells[5].get_text(" "), True),
            "home_runs": _number(cells[8].get_text(" "), True),
            "rbi": _number(cells[10].get_text(" "), True),
            "stolen_bases": _number(cells[11].get_text(" "), True),
            "avg": _number(cells[20].get_text(" ")), "slg": slg, "obp": obp,
            "ops": round(obp + slg, 3) if obp is not None and slg is not None else None,
        }
    return out


def _match_batting_stats(native_name: str, stats: dict[str, dict]) -> dict | None:
    """박스스코어의 성/등록명을 구단 타격표의 전체 이름과 유일하게 연결한다."""
    key = _name_key(native_name)
    if key in stats:
        return stats[key]
    matches = [value for stat_name, value in stats.items()
               if stat_name.startswith(key) or key.startswith(stat_name)]
    return matches[0] if len(matches) == 1 else None


def collect_recent_npb_lineups(session: requests.Session, now: datetime,
                               target_teams: set[str], team_name_ko: dict[str, str],
                               fetch_html, lookback_days: int = 7) -> dict[str, dict]:
    """각 팀의 전날 또는 가장 최근 완료 경기 선발 타순과 시즌 기록을 수집한다."""
    latest = {}
    for offset in range(1, lookback_days + 1):
        day = now.date() - timedelta(days=offset)
        daily_url = NPB_DAILY_URL.format(season=day.year, date=day.strftime("%Y%m%d"))
        try:
            links = parse_npb_daily_box_links(fetch_html(session, daily_url), day)
        except (requests.RequestException, ValueError):
            continue
        for box_url in links:
            try:
                parsed = parse_npb_box_lineups(fetch_html(session, box_url), team_name_ko)
            except (requests.RequestException, ValueError):
                continue
            for team, players in parsed.items():
                if team not in target_teams:
                    continue
                # 같은 날 더블헤더는 일일 페이지의 뒤쪽(제2경기) 기록을 남긴다.
                if team not in latest or latest[team]["reference_date"] == day.isoformat():
                    latest[team] = {
                        "players": players, "reference_date": day.isoformat(),
                        "source_url": box_url,
                    }
        if target_teams.issubset(latest):
            break

    for team, entry in latest.items():
        code = TEAM_STATS_CODE.get(team)
        if not code:
            continue
        stats_url = NPB_TEAM_BATTING_URL.format(season=now.year, code=code)
        try:
            stats = parse_npb_batting_stats(fetch_html(session, stats_url), now.year)
        except (requests.RequestException, ValueError):
            stats = {}
        for player in entry["players"]:
            player_stats = _match_batting_stats(player.get("native_name") or "", stats)
            if player_stats:
                player["stats"] = player_stats
                player["stats_source"] = "NPB.jp 공식 시즌 타격 기록"
    return latest
