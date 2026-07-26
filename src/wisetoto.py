"""와이즈토토 프로토 회차 아카이브 수집·파싱.

배당과 경기 결과가 같은 응답에 들어 있어, 한 번 수집하면 Q0·Q1·Q4·Q5를 전부 커버한다.
원본 응답은 gzip 캐시로 보관하고, 파서를 고쳐도 재수집하지 않는다(재현성 + 서버 부담 최소화).

DOM 구조 (2026-07-26 확인):
    div.gameinfo ul                 = 게임행 1개
      li.a1                         = 경기번호
      li.a2                         = '08.22(금) 18:00'
      li.a3.{sc|bs|bk|vl}           = 종목 (두 번째 class가 종목코드)
      li.a4                         = 리그명
      li.{hm|un|d1|hp}              = 마켓 유형 표시
      li.a6 / li.a7 / li.a8         = 홈팀+스코어 / ':' / 스코어+원정팀
      li.a9 × 3                     = 배당 (2-way면 가운데가 '-')
      li (class 없음, 마지막)        = 결과 라벨
"""
from __future__ import annotations

import gzip
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.wisetoto.com"
CACHE = Path(__file__).resolve().parent.parent / "data" / "raw" / "wisetoto"

REQUEST_INTERVAL = 2.5  # 초. 비상업 연구 목적, 서버 부담 최소화

SPORT_NAMES = {"sc": "축구", "bs": "야구", "bk": "농구", "vl": "배구"}

# 마켓 유형 표시 class → 이름
MARKET_TAGS = {
    "hm": "일반",      # 승패(2-way) 또는 승무패(3-way)
    "un": "언더오버",
    "d1": "승①패",     # 3-way 핸디캡
    "hp": "핸디캡",
}

# 무효/취소를 뜻하는 결과 라벨 — 오버라운드 계산에서 반드시 제외 (설계서 P2)
VOID_RESULTS = {"취소", "연기", "중단", "무효"}

_ODDS_RE = re.compile(r"\d{1,3}\.\d{2}")
# 'H -1.0', 'h H +1.5' 등 핸디캡 라벨
_HANDICAP_RE = re.compile(r"^(h\s+)?H\s*[-+]?\d")


@dataclass
class GameRow:
    year: int
    round: int
    game_no: str
    date_text: str
    sport: str          # sc/bs/bk/vl
    league: str
    market_tag: str     # hm/un/d1/hp
    market_label: str   # 'U 5.5', '승①패' 등 표시 텍스트
    home: str
    away: str
    score_text: str
    odds: list[float]   # 유효 배당만 (2개 또는 3개)
    result: str
    is_void: bool

    @property
    def n_way(self) -> int:
        return len(self.odds)

    @property
    def overround(self) -> float | None:
        """확률 합. 1.1362 = 마진 13.62% = 환급률 88.01%"""
        if not self.odds or any(o <= 1.0 for o in self.odds):
            return None
        return sum(1.0 / o for o in self.odds)

    @property
    def is_handicap(self) -> bool:
        return bool(_HANDICAP_RE.match(self.market_label.strip()))

    @property
    def is_first_half(self) -> bool:
        """'h(전반)', 'h U 4.5' 등 전반전 한정 마켓."""
        lbl = self.market_label.strip()
        return "전반" in lbl or bool(re.match(r"^h\s", lbl))

    @property
    def booking_class(self) -> str:
        """⭐ 오버라운드를 결정하는 유일한 축 (2026-07-26 실측).

        마켓 '상품'이 아니라 **선택지 수 + 핸디캡 여부**만으로 booking이 정해진다.
            2-way          → 1.1364 (환급 88.00%)
            3-way          → 1.1494 (환급 87.00%)
            3-way 핸디캡    → 1.1628 (환급 86.00%)
        """
        if self.n_way == 2:
            return "2-way"
        return "3-way-핸디캡" if self.is_handicap else "3-way"

    @property
    def market_family(self) -> str:
        """상품 구분 (booking과는 별개 축)."""
        if self.market_tag == "un":
            return "언더오버"
        if self.market_tag == "d1":
            return "승①패"
        if self.is_handicap:
            return "핸디캡"
        return "승무패" if self.n_way == 3 else "승패"

    @property
    def market_type(self) -> str:
        """사람이 읽는 조합 라벨."""
        fam = self.market_family
        half = "전반 " if self.is_first_half else ""
        return f"{half}{fam}({self.n_way}-way)"


# ---------------------------------------------------------------- 수집

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": BASE + "/index.htm",
        "X-Requested-With": "XMLHttpRequest",
    })
    return s


