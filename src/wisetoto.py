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
import sys
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
        """마켓 종류. 전반전 한정 마켓은 '전반' 접두사가 붙는다.

        ⚠️ 전반 마켓(3,844건)을 왜 분리하나: 라벨이 'h(전반)'·'h U 4.5' 처럼
           풀게임과 구분이 안 돼서 그동안 승무패·언더오버·핸디캡으로 섞여 있었다.
           그러면 **풀게임 스코어 분포로 전반전 가격을 매기게 된다.**
           실측: 전반 무승부 18.4% vs 풀게임 25.0%. 모델은 25% 를 씌우고
           시장은 15.3% 를 매기니 존재하지도 않는 +10%p 우위가 생기고,
           실제로 '무 @6.5' 가 사이트 추천에 올라갔다.
           전반전 λ 를 따로 추정하기 전까지 **모델은 이 마켓에 값을 매기지 않는다.**
        """
        fam = self._base_family
        return f"전반{fam}" if self.is_first_half and fam != "미분류" else fam

    @property
    def _base_family(self) -> str:
        """상품 구분 (booking과는 별개 축).

        ⚠️ 사이트의 `hm`(일반) 태그 하나에 **승무패·승⑤패·홀짝이 섞여 온다.**
           태그만 보고 n_way 로 가르면 셋이 뭉개진다. 실제로 그랬다:

           · 농구/배구 3-way 를 '승무패'로 라벨 → 결과값 '⑤'(5점차 이내)가
             WIN_IDX 에 없어 **조용히 버려졌다.** KBL 32.2% · WKBL 34.3% 가
             사라졌고, 6점차 이상으로 갈린 경기만 남아 모델이 이기는 것처럼
             보였다(가짜 ROI +30%). 2026-07-28 발견.
           · 홀짝(SUM)을 '승패 2-way'로 라벨 → 18,781건(44.2%)이 통째로 버려졌다.
             이쪽은 결과와 무관한 전량 누락이라 승패 분석 자체는 오염되지 않았지만,
             **홀짝 마켓은 한 번도 분석된 적이 없다.**

        농구·배구는 무승부가 없다(연장으로 반드시 승부가 난다). 따라서 이 두 종목의
        3-way 일반 마켓은 **구조적으로 승⑤패**다. 이 사실로 가른다.
        """
        # ⚠️ 태그가 비어 있는 행이 **한 종류가 아니다.**
        #    · n_way=2 → 홀짝(SUM). 사이트가 홀짝에만 class 를 안 붙인다.
        #      검증: 태그 없는 2-way 정산분 18,537건이 전부 홀/짝(순도 98.6%).
        #    · n_way=3 → 농구·배구면 승⑤패
        #    · n_way=0 → 배당 자체가 없는 행
        # ⚠️ 태그 없는 행이 한 종류가 아니다. 여기서 n_way 로 다시 갈라야 한다.
        #    2026-07-28 실수: 아래를 `홀짝 if n_way==2 else 미분류` 로만 짰더니
        #    **태그 없는 3-way(=KBL 승⑤패 1,206건)를 통째로 '미분류'로 삼켰다.**
        #    같은 날 아침에 고친 승⑤패 분류를 저녁에 다시 깨뜨린 셈이다.
        if not self.market_tag:
            if self.n_way == 2:
                return "홀짝"
            if self.n_way == 3:
                # 농구·배구는 무승부가 없다 → 3-way 는 구조적으로 승⑤패
                return "승⑤패" if self.sport in ("bk", "vl") else "승무패"
            # n_way=0 : 배당이 아예 없는 행(미공개). 476건, 결과는 대개 홀/짝이지만
            # 배당이 없어 마켓을 확정할 수 없고 어차피 분석에 못 쓴다.
            return "미분류"
        if self.market_tag == "un":
            return "언더오버"
        if self.market_tag == "d1":
            return "승①패"
        if self.is_handicap:
            return "핸디캡"
        if self.n_way == 3:
            return "승⑤패" if self.sport in ("bk", "vl") else "승무패"
        return "승패"

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
    if not m:
        return None
    seq = m.group(3)
    # ⚠️ 아직 발매되지 않은 회차를 요청하면 wisetoto 는 요청한 회차 번호를 그대로
    #    되돌리되 seq 자리에 '0' 을 넣은 껍데기 페이지를 준다(게임행 0건).
    #    이걸 유효한 seq 로 받으면 probe_latest_round 의 이분 탐색이 "이 회차도
    #    존재한다"고 오판해 실제 최신 회차(예: 105)를 한참 지나친 번호로 올라가고,
    #    find_live_rounds 가 빈 회차만 훑어 "발매 중인 회차를 찾지 못했습니다"로 끝난다.
    #    (2026-09-04 실측: 105회차가 발매 중인데 탐지가 511회차까지 올라갔다.)
    return seq if seq and seq != "0" else None


