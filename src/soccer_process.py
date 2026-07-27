"""축구 과정 지표 — 유효슈팅이 실제 골보다 나은가.

왜 유효슈팅인가
----------------
야구는 FIP 로 성공했다(시장 격차 1/3.6 축소). 같은 논리를 축구에 적용한다.

문헌이 쓰는 건 **xG** 다. 분데스리가 11시즌에서 xG 기반 모델이 ROI ≈10% 를 냈다.
그런데 K리그 xG 는 무료로 막혀 있다:

    Understat  유럽 5대리그만 · 봇 차단(TLS 지문)
    FBref      Cloudflare 차단
    SofaScore  403
    FotMob     API 경로 변경

**네이버에는 선수별 `shots`·`shotsOnGoal` 이 있다.**
유효슈팅은 xG 의 표준 대용품이다 — 슛의 '질'까지는 못 보지만,
**실제 골보다 앞단이고 운이 덜 섞인다.** 그게 과정 지표의 핵심이다.

검증 질문
---------
팀의 최근 **유효슈팅** 기록이 최근 **득점** 기록보다 다음 경기를 잘 설명하는가.

⚠️ 야구 FIP 와 같은 규율: ERA(결과) vs FIP(과정) 을 정면 비교했듯,
   여기서도 득점(결과) vs 유효슈팅(과정) 을 **같은 자리에서** 붙인다.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import build_features                    # noqa: E402
from matches import load_matches                       # noqa: E402
from variable_impact import _brier, _fit, _se          # noqa: E402

DETAIL = Path(__file__).resolve().parent.parent / "data" / "raw" / "detail"
TRAIN_END = 2024
WINDOW = 10


def load_shots() -> pd.DataFrame:
    f = next(DETAIL.glob("*_shots_*.json"), None)
    if f is None:
        return pd.DataFrame()
    raw = json.loads(f.read_text(encoding="utf-8"))
    rows = []
    for g in raw.values():
        d = g.get("data") or {}
        h, a = d.get("home") or {}, d.get("away") or {}
        if not h or not a:
            continue
        rows.append({
            "date": pd.to_datetime(g.get("date")),
            "home_team": g.get("home"), "away_team": g.get("away"),
            "h_shots": h.get("shots", 0), "h_sog": h.get("sog", 0),
            "a_shots": a.get("shots", 0), "a_sog": a.get("sog", 0),
            "h_goals": g.get("home_score"), "a_goals": g.get("away_score"),
        })
    return pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date")


def build_form(sh: pd.DataFrame) -> pd.DataFrame:
    """walk-forward — 각 경기 피처는 그 경기 이전 기록만."""
    hist: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    rows = []

    def stat(t):
        v = list(hist[t])
        if len(v) < 5:
            return None
        return {
            "gf": np.mean([x["gf"] for x in v]),
            "ga": np.mean([x["ga"] for x in v]),
            "sogf": np.mean([x["sogf"] for x in v]),
            "soga": np.mean([x["soga"] for x in v]),
            "shf": np.mean([x["shf"] for x in v]),
        }

    for r in sh.itertuples():
        h, a = stat(r.home_team), stat(r.away_team)
        out = {"date": r.date, "home_team": r.home_team, "away_team": r.away_team}
        if h and a:
            # 결과 지표: 득점 − 실점
            out["goal_diff"] = (h["gf"] - h["ga"]) - (a["gf"] - a["ga"])
            # 과정 지표: 유효슈팅 − 피유효슈팅
            out["sog_diff"] = (h["sogf"] - h["soga"]) - (a["sogf"] - a["soga"])
            # 총 슈팅(질 무시)
            out["shots_diff"] = h["shf"] - a["shf"]
            # 결정력 — 유효슈팅 대비 득점. 높으면 운이 좋았을 가능성
            out["conv_diff"] = ((h["gf"] / max(h["sogf"], .1))
                                - (a["gf"] / max(a["sogf"], .1)))
        else:
            for k in ("goal_diff", "sog_diff", "shots_diff", "conv_diff"):
                out[k] = np.nan
        rows.append(out)

        for t, gf, ga, sogf, soga, shf in (
                (r.home_team, r.h_goals, r.a_goals, r.h_sog, r.a_sog, r.h_shots),
                (r.away_team, r.a_goals, r.h_goals, r.a_sog, r.h_sog, r.a_shots)):
            hist[t].append({"gf": gf, "ga": ga, "sogf": sogf,
                            "soga": soga, "shf": shf})
    return pd.DataFrame(rows)


FEATS = ["goal_diff", "sog_diff", "shots_diff", "conv_diff"]
LABELS = {"goal_diff": "득실 차 (결과 지표)",
          "sog_diff": "유효슈팅 차 ⭐ 과정 지표",
          "shots_diff": "총 슈팅 차",
          "conv_diff": "결정력 차 (운 지표)"}


def main() -> int:
    sh = load_shots()
    if sh.empty:
        print("슈팅 데이터가 없습니다. python src/game_detail.py shots kleague")
        return 1
    print(f"슈팅 확보 {len(sh):,}경기 "
          f"({sh['date'].min().date()} ~ {sh['date'].max().date()})")
    print(f"  평균 유효슈팅 홈 {sh['h_sog'].mean():.2f} / 원정 {sh['a_sog'].mean():.2f}")
    print(f"  평균 득점     홈 {sh['h_goals'].mean():.2f} / 원정 {sh['a_goals'].mean():.2f}")

    ff = build_form(sh)
    m = load_matches()
    fe = build_features(m)
    sc = fe[(fe["sport"] == "sc") & (fe["outcome"] != 0.5)].copy()
    sc["date"] = pd.to_datetime(sc["date"])

    # 팀명 매핑 (lineup_impact 와 같은 방식)
    from lineup_impact import build_team_map
    lu = sh.rename(columns={"h_goals": "home_score", "a_goals": "away_score"})
    proto = m[m["sport"] == "sc"].copy()
    proto["date"] = pd.to_datetime(proto["date"])
    tmap = build_team_map(proto, lu)
    print(f"  팀명 매핑 {len(tmap)}팀")
    sc["home_team"] = sc["home_team"].map(lambda x: tmap.get(x, x))
    sc["away_team"] = sc["away_team"].map(lambda x: tmap.get(x, x))

    d = sc.merge(ff, on=["date", "home_team", "away_team"], how="inner")
    d = d.dropna(subset=["elo_diff"])
    tr, te = d[d["year"] <= TRAIN_END], d[d["year"] > TRAIN_END]
    print(f"\n프로토 결합 {len(d):,} · 학습 {len(tr):,} / 검증 {len(te):,}")
    if len(tr) < 200 or len(te) < 120:
        print("표본 부족 — 판정 불가")
        return 1

    def mk(x, cols):
        return np.column_stack([np.ones(len(x))]
                               + [x[c].to_numpy(float) for c in cols])

    y_tr = (tr["outcome"] == 1.0).to_numpy(float)
    y_te = (te["outcome"] == 1.0).to_numpy(float)
    b0 = _fit(mk(tr, ["elo_diff"]), y_tr)
    print(f"기준 (Elo 단독) 검증 Brier = "
          f"{_brier(mk(te, ['elo_diff']), b0, y_te):.5f}\n")

    print(f"{'피처':<26}{'n':>7}{'계수':>10}{'z':>8}{'Brier':>10}{'개선':>11}")
    print("-" * 74)
    sc_ = {}
    for f in FEATS:
        t2, v2 = tr.dropna(subset=[f]), te.dropna(subset=[f])
        if len(t2) < 150 or len(v2) < 100:
            print(f"{LABELS[f]:<26} 표본 부족 ({len(t2)}/{len(v2)})")
            continue
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        b = _fit(mk(t2, ["elo_diff", f]), yt)
        z = b[2] / _se(mk(t2, ["elo_diff", f]), b)[2]
        b_ref = _fit(mk(t2, ["elo_diff"]), yt)
        ref = _brier(mk(v2, ["elo_diff"]), b_ref, yv)
        br = _brier(mk(v2, ["elo_diff", f]), b, yv)
        sc_[f] = ref - br
        print(f"{LABELS[f]:<26}{len(t2):>7,}{b[2]:>10.4f}{z:>8.2f}"
              f"{br:>10.5f}{ref-br:>+11.5f}")

    if "goal_diff" in sc_ and "sog_diff" in sc_:
        print(f"\n⭐ 결과 vs 과정 정면 비교 (야구 FIP 검증과 같은 방식)")
        print(f"   득실 차   (결과) Brier 개선 {sc_['goal_diff']:+.5f}")
        print(f"   유효슈팅 차(과정) Brier 개선 {sc_['sog_diff']:+.5f}")
        w = "과정 지표" if sc_["sog_diff"] > sc_["goal_diff"] else "결과 지표"
        print(f"   → {w} 우위")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
