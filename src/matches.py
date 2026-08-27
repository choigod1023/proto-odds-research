"""프로토 게임행 → **실제 경기** 단위 테이블.

⚠️ 여기가 이 프로젝트에서 가장 틀리기 쉬운 지점이다.

프로토는 **같은 경기를 여러 회차·여러 상품으로 중복 발매**한다.
그래서 게임행을 그대로 세면 한 팀의 시즌 성적이 실제의 1.4배로 부풀고,
회차 순서로 정렬하면 시간 순서가 뒤섞여 '최근 10경기'가 무의미해진다.

    (실제로 2026 LG가 110승 86패 = 196경기로 집계됐다. KBO는 144경기다.)

해결
----
· `date_text`('07.26(일) 16:30')와 연도를 합쳐 **실제 날짜**를 만든다
· (리그, 홈팀, 원정팀, 경기시각)으로 재발매 중복만 제거한다
· **날짜순으로 정렬**한다 — 회차 번호는 시간 순서가 아니다
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

GAMES = Path(__file__).resolve().parent.parent / "data" / "processed" / "games.csv"

_HOME_RE = re.compile(r"^(.+?)\s+(-?\d+)\s*$")
_AWAY_RE = re.compile(r"^(-?\d+)\s+(.+?)\s*$")
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})")
_DATETIME_RE = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")

# ---------------------------------------------------------------- 팀명 정규화
# 🔴 **팀명을 벗기는 정규식은 여기 하나만 둔다.** 사본을 만들면 반드시 어긋난다.
#
# `home`/`away` 에는 숫자가 붙어 온다.
#     승패     "야쿠르트 5"  / "3 히로카프"     ← 정수 점수
#     핸디캡   "맨체스C -1.5" / "3 히로카프"    ← **소수** 핸디 보정 점수
#     언더오버 "야쿠르트"     / "히로카프"       ← 숫자 없음
# 원정은 **항상 접두**, 홈은 **항상 접미**다. 그래서 한 방향만 벗기면 반대쪽이 남는다.
#
# 실제로 6개 모듈이 각자 `-?\d+` 사본을 갖고 있었고 **소수를 못 벗겼다.** 실측 피해:
#     loss_filter   경기 키 44,915 → 71,282 (1.587배 부풀림, 유령 26,367경기)
#     market_scan   핸디캡 행 58% 조용히 소실 (38,831 → 16,372)
#     stack_filter  prior_meets 25,523행 NaN — 게다가 NaN 을 '통과'로 처리
#     q5_flb        접미만 벗겨 KBO 원정이 전멸 (2,688 → 30행, 1.1%)
# 이건 이 프로젝트가 반복해서 당한 '결과와 상관있는 조용한 행 누락'이다.
#
# ⚠️ 샬케04·마인츠05 처럼 **팀명 자체에 붙은 숫자는 남겨야 한다.**
#    그래서 '띄어쓰기로 분리된 숫자 토큰' 만 벗긴다.
_NUM_PRE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s+")
_NUM_SUF = re.compile(r"\s+-?\d+(?:\.\d+)?\s*$")


def clean_team(x) -> str:
    """`home`/`away` 문자열에서 팀명만 남긴다. 접두·접미 숫자 둘 다 벗긴다."""
    return _NUM_SUF.sub("", _NUM_PRE.sub("", str(x).strip())).strip()


def _home(x):
    m = _HOME_RE.match(str(x).strip())
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _away(x):
    m = _AWAY_RE.match(str(x).strip())
    return (int(m.group(1)), m.group(2)) if m else (None, None)


def actual_game_year(year, round_no, month):
    """발매 연도 1회차에 섞인 전년도 12월 31일의 실제 경기 연도를 복원한다."""
    y = pd.to_numeric(year, errors="coerce")
    rnd = pd.to_numeric(round_no, errors="coerce")
    mm = pd.to_numeric(month, errors="coerce")
    return y - ((rnd == 1) & (mm == 12)).astype(int)


def load_matches(sports: tuple[str, ...] | None = None,
                 path: Path = GAMES) -> pd.DataFrame:
    """정산 완료 경기. 시각으로 더블헤더를 보존하고 재발매만 합친다."""
    g = pd.read_csv(path)
    g = g[(~g["is_void"].astype(bool))
          & (g["market_family"].isin(["승패", "승무패"]))
          & (g["result"].isin(["홈승", "홈패", "무승부"]))]
    if sports:
        g = g[g["sport"].isin(sports)]

    hs = g["home"].map(_home)
    aw = g["away"].map(_away)
    g = g.assign(home_team=[t for t, _ in hs], home_score=[s for _, s in hs],
                 away_score=[s for s, _ in aw], away_team=[t for _, t in aw])
    g = g.dropna(subset=["home_team", "away_team", "home_score", "away_score"])

    # 경기시각 복원: 날짜만 쓰면 같은 날 더블헤더와 재발매를 구별할 수 없다.
    dt = g["date_text"].astype(str).str.extract(_DATETIME_RE)
    g = g.assign(_mm=pd.to_numeric(dt[0], errors="coerce"),
                 _dd=pd.to_numeric(dt[1], errors="coerce"),
                 _hh=pd.to_numeric(dt[2], errors="coerce"),
                 _minute=pd.to_numeric(dt[3], errors="coerce"))
    g = g.dropna(subset=["_mm", "_dd", "_hh", "_minute"])
    game_year = actual_game_year(g["year"], g["round"], g["_mm"])
    g["date"] = pd.to_datetime(
        dict(year=game_year, month=g["_mm"].astype(int), day=g["_dd"].astype(int),
             hour=g["_hh"].astype(int), minute=g["_minute"].astype(int)),
        errors="coerce")
    g = g.dropna(subset=["date"])
    # 이후 학습/검증 분할의 year도 발매 연도가 아니라 실제 경기 연도여야 한다.
    g["year"] = g["date"].dt.year.astype(int)

    key = ["league", "home_team", "away_team", "date"]
    # 동일 시각·팀인데 최종 스코어가 다르면 어느 행도 실제 경기로 확정하지 않는다.
    variants = g.groupby(key)[["home_score", "away_score"]].nunique()
    bad = variants.index[variants.max(axis=1) > 1]
    if len(bad):
        keys = pd.MultiIndex.from_frame(g[key])
        g = g.loc[~keys.isin(bad)]
    # 같은 실제 경기가 여러 회차에 판매된 행은 결과가 같을 때만 1건으로 축약한다.
    g = g.drop_duplicates(subset=key)

    g["outcome"] = np.where(g["home_score"] > g["away_score"], 1.0,
                            np.where(g["home_score"] < g["away_score"], 0.0, 0.5))
    g = g.sort_values(["date", "league", "home_team"]).reset_index(drop=True)
    return g[["date", "year", "league", "sport", "home_team", "away_team",
              "home_score", "away_score", "outcome"]]


def sanity_report(m: pd.DataFrame, league: str = "KBO") -> None:
    """팀별 경기 수가 리그 일정과 맞는지 눈으로 확인한다."""
    sub = m[m["league"] == league]
    for yr, s in sub.groupby("year"):
        cnt = pd.concat([s["home_team"], s["away_team"]]).value_counts()
        print(f"  {league} {yr}: 경기 {len(s):,} · 팀 {len(cnt)}개 · "
              f"팀당 {cnt.min()}~{cnt.max()}경기")


if __name__ == "__main__":
    m = load_matches()
    print(f"실제 경기 {len(m):,}건 · 리그 {m['league'].nunique()}개 "
          f"· {m['date'].min().date()} ~ {m['date'].max().date()}")
    for lg in ("KBO", "MLB", "NBA", "K리그1"):
        sanity_report(m, lg)
