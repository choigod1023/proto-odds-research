"""과거 박빙 xFIP 실험을 재현하는 감사용 스크립트 — 운영 채택 금지.

2026-08-26 인과 재감사(`findings/xfip_causal_audit_reference.json`)에서 이
스크립트가 전제로 삼은 xFIP 피처에 미래 리그평균과
같은 날짜 경기 순서 누수가 있었음이 확인됐다. 누수를 제거한 재실험에서는 모델 적중률이
시장보다 0.24%p 낮고 Brier도 악화했다. 따라서 아래 계산에서 과거의 음수 격차가 다시
나오더라도 예측 우위나 추천 승격 근거가 아니다.

그런데 그건 **모델 내부 비교**였다. 진짜 물어야 할 건 이것이다:

    전체 구간에서는 시장이 앞선다. 박빙에서도 그런가?

과거 실험의 규율
----------------
1. **박빙은 시장 확률로 정의한다.** 모델 확률로 자르면 모델이 헷갈리는 경기만
   골라내는 셈이라 자기 편한 표본이 된다. 시장 확률은 경기 전에 알 수 있고
   비교 기준 자체이므로 공정하다.
2. **시간 분리.** 학습 ≤2024 / 검증 2025~. random split 금지.
3. **같은 경기에 같은 잣대.** 시장·모델 모두 동일한 검증 표본에서 Brier 를 잰다.
4. 프로토는 같은 경기를 여러 회차에 중복 발매한다 → matches.py 와 같은 방식으로
   (리그, 홈, 원정, 날짜) 중복 제거. 안 하면 표본이 1.4배로 부풀고 결론이 바뀐다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import multiplicative                       # noqa: E402
from features import build_features                    # noqa: E402
from matches import GAMES, _DATE_RE, _away, _home, load_matches   # noqa: E402
from pitcher_xfip import (TRAIN_END, build, load_full)  # noqa: E402
from variable_impact import _brier, _fit               # noqa: E402

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"

# 과거 실험의 박빙 정의. 밴드 자체도 채택 근거가 아니다.
CLOSE_LO, CLOSE_HI = 0.45, 0.55


def load_market(league: str = "KBO") -> pd.DataFrame:
    """정산된 승패(2-way) 경기의 **시장 확률**. matches.py 와 같은 규약으로 판다.

    승패(2-way)를 쓰는 이유: 홈/원정 두 갈래뿐이라 devig 이 깔끔하고,
    무승부 처리에서 생기는 해석 여지가 없다.
    """
    g = pd.read_csv(GAMES)
    g = g[(~g["is_void"].astype(bool))
          & (g["league"] == league)
          & (g["market_family"] == "승패")
          & (g["n_way"] == 2)
          & (g["result"].isin(["홈승", "홈패"]))]

    hs, aw = g["home"].map(_home), g["away"].map(_away)
    g = g.assign(home_team=[t for t, _ in hs], away_team=[t for _, t in aw])
    g = g.dropna(subset=["home_team", "away_team"])

    md = g["date_text"].astype(str).str.extract(_DATE_RE)
    g = g.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                 _dd=pd.to_numeric(md[1], errors="coerce")).dropna(subset=["_mm", "_dd"])
    g["date"] = pd.to_datetime(
        dict(year=g["year"], month=g["_mm"].astype(int), day=g["_dd"].astype(int)),
        errors="coerce")
    g = g.dropna(subset=["date"])

    # 배당은 'a,b' 문자열 — 승패는 [홈, 원정] 순서다(bets.py 의 매핑 규약).
    def _mkt(s):
        try:
            o = [float(x) for x in str(s).split(",")]
        except ValueError:
            return np.nan
        if len(o) != 2 or min(o) <= 1.0:
            return np.nan
        return multiplicative(o)[0]          # devig 후 홈 승 확률

    g["p_mkt"] = g["odds"].map(_mkt)
    g = g.dropna(subset=["p_mkt"])

    # ⭐ 중복 발매 제거 — 회차별로 배당이 조금씩 다르므로 경기당 중앙값
    g = (g.groupby(["home_team", "away_team", "date"], as_index=False)
           .agg(p_mkt=("p_mkt", "median"), year=("year", "first")))
    return g


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def _predict(X: np.ndarray, beta: np.ndarray) -> np.ndarray:
    """_brier 내부와 같은 식 — 경기별 확률이 필요해서 따로 뺀다."""
    return 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30)))


def main() -> int:
    print("[정정] 이 스크립트는 시간누수가 포함된 과거 출력의 감사 재현용입니다.")
    print("       근거: findings/xfip_causal_audit_reference.json")
    print("       최신 인과 재실험: 시장 대비 적중 -0.24%p·Brier 악화·승격 없음.\n")
    # ---- 시장
    mkt = load_market("KBO")
    print(f"시장(KBO 승패 2-way) {len(mkt):,}경기 "
          f"({mkt['date'].min().date()} ~ {mkt['date'].max().date()})")

    # ---- 모델 피처 (pitcher_xfip.py 와 동일 절차)
    df = load_full()
    tr_d = df[df["date"] < f"{TRAIN_END + 1}-01-01"]
    t = {"ip": 0.0, "er": 0.0, "hr": 0.0, "bb": 0.0, "kk": 0.0}
    from pitcher_er import _inn
    for r in tr_d.itertuples():
        for p in (r.home_sp, r.away_sp):
            t["ip"] += _inn(p.get("inn"))
            for k in ("er", "hr", "bb", "kk"):
                t[k] += float(p.get(k) or 0)
    fip_c = t["er"] / t["ip"] * 9 - (13 * t["hr"] + 3 * t["bb"] - 2 * t["kk"]) / t["ip"]
    lg_hr9 = t["hr"] / t["ip"] * 9
    pf = build(df, fip_c, lg_hr9)

    m = load_matches()
    fe = build_features(m)
    kbo = fe[(fe["league"] == "KBO") & (fe["outcome"] != 0.5)].copy()
    kbo["date"] = pd.to_datetime(kbo["date"])
    tmap = json.loads((PROC / "team_map.json").read_text(encoding="utf-8")).get("KBO", {})
    for c in ("home_team", "away_team"):
        kbo[c] = kbo[c].map(lambda x: tmap.get(x, x))
        mkt[c] = mkt[c].map(lambda x: tmap.get(x, x))

    d = (kbo.merge(pf, on=["date", "home_team", "away_team"], how="inner")
            .merge(mkt[["date", "home_team", "away_team", "p_mkt"]],
                   on=["date", "home_team", "away_team"], how="inner"))
    d = d.dropna(subset=["elo_diff", "xfip_diff"])
    tr, te = d[d["year"] <= TRAIN_END], d[d["year"] > TRAIN_END]
    print(f"시장·모델 결합 {len(d):,} · 학습 {len(tr):,} / 검증 {len(te):,}\n")
    if len(tr) < 200 or len(te) < 100:
        print("표본 부족 — 판정 불가")
        return 1

    def mk(x, cols):
        return np.column_stack([np.ones(len(x))]
                               + [x[c].to_numpy(float) for c in cols])

    y_tr = (tr["outcome"] == 1.0).to_numpy(float)
    b_elo = _fit(mk(tr, ["elo_diff"]), y_tr)
    b_xf = _fit(mk(tr, ["elo_diff", "xfip_diff"]), y_tr)

    def report(sub: pd.DataFrame, title: str) -> dict:
        y = (sub["outcome"] == 1.0).to_numpy(float)
        out = {
            "시장 (devig)": brier(sub["p_mkt"].to_numpy(float), y),
            "모델 Elo 단독": _brier(mk(sub, ["elo_diff"]), b_elo, y),
            "모델 Elo+xFIP": _brier(mk(sub, ["elo_diff", "xfip_diff"]), b_xf, y),
        }
        base = float(np.mean((y.mean() - y) ** 2))
        print(f"── {title} · n={len(sub):,} · 홈승률 {y.mean():.3f}")
        print(f"{'':22}{'Brier':>10}{'시장 대비':>12}")
        for k, v in out.items():
            gap = "" if k.startswith("시장") else f"{v - out['시장 (devig)']:+12.5f}"
            print(f"  {k:<20}{v:>10.5f}{gap}")
        print(f"  {'(기준선: 홈승률 고정)':<20}{base:>10.5f}")
        return out

    print("=" * 52)
    allr = report(te, "검증 전체")

    close = te[(te["p_mkt"] >= CLOSE_LO) & (te["p_mkt"] <= CLOSE_HI)]
    wide = te[(te["p_mkt"] < CLOSE_LO) | (te["p_mkt"] > CLOSE_HI)]
    print()
    if len(close) < 100:
        print(f"── 박빙 표본 부족 ({len(close)}건) — 판정 불가")
        return 0
    clr = report(close, f"박빙 (시장 {CLOSE_LO:.0%}~{CLOSE_HI:.0%})")
    print()
    wdr = report(wide, "박빙 아닌 구간")

    print("\n" + "=" * 52)
    print("[과거 누수 출력] 모델(Elo+xFIP) − 시장 · 음수여도 채택 근거 아님")
    for name, r in (("전체", allr), ("박빙", clr), ("그 외", wdr)):
        g = r["모델 Elo+xFIP"] - r["시장 (devig)"]
        print(f"   {name:<6}{g:+.5f}  {'← 모델 우위' if g < 0 else ''}")

    g_close = clr["모델 Elo+xFIP"] - clr["시장 (devig)"]
    g_wide = wdr["모델 Elo+xFIP"] - wdr["시장 (devig)"]

    # ---- 이 격차가 진짜인가 — 부트스트랩
    # 격차가 −0.0003 수준이면 표본을 조금만 흔들어도 부호가 뒤집힐 수 있다.
    # 경기별 Brier 차이를 짝지어(paired) 재표집해 신뢰구간을 낸다.
    print("\n" + "=" * 52)
    print("과거 누수 표본의 민감도 — 부트스트랩 10,000회 (인과 검증 아님)")
    rng = np.random.default_rng(42)
    for name, sub in (("전체", te), ("박빙", close), ("그 외", wide)):
        y = (sub["outcome"] == 1.0).to_numpy(float)
        pm = _predict(mk(sub, ["elo_diff", "xfip_diff"]), b_xf)
        diff = (pm - y) ** 2 - (sub["p_mkt"].to_numpy(float) - y) ** 2
        idx = rng.integers(0, len(diff), size=(10000, len(diff)))
        boot = diff[idx].mean(axis=1)
        lo, hi = np.percentile(boot, [2.5, 97.5])
        p_better = float((boot < 0).mean())
        verdict = ("과거 표본 내 모델 우위" if hi < 0 else
                   ("과거 표본 내 시장 우위" if lo > 0 else "판정 불가"))
        print(f"  {name:<6}{diff.mean():+.5f}  95% CI [{lo:+.5f}, {hi:+.5f}]"
              f"  모델 우위 확률 {p_better:.1%}  → {verdict}")

    # ---- 밴드 폭 민감도
    # ⚠️ 탐색적이다. 여러 폭을 훑어 제일 좋은 걸 고르면 다중비교로 우연이 섞인다.
    #    "45~55% 가 운 좋게 걸린 건 아닌가"를 눈으로 보는 용도일 뿐,
    #    여기서 고른 밴드를 채택 근거로 쓰면 안 된다.
    print("\n밴드 폭 민감도 (탐색용 — 채택 근거 아님)")
    print(f"  {'밴드':<14}{'n':>6}{'모델−시장':>12}")
    for half in (0.03, 0.05, 0.07, 0.10, 0.15):
        sub = te[(te["p_mkt"] >= .5 - half) & (te["p_mkt"] <= .5 + half)]
        if len(sub) < 80:
            continue
        y = (sub["outcome"] == 1.0).to_numpy(float)
        pm = _predict(mk(sub, ["elo_diff", "xfip_diff"]), b_xf)
        gap = brier(pm, y) - brier(sub["p_mkt"].to_numpy(float), y)
        print(f"  {f'{.5-half:.0%}~{.5+half:.0%}':<14}{len(sub):>6,}{gap:>+12.5f}")

    print()
    if g_close < 0 <= g_wide:
        print("[과거 출력] 점추정으로는 박빙에서만 모델이 앞선 것처럼 보였다.")
    elif g_close < g_wide:
        print("[과거 출력] 박빙에서 격차가 줄지만 시장이 앞섰다.")
    else:
        print("[과거 출력] 박빙에서도 시장 우위가 유지됐다.")
    print("[현재 판정] 시간누수 제거 후 적중 -0.24%p·Brier 악화. 자동 추천 승격 없음.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
