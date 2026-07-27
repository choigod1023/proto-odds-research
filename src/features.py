"""경기별 피처 생성 — **경기 전 시점 정보만** 사용한다.

왜 한 번에 훑으며 만드는가
--------------------------
"최근 10경기 성적"을 나중에 계산하면 그 경기 결과가 이미 섞여 들어간다(누수).
그래서 날짜순으로 한 번 훑으면서 **경기 직전 상태를 먼저 기록하고, 그 다음에 갱신**한다.
`wc2026-predictor` 의 elo_diff_pre 와 같은 규율이다.

생성 피처 (홈 기준, 원정 대비 차이값)
    elo_diff        Elo 차 (홈 어드밴티지 포함)
    form_diff       최근 10경기 승률 차
    margin_diff     최근 10경기 평균 득실 마진 차
    trend_diff      마진 추세 차 (최근5 − 그이전5)
    rest_diff       휴식일 차
    b2b_home/away   백투백 여부(휴식 0~1일)
    streak_diff     연승/연패 차 (연승 +, 연패 −)
    venue_diff      홈팀 홈승률 − 원정팀 원정승률
    h2h_diff        시즌 맞대결 승률 차
"""
from __future__ import annotations

from collections import defaultdict, deque

import numpy as np
import pandas as pd

BASE = 1500.0
HOME_ADV = 45.0
K_BY_SPORT = {"bs": 12.0, "bk": 16.0, "sc": 24.0, "vl": 18.0}


def _rate(dq) -> float | None:
    if not dq:
        return None
    w = sum(1 for x in dq if x == "W")
    d = sum(1 for x in dq if x == "D")
    return (w + 0.5 * d) / len(dq)


def build_features(m: pd.DataFrame) -> pd.DataFrame:
    """날짜순 1패스. 각 경기의 피처는 그 경기 '이전' 상태로만 만든다."""
    rating: dict = defaultdict(lambda: BASE)
    res: dict = defaultdict(lambda: deque(maxlen=10))
    mgn: dict = defaultdict(lambda: deque(maxlen=10))
    last_date: dict = {}
    streak_days: dict = defaultdict(int)
    venue_w: dict = defaultdict(int)
    venue_n: dict = defaultdict(int)
    h2h_w: dict = defaultdict(int)
    h2h_n: dict = defaultdict(int)

    rows = []
    for r in m.itertuples():
        lg, ht, at, sp = r.league, r.home_team, r.away_team, r.sport
        kh, ka = (lg, ht), (lg, at)
        d = r.date

        # ---------- 경기 전 상태로 피처 생성 ----------
        elo_diff = rating[kh] + HOME_ADV - rating[ka]
        fh, fa = _rate(res[kh]), _rate(res[ka])
        mh = np.mean(mgn[kh]) if mgn[kh] else None
        ma = np.mean(mgn[ka]) if mgn[ka] else None

        def trend(k):
            v = list(mgn[k])
            if len(v) < 6:
                return None
            return float(np.mean(v[-5:]) - np.mean(v[-10:-5]))

        th, ta = trend(kh), trend(ka)
        rh = (d - last_date[kh]).days if kh in last_date else None
        ra = (d - last_date[ka]).days if ka in last_date else None

        def strk(k):
            v = list(res[k])[::-1]
            if not v:
                return 0
            n = 0
            for x in v:
                if x != v[0]:
                    break
                n += 1
            return n if v[0] == "W" else (-n if v[0] == "L" else 0)

        vh = venue_w[("H", kh)] / venue_n[("H", kh)] if venue_n[("H", kh)] >= 5 else None
        va = venue_w[("A", ka)] / venue_n[("A", ka)] if venue_n[("A", ka)] >= 5 else None
        pair = (lg,) + tuple(sorted([ht, at]))
        hh = (h2h_w[(pair, ht)] / h2h_n[pair]) if h2h_n[pair] >= 3 else None
        ha = (h2h_w[(pair, at)] / h2h_n[pair]) if h2h_n[pair] >= 3 else None

        rows.append({
            "date": d, "year": r.year, "league": lg, "sport": sp,
            "home_team": ht, "away_team": at, "outcome": r.outcome,
            "elo_diff": elo_diff,
            "form_diff": (fh - fa) if None not in (fh, fa) else np.nan,
            "margin_diff": (mh - ma) if None not in (mh, ma) else np.nan,
            "trend_diff": (th - ta) if None not in (th, ta) else np.nan,
            "rest_diff": (rh - ra) if None not in (rh, ra) else np.nan,
            "rest_home": rh if rh is not None else np.nan,
            "rest_away": ra if ra is not None else np.nan,
            "b2b_home": 1.0 if (rh is not None and rh <= 1) else 0.0,
            "b2b_away": 1.0 if (ra is not None and ra <= 1) else 0.0,
            "streak_diff": float(strk(kh) - strk(ka)),
            "venue_diff": (vh - va) if None not in (vh, va) else np.nan,
            "h2h_diff": (hh - ha) if None not in (hh, ha) else np.nan,
        })

        # ---------- 상태 갱신 (피처 생성 이후) ----------
        hs, as_ = int(r.home_score), int(r.away_score)
        gap = hs - as_
        rh_, ra_ = ("W", "L") if gap > 0 else (("L", "W") if gap < 0 else ("D", "D"))
        res[kh].append(rh_); res[ka].append(ra_)
        mgn[kh].append(gap); mgn[ka].append(-gap)

        venue_n[("H", kh)] += 1; venue_n[("A", ka)] += 1
        if gap > 0:
            venue_w[("H", kh)] += 1
        elif gap < 0:
            venue_w[("A", ka)] += 1
        h2h_n[pair] += 1
        if gap > 0:
            h2h_w[(pair, ht)] += 1
        elif gap < 0:
            h2h_w[(pair, at)] += 1

        for k, dt in ((kh, d), (ka, d)):
            if k in last_date and (dt - last_date[k]).days == 1:
                streak_days[k] += 1
            else:
                streak_days[k] = 1
            last_date[k] = dt

        exp_h = 1.0 / (1.0 + 10 ** (-elo_diff / 400.0))
        k_ = K_BY_SPORT.get(sp, 16.0)
        mult = min(np.log1p(abs(gap)) / np.log(2), 3.0) if gap else 1.0
        delta = k_ * mult * (r.outcome - exp_h)
        rating[kh] += delta
        rating[ka] -= delta

    return pd.DataFrame(rows)


FEATURES = ["form_diff", "margin_diff", "trend_diff", "rest_diff",
            "b2b_home", "b2b_away", "streak_diff", "venue_diff", "h2h_diff"]

LABELS = {
    "form_diff": "최근 10경기 승률 차",
    "margin_diff": "최근 득실 마진 차",
    "trend_diff": "마진 추세 차 (상승/하락)",
    "rest_diff": "휴식일 차",
    "b2b_home": "홈팀 백투백(휴식 0~1일)",
    "b2b_away": "원정팀 백투백",
    "streak_diff": "연승/연패 차",
    "venue_diff": "홈승률 − 원정승률",
    "h2h_diff": "시즌 맞대결 우위",
}
