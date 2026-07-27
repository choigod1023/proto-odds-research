"""팀별 최근 폼 계산 — 분석 코멘트의 재료.

픽스터·프리뷰 기사가 쓰는 근거를 **실제 데이터로만** 만든다.
    · 최근 10경기 전적, 연승/연패
    · 직전 3경기 스코어
    · 홈/원정 성적
    · 최근 득실점
    · 시즌 상대전적

⚠️ 없는 것은 지어내지 않는다.
   선발투수·부상자·라인업·감독 코멘트는 이 데이터에 없으므로 언급하지 않는다.
   프로토 아카이브에는 스코어와 팀명만 있다.
"""
from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

GAMES = Path(__file__).resolve().parent.parent / "data" / "processed" / "games.csv"


@dataclass
class Form:
    team: str
    league: str
    w: int = 0
    l: int = 0
    d: int = 0
    last10: list[str] = field(default_factory=list)       # 'W'/'L'/'D'
    streak_kind: str = ""                                  # '연승'/'연패'
    streak_n: int = 0
    recent_games: list[dict] = field(default_factory=list)  # 최신순 3경기
    home_w: int = 0
    home_l: int = 0
    away_w: int = 0
    away_l: int = 0
    scored: list[int] = field(default_factory=list)
    conceded: list[int] = field(default_factory=list)

    @property
    def last10_str(self) -> str:
        w = self.last10.count("W")
        l = self.last10.count("L")
        d = self.last10.count("D")
        return f"{w}승 {l}패" + (f" {d}무" if d else "")

    @property
    def avg_scored(self) -> float | None:
        return sum(self.scored) / len(self.scored) if self.scored else None

    @property
    def avg_conceded(self) -> float | None:
        return sum(self.conceded) / len(self.conceded) if self.conceded else None


def _home(x):
    m = re.match(r"^(.+?)\s+(-?\d+)\s*$", str(x).strip())
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _away(x):
    m = re.match(r"^(-?\d+)\s+(.+?)\s*$", str(x).strip())
    return (int(m.group(1)), m.group(2)) if m else (None, None)


def load_history() -> pd.DataFrame:
    """실제 경기 단위 테이블 (중복 제거 + 날짜순). matches.py 참조."""
    from matches import load_matches
    return load_matches()


def build_forms(g: pd.DataFrame, season: int | None = None
                ) -> tuple[dict, dict]:
    """(리그, 팀) → Form,  (리그, 팀A, 팀B) → 상대전적 를 만든다.

    season 을 주면 그 시즌 경기만으로 집계한다(시즌 성적은 시즌 안에서 세는 게 맞다).
    """
    if season is not None:
        g = g[g["year"] == season]

    forms: dict[tuple[str, str], Form] = {}
    h2h: dict[tuple, dict] = defaultdict(lambda: {"a": 0, "b": 0, "d": 0})
    last10: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))
    recent: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=3))
    sc: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))
    cc: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=10))

    def get(lg, t) -> Form:
        k = (lg, t)
        if k not in forms:
            forms[k] = Form(team=t, league=lg)
        return forms[k]

    for r in g.itertuples():
        lg, ht, at = r.league, r.home_team, r.away_team
        fh, fa = get(lg, ht), get(lg, at)
        hs, as_ = int(r.home_score), int(r.away_score)

        if hs > as_:
            fh.w += 1; fa.l += 1; fh.home_w += 1; fa.away_l += 1
            rh, ra = "W", "L"
        elif hs < as_:
            fh.l += 1; fa.w += 1; fh.home_l += 1; fa.away_w += 1
            rh, ra = "L", "W"
        else:
            fh.d += 1; fa.d += 1
            rh = ra = "D"

        last10[(lg, ht)].append(rh); last10[(lg, at)].append(ra)
        sc[(lg, ht)].append(hs); cc[(lg, ht)].append(as_)
        sc[(lg, at)].append(as_); cc[(lg, at)].append(hs)
        recent[(lg, ht)].append({"opp": at, "at": "홈", "gf": hs, "ga": as_, "r": rh})
        recent[(lg, at)].append({"opp": ht, "at": "원정", "gf": as_, "ga": hs, "r": ra})

        key = (lg,) + tuple(sorted([ht, at]))
        rec = h2h[key]
        if hs > as_:
            rec["a" if ht == key[1] else "b"] += 1
        elif hs < as_:
            rec["a" if at == key[1] else "b"] += 1
        else:
            rec["d"] += 1

    for k, f in forms.items():
        f.last10 = list(last10[k])
        f.recent_games = list(recent[k])[::-1]
        f.scored = list(sc[k])
        f.conceded = list(cc[k])
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
    return forms, dict(h2h)


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
    if aw == bw:
        return f"시즌 맞대결은 {aw}승 {bw}패{d}로 팽팽하다"
    lead, ln, sn = (a, aw, bw) if aw > bw else (b, bw, aw)
    from commentary import josa
    return f"시즌 맞대결은 {josa(lead,'이','가')} {ln}승 {sn}패{d}로 앞선다"
