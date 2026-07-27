"""선발투수 피처가 야구에서 Elo를 넘는가.

배경
----
`variable_impact.py` 에서 **야구는 팀 단위 변수 9개가 전부 Elo를 못 넘었다.**
그 자리를 선발투수가 채우는지 확인한다. 야구에서 이게 안 되면 남는 길이 없다.

투수 피처 (walk-forward, 누수 없음)
    p_ra    그 투수가 선발 등판한 최근 경기에서 **팀이 내준 점수** (낮을수록 좋음)
    p_wr    그 투수 선발 경기의 팀 승률
    p_n     표본 경기 수

    starter_ra_diff = (원정 선발 p_ra) − (홈 선발 p_ra)     홈에 유리하면 +
    starter_wr_diff = (홈 선발 p_wr) − (원정 선발 p_wr)

⚠️ 팀 실점은 선발 혼자의 성적이 아니다(불펜 포함). 투수 개인 자책점은 이 데이터에 없다.
   그래도 **선발이 누구냐로 팀 실점이 달라지는가**는 잴 수 있고, 그게 첫 질문이다.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import build_features                    # noqa: E402
from matches import load_matches                       # noqa: E402
from variable_impact import _brier, _fit, _se          # noqa: E402

STARTERS = Path(__file__).resolve().parent.parent / "data" / "raw" / "kbo_starters.json"
TRAIN_END = 2024
WINDOW = 12          # 투수별 최근 등판 표본
MIN_START = 4        # 이보다 적으면 신뢰하지 않는다


def load_starters() -> pd.DataFrame:
    rows = json.loads(STARTERS.read_text(encoding="utf-8"))
    df = pd.DataFrame(rows)
    df = df[(df["home_starter"] != "") & (df["away_starter"] != "")]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "home_score", "away_score"])
    df = df[df["status"].astype(str).str.contains("종료|회말|회초", na=False)
            | df["home_score"].gt(0) | df["away_score"].gt(0)]
    return df.sort_values("date").reset_index(drop=True)


def build_pitcher_features(sd: pd.DataFrame) -> pd.DataFrame:
    """날짜순 1패스. 각 경기 피처는 **그 경기 이전** 등판 기록만 쓴다."""
    ra: dict = defaultdict(lambda: deque(maxlen=WINDOW))   # 등판 시 팀 실점
    win: dict = defaultdict(lambda: deque(maxlen=WINDOW))  # 등판 시 팀 승패
    rows = []

    for r in sd.itertuples():
        hp, ap = r.home_starter, r.away_starter

        def stat(p):
            n = len(ra[p])
            if n < MIN_START:
                return None, None, n
            return float(np.mean(ra[p])), float(np.mean(win[p])), n

        h_ra, h_wr, h_n = stat(hp)
        a_ra, a_wr, a_n = stat(ap)

        rows.append({
            "date": r.date, "home_team": r.home, "away_team": r.away,
            "home_starter": hp, "away_starter": ap,
            "h_ra": h_ra, "a_ra": a_ra, "h_wr": h_wr, "a_wr": a_wr,
            "p_n_min": min(h_n, a_n),
            "starter_ra_diff": (a_ra - h_ra) if None not in (h_ra, a_ra) else np.nan,
            "starter_wr_diff": (h_wr - a_wr) if None not in (h_wr, a_wr) else np.nan,
        })

        hs, as_ = int(r.home_score), int(r.away_score)
        ra[hp].append(as_)                 # 홈 선발 등판 → 팀이 내준 점수 = 원정 득점
        ra[ap].append(hs)
        win[hp].append(1.0 if hs > as_ else (0.5 if hs == as_ else 0.0))
        win[ap].append(1.0 if as_ > hs else (0.5 if hs == as_ else 0.0))

    return pd.DataFrame(rows)


def main() -> int:
    if not STARTERS.exists():
        print("먼저 python src/kbo_starters.py 를 실행하세요.")
        return 1
    sd = load_starters()
    print(f"선발 확보 경기 {len(sd):,}건 "
          f"({sd['date'].min().date()} ~ {sd['date'].max().date()})")
    pf = build_pitcher_features(sd)

    m = load_matches()
    fe = build_features(m)
    kbo = fe[(fe["league"] == "KBO") & (fe["outcome"] != 0.5)].copy()
    kbo["date"] = pd.to_datetime(kbo["date"])

    df = kbo.merge(pf, on=["date", "home_team", "away_team"], how="inner")
    print(f"프로토 KBO 경기와 결합: {len(df):,}건")

    df = df.dropna(subset=["elo_diff"])
    tr, te = df[df["year"] <= TRAIN_END], df[df["year"] > TRAIN_END]
    print(f"학습 {len(tr):,} / 검증 {len(te):,}\n")

    def mk(d, cols):
        return np.column_stack([np.ones(len(d))]
                               + [d[c].to_numpy(float) for c in cols])

    y_tr = (tr["outcome"] == 1.0).to_numpy(float)
    y_te = (te["outcome"] == 1.0).to_numpy(float)
    b0 = _fit(mk(tr, ["elo_diff"]), y_tr)
    base = _brier(mk(te, ["elo_diff"]), b0, y_te)
    print(f"기준 (Elo 단독) 검증 Brier = {base:.5f}\n")

    print(f"{'피처':<24}{'n(학습)':>9}{'계수':>10}{'z':>8}{'Brier':>10}{'개선':>11}  판정")
    print("-" * 76)
    for f in ("starter_ra_diff", "starter_wr_diff"):
        t2 = tr.dropna(subset=[f])
        v2 = te.dropna(subset=[f])
        if len(t2) < 300 or len(v2) < 150:
            print(f"{f:<24} 표본 부족 ({len(t2)}/{len(v2)})")
            continue
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        b = _fit(mk(t2, ["elo_diff", f]), yt)
        se = _se(mk(t2, ["elo_diff", f]), b)
        z = b[2] / se[2] if se[2] > 0 else 0.0
        b_ref = _fit(mk(t2, ["elo_diff"]), yt)
        ref = _brier(mk(v2, ["elo_diff"]), b_ref, yv)
        br = _brier(mk(v2, ["elo_diff", f]), b, yv)
        ok = abs(z) >= 2.58 and (ref - br) > 0
        print(f"{f:<24}{len(t2):>9,}{b[2]:>10.4f}{z:>8.2f}{br:>10.5f}"
              f"{ref-br:>+11.5f}  {'✅ 채택' if ok else '❌'}")

    # 둘 다
    both = ["starter_ra_diff", "starter_wr_diff"]
    t2, v2 = tr.dropna(subset=both), te.dropna(subset=both)
    if len(t2) >= 300 and len(v2) >= 150:
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        b = _fit(mk(t2, ["elo_diff"] + both), yt)
        b_ref = _fit(mk(t2, ["elo_diff"]), yt)
        ref = _brier(mk(v2, ["elo_diff"]), b_ref, yv)
        br = _brier(mk(v2, ["elo_diff"] + both), b, yv)
        print(f"{'둘 다':<24}{len(t2):>9,}{'':>10}{'':>8}{br:>10.5f}{ref-br:>+11.5f}"
              f"  {'✅' if ref - br > 0 else '❌'}")

        # 시장과 비교
        from model_v2 import attach_odds
        v3 = attach_odds(v2.assign(league="KBO"))
        if len(v3) > 150:
            yv3 = (v3["outcome"] == 1.0).to_numpy(float)
            p = 1 / (1 + np.exp(-np.clip(mk(v3, ["elo_diff"] + both) @ b, -30, 30)))
            ov = 1 / v3["o_home"] + 1 / v3["o_away"]
            pm = ((1 / v3["o_home"]) / ov).to_numpy(float)
            print(f"\n시장 비교 (검증 {len(v3):,}경기)")
            print(f"  모델(Elo+투수) Brier {np.mean((p-yv3)**2):.5f}   "
                  f"시장 Brier {np.mean((pm-yv3)**2):.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