# 회차 페이지에서 master_seq 추출.
# 주의: 'game_info_master_seq' 문자열은 JS 함수 *정의부*에만 있고 값이 없다.
# 실제 값은 get_gameinfo_body(...) 호출부의 7번째 인자다. (README §14-1)
_SEQ_RE = re.compile(
    r"get_gameinfo_body\(\s*'proto'\s*,\s*'pt1'\s*,\s*'?(\d{4})'?\s*,"
    r"\s*'(\d+)'\s*,\s*''\s*,\s*''\s*,\s*'(\d+)'"
)


def get_master_seq(year: int, rnd: int, sess: requests.Session | None = None) -> str | None:
    s = sess or _session()
    url = (f"{BASE}/index.htm?tab_type=proto&game_type=pt&game_category=pt1"
           f"&game_year={year}&game_round={rnd}")
    html = s.get(url, timeout=25).text
    m = _SEQ_RE.search(html)
    return m.group(3) if m else None


def _cache_path(year: int, rnd: int) -> Path:
    return CACHE / str(year) / f"{rnd:04d}.html.gz"


def fetch_round(year: int, rnd: int, sess: requests.Session | None = None,
                use_cache: bool = True) -> str | None:
    """한 회차의 전 종목 게임 목록 HTML을 반환. 캐시가 있으면 요청하지 않는다(멱등)."""
    path = _cache_path(year, rnd)
    if use_cache and path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read()

    s = sess or _session()
    seq = get_master_seq(year, rnd, s)
    if not seq:
        return None
    time.sleep(REQUEST_INTERVAL)

    r = s.get(f"{BASE}/util/gameinfo/get_proto_list.htm", params={
        "game_category": "pt1", "game_year": year, "game_round": rnd,
        "game_month": "", "game_day": "", "game_info_master_seq": seq,
        "sports": "", "sort": "", "tab_type": "proto",
    }, timeout=40)
    r.raise_for_status()
    html = r.text

    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(html)
    return html


# ---------------------------------------------------------------- 파싱

def _clean_odds(text: str) -> float | None:
    """'1.73 ↑' → 1.73 / '-' → None"""
    m = _ODDS_RE.search(text.replace(" ", ""))
    return float(m.group(0)) if m else None


def parse_rows(html: str, year: int, rnd: int) -> list[GameRow]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[GameRow] = []

    for ul in soup.select("div.gameinfo ul"):
        lis = ul.find_all("li", recursive=False)
        if not lis:
            continue
        a1 = ul.select_one("li.a1")
        if not a1 or not a1.get_text(strip=True).isdigit():
            continue  # 헤더 등 비-게임행

        a3 = ul.select_one("li.a3")
        cls3 = a3.get("class") or [] if a3 else []
        sport = next((c for c in cls3 if c in SPORT_NAMES), "")

        market_tag, market_label = "", ""
        for li in lis:
            for c in (li.get("class") or []):
                if c in MARKET_TAGS:
                    market_tag = c
                    market_label = " ".join(li.get_text(" ", strip=True).split())
                    break
            if market_tag:
                break

        odds = [o for o in (_clean_odds(x.get_text(" ", strip=True))
                            for x in ul.select("li.a9")) if o is not None]

        tail = [li for li in lis if not li.get("class")]
        result = tail[-1].get_text(strip=True) if tail else ""

        def _txt(sel: str) -> str:
            el = ul.select_one(sel)
            return " ".join(el.get_text(" ", strip=True).split()) if el else ""

        home = _txt("li.a6") or _txt("li.a6_un")
        away = _txt("li.a8") or _txt("li.a8_un")
        mid = _txt("li.a7") or _txt("li.a7_un")

        rows.append(GameRow(
            year=year, round=rnd,
            game_no=a1.get_text(strip=True),
            date_text=_txt("li.a2"),
            sport=sport,
            league=_txt("li.a4"),
            market_tag=market_tag,
            market_label=market_label,
            home=home, away=away,
            score_text=f"{home} {mid} {away}",
            odds=odds,
            result=result,
            is_void=(result in VOID_RESULTS) or any(o <= 1.0 for o in odds),
        ))
    return rows


def rows_to_records(rows: list[GameRow]) -> list[dict]:
    out = []
    for r in rows:
        d = asdict(r)
        d["n_way"] = r.n_way
        d["overround"] = r.overround
        d["market_type"] = r.market_type
        d["market_family"] = r.market_family
        d["booking_class"] = r.booking_class
        out.append(d)
    return out
