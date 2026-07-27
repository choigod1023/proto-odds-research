"""팀별 최근 폼·경기 변수 계산 — 분석 코멘트의 재료.

프리뷰 기사가 쓰는 근거를 **실제 데이터로만** 만든다.

기본 폼
    · 최근 10경기 전적, 연승/연패, 직전 3경기 스코어
    · 홈/원정 성적, 최근 득실점

경기 변수 (2026-07-26 확장)
    · **휴식일** — 직전 경기로부터 며칠 쉬었나. 연전 피로는 후반 집중력에 직결
    · **연전 길이** — 며칠 연속 경기 중인가
    · **득실 마진 추세** — 최근 5경기 vs 그 이전 5경기. 상승세인가 하락세인가
    · **접전 성향** — 1~2점차 경기 비율. 접전에 강한 팀인가
    · **폭발력 / 침묵** — 대량득점·영봉패 빈도
    · **상대전적 최근 스코어** — 맞대결에서 실제로 어떻게 흘렀나

⚠️ 없는 것은 지어내지 않는다.
   선발투수·부상자·라인업·타율은 프로토 아카이브에 없다.
   (KBO 공식 API 연동은 다음 단계 — docs 참조)
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Form:
    team: str
    league: str
    w: int = 0
    l: int = 0
    d: int = 0
    last10: list[str] = field(default_factory=list)
    streak_kind: str = ""
    streak_n: int = 0
    recent_games: list[dict] = field(default_factory=list)   # 최신순
    home_w: int = 0
    home_l: int = 0
    away_w: int = 0
    away_l: int = 0
    scored: list[int] = field(default_factory=list)          # 최근 10
    conceded: list[int] = field(default_factory=list)
    last_date: pd.Timestamp | None = None
    # --- 경기 변수 ---
    rest_days: int | None = None          # 마지막 경기 이후 휴식일
    streak_days: int = 0                  # 연전 길이
    close_games: int = 0                  # 최근 10경기 중 2점차 이내
    blowout_w: int = 0                    # 5점차 이상 승
    shutout_l: int = 0                    # 무득점 패
    margin_recent: float | None = None    # 최근 5경기 평균 득실 마진
    margin_prev: float | None = None      # 그 이전 5경기

    @property
    def last10_str(self) -> str:
        w, l, d = (self.last10.count(x) for x in "WLD")
        return f"{w}승 {l}패" + (f" {d}무" if d else "")

    @property
    def avg_scored(self) -> float | None:
        return sum(self.scored) / len(self.scored) if self.scored else None

    @property
    def avg_conceded(self) -> float | None:
        return sum(self.conceded) / len(self.conceded) if self.conceded else None

    @property
    def trend(self) -> str | None:
        """득실 마진 추세. 최근 5경기가 그 이전 5경기보다 나아졌나."""
        if self.margin_recent is None or self.margin_prev is None:
            return None
        d = self.margin_recent - self.margin_prev
        if d >= 1.0:
            return "상승"
        if d <= -1.0:
            return "하락"
        return "유지"

    @property
    def close_rate(self) -> float | None:
        return self.close_games / len(self.last10) if self.last10 else None


def load_history() -> pd.DataFrame:
    """실제 경기 단위 테이블 (중복 제거 + 날짜순). matches.py 참조."""
    from matches import load_matches
    return load_matches()


def build_forms(g: pd.DataFrame, season: int | None = None,
                as_of: pd.Timestamp | None = None) -> tuple[dict, dict]:
    """(리그, 팀) → Form, (리그, 팀A, 팀B) → 상대전적.

    as_of 를 주면 그 시점까지의 경기만 사용한다(누수 방지).
    """
    if season is not None:
        g = g[g["year"] == season]
    if as_of is not None:
        g = g[g["date"] <= as_of]

    forms: dict[tuple[str, str], Form] = {}
    h2h: dict[tuple, dict] = defaultdict(
        lambda: {"a": 0, "b": 0, "d": 0, "games": []})
    L10: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))
    REC: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))
    SC: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))
    CC: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))
    MG: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))
    DATES: dict[tuple, list] = defaultdict(list)

    def get(lg, t) -> Form:
        k = (lg, t)
        if k not in forms:
            forms[k] = Form(team=t, league=lg)
        return forms[k]

    for r in g.itertuples():
        lg, ht, at = r.league, r.home_team, r.away_team
        fh, fa = get(lg, ht), get(lg, at)
        hs, as_ = int(r.home_score), int(r.away_score)
        d = int(hs - as_)

        if hs > as_:
            fh.w += 1; fa.l += 1; fh.home_w += 1; fa.away_l += 1
            rh, ra = "W", "L"
        elif hs < as_:
            fh.l += 1; fa.w += 1; fh.home_l += 1; fa.away_w += 1
            rh, ra = "L", "W"
        else:
            fh.d += 1; fa.d += 1
            rh = ra = "D"

        for key, res, gf, ga, mg, at_ in (((lg, ht), rh, hs, as_, d, "홈"),
                                          ((lg, at), ra, as_, hs, -d, "원정")):
            L10[key].append(res)
            SC[key].append(gf)
            CC[key].append(ga)
            MG[key].append(mg)
            DATES[key].append(r.date)
            REC[key].append({"opp": at if key[1] == ht else ht, "at": at_,
                             "gf": gf, "ga": ga, "r": res,
                             "date": r.date})

        # 접전·대승·영봉
        for key, gf, ga, res in (((lg, ht), hs, as_, rh), ((lg, at), as_, hs, ra)):
            f = forms[key]
            if abs(gf - ga) <= 2:
                f.close_games += 1
            if res == "W" and gf - ga >= 5:
                f.blowout_w += 1
            if res == "L" and gf == 0:
                f.shutout_l += 1

        k = (lg,) + tuple(sorted([ht, at]))
        rec = h2h[k]
        if hs > as_:
            rec["a" if ht == k[1] else "b"] += 1
        elif hs < as_:
            rec["a" if at == k[1] else "b"] += 1
        else:
            rec["d"] += 1
        rec["games"].append({"date": r.date, "home": ht, "away": at,
                             "hs": hs, "as": as_})

    for k, f in forms.items():
        f.last10 = list(L10[k])
        f.recent_games = list(REC[k])[::-1]
        f.scored = list(SC[k])
        f.conceded = list(CC[k])
        # 접전·대승·영봉은 최근 10경기 기준으로 재계산한다.
        # (루프에서는 전 경기를 셌으므로 그대로 두면 '대승 20번' 같은 값이 나온다)
        rg = f.recent_games
        f.close_games = sum(1 for x in rg if abs(x["gf"] - x["ga"]) <= 2)
        f.blowout_w = sum(1 for x in rg if x["r"] == "W" and x["gf"] - x["ga"] >= 5)
        f.shutout_l = sum(1 for x in rg if x["r"] == "L" and x["gf"] == 0)

        seq = f.last10[::-1]
        if seq:
            first = seq[0]
            n = 0
            for s in seq:
                if s != first:
                    break
                n += 1
            if first in ("W", "L") and n >= 2:
                f.streak_kind = "연승" if first == "W" else "연패"
                f.streak_n = n

        mg = list(MG[k])
        if len(mg) >= 6:
            f.margin_recent = sum(mg[-5:]) / 5
            f.margin_prev = sum(mg[-10:-5]) / len(mg[-10:-5])

        ds = sorted(DATES[k])
        if ds:
            f.last_date = ds[-1]
            # 연전 길이: 하루 간격으로 이어진 경기 수
            n = 1
            for i in range(len(ds) - 1, 0, -1):
                if (ds[i] - ds[i - 1]).days == 1:
                    n += 1
                else:
                    break
            f.streak_days = n
    return forms, dict(h2h)


def set_rest_days(forms: dict, game_date: pd.Timestamp) -> None:
    """경기 예정일 기준 휴식일을 채운다."""
    for f in forms.values():
        if f.last_date is not None:
            f.rest_days = int((game_date - f.last_date).days)


def h2h_text(h2h: dict, league: str, a: str, b: str) -> str | None:
    key = (league,) + tuple(sorted([a, b]))
    rec = h2h.get(key)
    if not rec:
        return None
    first = key[1]
    aw = rec["a"] if first == a else rec["b"]
    bw = rec["b"] if first == a else rec["a"]
    if aw + bw + rec["d"] == 0:
        return None
    d = f" {rec['d']}무" if rec["d"] else ""
    from commentary import josa
    if aw == bw:
        base = f"시즌 맞대결은 {aw}승 {bw}패{d}로 팽팽하다"
    else:
        lead, ln, sn = (a, aw, bw) if aw > bw else (b, bw, aw)
        base = f"시즌 맞대결은 {josa(lead,'이','가')} {ln}승 {sn}패{d}로 앞선다"

    gs = rec.get("games", [])[-2:]
    if gs:
        sc = ", ".join(f"{g['hs']}-{g['as']}" for g in gs)
        base += f". 최근 두 번의 맞대결은 {sc}였다"
    return base