def _cache_path(year: int, rnd: int) -> Path:
    return CACHE / str(year) / f"{rnd:04d}.html.gz"


# 한글이 한 글자도 없으면 디코딩이 틀린 것이다.
_HANGUL = re.compile(r"[가-힣]")
# 잘못 디코딩된 흔적 — 키릴/라틴확장이 본문에 섞인다.
_MOJI = re.compile(r"[Ѐ-ӿĀ-ſ]")


def _decode(r: requests.Response) -> str:
    """응답을 **UTF-8 로 고정** 디코딩한다.

    🔴 원래 `r.text` 를 그냥 썼다. 이 API 는 charset 헤더를 안 줄 때가 있고,
       그러면 requests 가 chardet 로 **추측**한다. 추측이 빗나가서
       아카이브 10개 회차(게임행 3,429건 · 1.9%)가 통째로 모지바케로 저장됐다 —
       `ptcp154`(카자흐 키릴) 2,497건 · `mac_latin2` 1,157건.
       `result` 컬럼까지 깨져서('нҷҲмҠ№'=홈승) 그 행들은 모든 분석에서
       조용히 빠졌다. 이 프로젝트가 반복해서 당한 '결과와 상관있는 행 누락'이다.

    저장 전에 한글이 있는지 확인하고, 없으면 알려진 오디코딩을 되돌려 본다.
    """
    r.encoding = "utf-8"
    html = r.text
    if _HANGUL.search(html):
        return html
    # UTF-8 이 아니었을 수도 있다 — 한국 사이트의 나머지 후보를 시도한다.
    for enc in ("euc-kr", "cp949"):
        try:
            t = r.content.decode(enc)
        except UnicodeDecodeError:
            continue
        if _HANGUL.search(t):
            return t
    return html


def repair_mojibake(s: str) -> str:
    """이미 깨진 채로 저장된 문자열을 되돌린다 (아카이브 소급 복구용).

    세 계열 다 왕복 검증했다 (chardet 이 회차마다 다르게 빗나갔다) —
        'нҷҲмҠ№'.encode('ptcp154').decode('utf-8')     == '홈승'
        'ŪôąŪĆ®'.encode('mac_latin2').decode('utf-8')   == '홈패'
        'л≤ИнШЄ'.encode('mac_cyrillic').decode('utf-8') == '번호'
    되돌린 결과에 한글이 없으면 원본을 그대로 돌려준다(추측하지 않는다).
    """
    if not s or not _MOJI.search(s):
        return s
    for enc in ("ptcp154", "mac_latin2", "mac_cyrillic"):
        try:
            t = s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if _HANGUL.search(t) and not _MOJI.search(t):
            # 원본의 \xa0(줄바꿈없는공백)이 팀명을 갈랐다 — 보통 공백으로 되돌린다
            return t.replace("\xa0", " ")
    return s


def fetch_round(year: int, rnd: int, sess: requests.Session | None = None,
                use_cache: bool = True) -> str | None:
    """한 회차의 전 종목 게임 목록 HTML을 반환. 캐시가 있으면 요청하지 않는다(멱등)."""
    path = _cache_path(year, rnd)
    if use_cache and path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as f:
            # 이미 깨진 채로 저장된 회차가 10개 있다 — 읽을 때 되돌린다.
            # (다시 긁으면 되지만 아카이브는 멱등이 원칙이라 캐시를 건드리지 않는다)
            return repair_mojibake(f.read())

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
    html = _decode(r)

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


