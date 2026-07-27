"""ERA → FIP 교체 — '결과 지표'에서 '과정 지표'로.

근본 진단
---------
지금까지 이 프로젝트는 **결과(outcome) 지표만** 썼다.

    승패 · 실제 득점 · 자책점(ERA)

그런데 문헌은 일관되게 **과정(process) 지표**가 예측력이 높다고 말한다.

  · 축구 — xG(기대득점)로 승무패 확률을 추정한 모델이 분데스리가 11시즌에서
    평균 배당 기준 ROI ≈10%, 최적 배당 기준 ≈15%
    (Wilkens 2026, *Journal of Sports Analytics*)
  · 야구 — **SIERA·xFIP 가 ERA·FIP 보다 다음 시즌 ERA 를 잘 예측**한다.
    결정적으로 **작은 표본에서 격차가 크고, 400이닝쯤에서 사라진다.**
    우리는 최근 12등판(≈70이닝)을 쓰므로 **정확히 그 구간**이다.

왜 그런가: ERA 는 **수비력과 BABIP 운**에 오염된다.
투수가 실제로 통제하는 건 삼진·볼넷·피홈런이고, FIP 는 그것만 쓴다.

    FIP = (13·HR + 3·BB − 2·K) / IP + C

우리 박스스코어에 kk(삼진)·bb(볼넷)·hr(홈런)·inn(이닝)이 모두 있다.
**추가 수집 없이 지금 바로 교체할 수 있다.**

⚠️ 채택은 측정 후. ERA 보다 Brier 가 낮지 않으면 쓰지 않는다.
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
from pitcher_er import _inn, load_detail               # noqa: E402
from variable_impact import _brier, _fit, _se          # noqa: E402

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
TEAM_MAP = PROC / "team_map.json"
TRAIN_END = 2024
WINDOW = 12
MIN_IP = 15.0          # 이 이닝 미만이면 지표를 신뢰하지 않는다


def build(df: pd.DataFrame, fip_const: float) -> pd.DataFrame:
    """선발별 ERA·FIP 를 walk-forward 로 만든다."""
    hist: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    rows = []

    def stat(p):
        v = list(hist[p])
        ip = sum(x["ip"] for x in v)
        if ip < MIN_IP:
            return None
        er = sum(x["er"] for x in v)
        hr = sum(x["hr"] for x in v)
        bb = sum(x["bb"] for x in v)
        kk = sum(x["kk"] for x in v)
        return {
            "era": er / ip * 9,
            # 투수가 통제하는 것만: 삼진·볼넷·피홈런
            "fip": (13 * hr + 3 * bb - 2 * kk) / ip + fip_const,
            "k9": kk / ip * 9,
            "bb9": bb / ip * 9,
            "hr9": hr / ip * 9,
            "ip": ip / len(v),
        }

    for r in df.itertuples():
        hp, ap = r.home_sp.get("name"), r.away_sp.get("name")
        sh, sa = stat(hp), stat(ap)
        out = {"date": r.date, "home_team": r.home_team, "away_team": r.away_team}
        if sh and sa:
            # 홈에 유리한 방향(원정 선발이 나쁠수록 +)
            out["era_diff"] = sa["era"] - sh["era"]
            out["fip_diff"] = sa["fip"] - sh["fip"]
            out["ip_diff"] = sh["ip"] - sa["ip"]
            out["k9_diff"] = sh["k9"] - sa["k9"]
            out["bb9_diff"] = sa["bb9"] - sh["bb9"]
            out["hr9_diff"] = sa["hr9"] - sh["hr9"]
        else:
            for k in ("era_diff", "fip_diff", "ip_diff", "k9_diff",
                      "bb9_diff", "hr9_diff"):
                out[k] = np.nan
        rows.append(out)

        for name, p in ((hp, r.home_sp), (ap, r.away_sp)):
            hist[name].append({
                "ip": _inn(p.get("inn")), "er": float(p.get("er") or 0),
                "hr": float(p.get("hr") or 0), "bb": float(p.get("bb") or 0),
                "kk": float(p.get("kk") or 0)})
    return pd.DataFrame(rows)


FEATS = ["era_diff", "fip_diff", "ip_diff", "k9_diff", "bb9_diff", "hr9_diff"]
LABELS = {"era_diff": "자책률 차 (기존)", "fip_diff": "FIP 차 ⭐ 과정 지표",
          "ip_diff": "평균 이닝 차", "k9_diff": "K/9 차",
          "bb9_diff": "BB/9 차", "hr9_diff": "HR/9 차"}


def main() -> int:
    det = load_detail()

    # FIP 상수 — 리그 평균 ERA 와 FIP 원값을 맞추는 값. 학습 구간에서 구한다.
    tr_det = det[det["date"] < f"{TRAIN_END+1}-01-01"]
    tot_ip = tot_er = tot_hr = tot_bb = tot_kk = 0.0
    for r in tr_det.itertuples():
        for p in (r.home_sp, r.away_sp):
            tot_ip += _inn(p.get("inn"))
            tot_er += float(p.get("er") or 0)
            tot_hr += float(p.get("hr") or 0)
            tot_bb += float(p.get("bb") or 0)
            tot_kk += float(p.get("kk") or 0)
    lg_era = tot_er / tot_ip * 9
    raw = (13 * tot_hr + 3 * tot_bb - 2 * tot_kk) / tot_ip
    fip_const = lg_era - raw
    print(f"학습 구간 리그 선발 ERA {lg_era:.3f} · FIP 상수 {fip_const:.3f}")

    pf = build(det, fip_const)
    m = load_matches()
    fe = build_features(m)
    kbo = fe[(fe["league"] == "KBO") & (fe["outcome"] != 0.5)].copy()
    kbo["date"] = pd.to_datetime(kbo["date"])
    tmap = json.loads(TEAM_MAP.read_text(encoding="utf-8")).get("KBO", {}) \
        if TEAM_MAP.exists() else {}
    kbo["home_team"] = kbo["home_team"].map(lambda x: tmap.get(x, x))
    kbo["away_team"] = kbo["away_team"].map(lambda x: tmap.get(x, x))

    d = kbo.merge(pf, on=["date", "home_team", "away_team"], how="inner")
    d = d.dropna(subset=["elo_diff"])
    tr, te = d[d["year"] <= TRAIN_END], d[d["year"] > TRAIN_END]
    print(f"결합 {len(d):,} · 학습 {len(tr):,} / 검증 {len(te):,}\n")

    def mk(x, cols):
        return np.column_stack([np.ones(len(x))]
                               + [x[c].to_numpy(float) for c in cols])

    y_tr = (tr["outcome"] == 1.0).to_numpy(float)
    y_te = (te["outcome"] == 1.0).to_numpy(float)
    b0 = _fit(mk(tr, ["elo_diff"]), y_tr)
    base = _brier(mk(te, ["elo_diff"]), b0, y_te)
    print(f"기준 (Elo 단독) 검증 Brier = {base:.5f}\n")

    print(f"{'피처':<22}{'n':>7}{'계수':>10}{'z':>8}{'Brier':>10}{'개선':>11}  판정")
    print("-" * 72)
    scores = {}
    for f in FEATS:
        t2, v2 = tr.dropna(subset=[f]), te.dropna(subset=[f])
        if len(t2) < 300 or len(v2) < 150:
            continue
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        b = _fit(mk(t2, ["elo_diff", f]), yt)
        z = b[2] / _se(mk(t2, ["elo_diff", f]), b)[2]
        b_ref = _fit(mk(t2, ["elo_diff"]), yt)
        ref = _brier(mk(v2, ["elo_diff"]), b_ref, yv)
        br = _brier(mk(v2, ["elo_diff", f]), b, yv)
        scores[f] = ref - br
        good = abs(z) >= 2.58 and (ref - br) > 0
        print(f"{LABELS[f]:<22}{len(t2):>7,}{b[2]:>10.4f}{z:>8.2f}"
              f"{br:>10.5f}{ref-br:>+11.5f}  {'✅' if good else '❌'}")

    # ERA vs FIP 정면 비교
    if "era_diff" in scores and "fip_diff" in scores:
        print(f"\n⭐ ERA vs FIP 정면 비교")
        print(f"   자책률(결과 지표) Brier 개선 {scores['era_diff']:+.5f}")
        print(f"   FIP  (과정 지표) Brier 개선 {scores['fip_diff']:+.5f}")
        w = "FIP" if scores["fip_diff"] > scores["era_diff"] else "ERA"
        print(f"   → {w} 우위")

    # 최적 조합
    best = [f for f, v in scores.items() if v > 0]
    if best:
        t2, v2 = tr.dropna(subset=best), te.dropna(subset=best)
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        b = _fit(mk(t2, ["elo_diff"] + best), yt)
        br = _brier(mk(v2, ["elo_diff"] + best), b, yv)
        print(f"\n개선 피처 전부({len(best)}개): Brier {br:.5f}")

        from model_v2 import attach_odds
        v3 = attach_odds(v2.assign(league="KBO"))
        if len(v3) > 150:
            yv3 = (v3["outcome"] == 1.0).to_numpy(float)
            p = 1 / (1 + np.exp(-np.clip(mk(v3, ["elo_diff"] + best) @ b, -30, 30)))
            ov = 1 / v3["o_home"] + 1 / v3["o_away"]
            pm = ((1 / v3["o_home"]) / ov).to_numpy(float)
            bm, bk = float(np.mean((p - yv3) ** 2)), float(np.mean((pm - yv3) ** 2))
            print(f"\n⭐ 시장 비교 (검증 {len(v3):,}경기)")
            print(f"   모델 Brier {bm:.5f}   시장 Brier {bk:.5f}   "
                  f"격차 {bm-bk:+.5f}  {'✅ 모델 우위' if bm < bk else '❌ 시장 우위'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
