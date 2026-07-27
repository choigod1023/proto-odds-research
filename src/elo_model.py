"""리그별 Elo 레이팅 → 승률 확률 모델.

왜 필요한가
------------
Q0에서 확인했듯 **배당을 쪼개는 것만으로는 +EV 구간이 없다.**
픽을 찍으려면 시장과 **다른 확률 추정**이 있어야 하고, 그게 모델이다.

    EV = 내 확률 × 배당 − 1

내 확률이 시장보다 정확한 경기에서만 EV가 양수가 된다.

설계
----
· 리그별로 독립된 Elo (KBO와 NBA는 다른 세계다)
· **시간 순서 엄수** — 각 경기의 확률은 그 경기 *이전* 레이팅만 사용 (walk-forward)
· Elo 차 → 승률은 로지스틱. 스케일 s 와 홈 어드밴티지 h 는 학습 구간에서 적합
· 종목별 K 값: 경기 수가 많은 야구는 작게, 적은 축구는 크게

이건 `wc2026-predictor` 의 Elo 엔진과 같은 계열이되, 리그·종목이 섞인
프로토 데이터에 맞춰 리그 단위로 분리했다.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

GAMES = Path(__file__).resolve().parent.parent / "data" / "processed" / "games.csv"

BASE = 1500.0
K_BY_SPORT = {"bs": 12.0, "bk": 16.0, "sc": 24.0, "vl": 18.0}
DEFAULT_K = 16.0


def _home(x: str) -> tuple[str | None, int | None]:
    m = re.match(r"^(.+?)\s+(-?\d+)\s*$", str(x).strip())
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _away(x: str) -> tuple[int | None, str | None]:
    m = re.match(r"^(-?\d+)\s+(.+?)\s*$", str(x).strip())
    return (int(m.group(1)), m.group(2)) if m else (None, None)


def load_results() -> pd.DataFrame:
    """실제 경기 단위 테이블 (중복 제거 + 날짜순).

    ⚠️ 회차 순서로 정렬하면 안 된다. 같은 경기가 여러 회차에 중복 발매되고
       회차 번호는 시간 순서가 아니다. 자세한 내용은 matches.py 참조.
    """
    from matches import load_matches
    return load_matches()


def run_elo(g: pd.DataFrame, scale: float = 400.0, home_adv: float = 45.0
            ) -> pd.DataFrame:
    """시간 순서대로 Elo를 갱신하며 **경기 전** 레이팅을 기록한다.

    반환된 elo_diff_pre 는 그 경기 시점에 알 수 있었던 정보만 담는다(누수 없음).
    """
    ratings: dict[tuple[str, str], float] = {}
    diffs, hr, ar = [], [], []

    for r in g.itertuples():
        kh = (r.league, r.home_team)
        ka = (r.league, r.away_team)
        rh = ratings.get(kh, BASE)
        ra = ratings.get(ka, BASE)
        hr.append(rh)
        ar.append(ra)
        diffs.append(rh + home_adv - ra)

        exp_h = 1.0 / (1.0 + 10 ** (-(rh + home_adv - ra) / scale))
        k = K_BY_SPORT.get(r.sport, DEFAULT_K)
        # 점수차가 클수록 조금 더 크게 갱신 (과도한 반응은 억제)
        margin = abs(r.home_score - r.away_score)
        mult = np.log1p(margin) / np.log(2) if margin > 0 else 1.0
        mult = min(mult, 3.0)
        delta = k * mult * (r.outcome - exp_h)
        ratings[kh] = rh + delta
        ratings[ka] = ra - delta

    out = g.copy()
    out["elo_home_pre"] = hr
    out["elo_away_pre"] = ar
    out["elo_diff_pre"] = diffs
    return out, ratings


def fit_logistic(train: pd.DataFrame) -> tuple[float, float]:
    """elo_diff_pre → 홈 승률 로지스틱 적합 (무승부는 0.5로 처리).

    반환: (a, b) 로 p_home = 1/(1+exp(-(a + b·diff)))
    """
    x = train["elo_diff_pre"].to_numpy(float)
    y = train["outcome"].to_numpy(float)
    a, b = 0.0, 1.0 / 400.0
    for _ in range(200):
        z = a + b * x
        p = 1.0 / (1.0 + np.exp(-z))
        w = np.clip(p * (1 - p), 1e-6, None)
        r = y - p
        # 뉴턴-랩슨 (2x2)
        g0, g1 = r.sum(), (r * x).sum()
        h00, h01, h11 = w.sum(), (w * x).sum(), (w * x * x).sum()
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        a += da
        b += db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return float(a), float(b)


def prob_home(diff: float | np.ndarray, a: float, b: float):
    return 1.0 / (1.0 + np.exp(-(a + b * np.asarray(diff, dtype=float))))