def _self_test() -> None:
    """market_family 분류 단위검사.

    ⚠️ 2026-07-28 에 여기서 두 번 사고가 났다.
      · 아침: 태그 'hm' 하나에 승무패·승⑤패·홀짝이 섞여 오는 걸 놓쳐
              KBL 승⑤패가 '승무패' 로 분류 → 결과 `⑤` 32% 가 조용히 버려져
              가짜 ROI +30% 가 나왔다.
      · 저녁: 홀짝을 고치면서 `태그없음 → 홀짝 or 미분류` 로만 갈라
              **태그 없는 3-way(KBL 승⑤패 1,206건)를 미분류로 삼켰다.**
    분류는 (태그 × 선택지수 × 종목) 세 축이 얽혀 있어 눈으로 못 지킨다.
    바꿀 때마다 `python3 src/wisetoto.py --selftest` 를 돌릴 것.
    """
    def mk(tag, nw, sp, lab=""):
        return GameRow(year=2026, round=1, game_no="1", date_text="", sport=sp,
                       league="", market_tag=tag, market_label=lab, home="", away="",
                       score_text="", odds=[2.0] * nw, result="", is_void=False)

    # (태그, 선택지수, 종목, 기대분류[, 라벨])
    cases = [
        ("", 2, "bs", "홀짝"),        # 사이트가 홀짝에만 class 를 안 붙인다
        ("", 3, "bk", "승⑤패"),       # 태그 없는 3-way 농구 = 승⑤패 (저녁 사고)
        ("", 3, "vl", "승⑤패"),
        ("", 3, "sc", "승무패"),
        ("", 0, "bs", "미분류"),       # 배당 자체가 없는 행
        # 전반전 한정 마켓 — 풀게임과 섞이면 모델이 가짜 우위를 만든다 (3,844건)
        ("hm", 3, "sc", "전반승무패", "h(전반)"),
        ("hm", 2, "sc", "전반승패", "h(전반)"),
        ("hm", 3, "sc", "전반핸디캡", "h H -1.5"),
        ("un", 2, "bs", "전반언더오버", "h U 4.5"),
        ("hm", 3, "sc", "핸디캡", "H -1.5"),   # 대문자 H = 풀게임. 전반 아님
        ("hm", 3, "bk", "승⑤패"),     # 농구·배구는 무승부가 없다 (아침 사고)
        ("hm", 3, "sc", "승무패"),
        ("hm", 2, "bs", "승패"),
        ("un", 2, "bs", "언더오버"),
        # ⚠️ hp 는 **라벨이 있어야** 핸디캡으로 잡힌다(is_handicap 이 라벨을 본다).
        #    라벨 없이 넣으면 승무패로 떨어진다 — 자기검사가 이걸 잡아냈다.
        ("hp", 3, "sc", "핸디캡", "H -1.0"),
        ("d1", 3, "bs", "승①패"),
    ]
    bad = []
    for c in cases:
        t, n, sp, want = c[:4]
        lab = c[4] if len(c) > 4 else ""
        got = mk(t, n, sp, lab).market_family
        if got != want:
            bad.append((t, n, sp, want, got))
    for t, n, sp, want, got in bad:
        print(f"  FAIL tag={t or '(없음)'} n_way={n} {sp} → {got} (기대 {want})")
    print(f"market_family 단위검사: {len(cases) - len(bad)}/{len(cases)} 통과")
    if bad:
        raise SystemExit(1)

if __name__ == "__main__":
    # ⚠️ --selftest 는 main() 보다 **먼저** 검사해야 한다.
    #    아래 순서가 뒤바뀌면 자기검사가 영영 안 돌아간다(실제로 그랬다).
    if "--selftest" in sys.argv:
        _self_test()
