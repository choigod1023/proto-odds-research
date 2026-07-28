"""리그별 시장 효율성 — 프로토가 어느 리그를 가장 못 매기는가.

왜 이 축인가
------------
지금까지 아홉 번의 실험이 전부 같은 결론이었다: **공개 정보로는 시장을 못 이긴다.**
그런데 그 실험들은 전부 **"어떤 변수를 넣을까"** 를 물었다. 한 번도 안 물어본 게 있다.

    **어느 시장을 상대할 것인가.**

프로토 배당은 해외 북메이커를 참조해 만들어진다. 그렇다면 **해외 커버가 두꺼운
리그**(MLB·EPL)는 가격이 정교하고, **얇은 리그**(K리그2·V리그·KBL)는 프로토가
자체 산정에 의존해야 하므로 틀릴 여지가 크다.

같은 모델이라도 **상대가 약한 곳에서는 이긴다.** 그게 이 스캔의 가설이다.

⚠️ 다중비교 함정
----------------
리그 × 마켓 조합을 수십 개 돌리면 **우연히 하나는 좋아 보인다.**
그래서 세 가지를 같이 낸다.
  1. 부트스트랩 CI 와 우위확률
  2. 검정 개수를 명시하고 **Bonferroni 보정 임계**를 함께 표시
  3. **학습기간(≤2024)에서도 같은 방향인지** — 검증에서만 좋으면 우연이다

그리고 Brier 우위는 돈이 아니다. 통과한 조합만 **실제 ROI**까지 본다.
프로토 환급률 88% 를 넘겨야 의미가 있다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
TRAIN_END = 2024
MIN_N = 300              # 이보다 적으면 판정 불가로 둔다


def boot(model_loss: np.ndarray, proto_loss: np.ndarray,
         seed: int = 42, n: int = 4000):
    """짝지은 손실차(모델−프로토). 음수면 모델이 낫다."""
    rng = np.random.default_rng(seed)
    # pandas Series 를 그대로 넘기면 d[idx] 가 라벨 색인으로 가서 죽는다.
    d = np.asarray(model_loss, dtype=float) - np.asarray(proto_loss, dtype=float)
    idx = rng.integers(0, len(d), size=(n, len(d)))
    b = d[idx].mean(axis=1)
    lo, hi = np.percentile(b, [2.5, 97.5])
    return float(d.mean()), float(lo), float(hi), float((b < 0).mean())


def roi_flat(sub: pd.DataFrame, thresh: float = 0.0) -> tuple[float, int]:
    """모델이 시장보다 높게 본 선택지에 균등 베팅했을 때의 수익률."""
    bet = sub[(sub["p_model"] - sub["p_proto"]) > thresh]
    if bet.empty:
        return float("nan"), 0
    ret = np.where(bet["won"] > 0.5, bet["odds"] - 1.0, -1.0)
    return float(ret.mean()), len(bet)


def main() -> int:
    L = pd.read_csv(PROC / "market_scan.csv")
    if "league" not in L.columns:
        print("market_scan.csv 에 league 가 없다 → src/market_scan.py 먼저 실행")
        return 1

    tr = L[L["year"] <= TRAIN_END]
    te = L[L["year"] > TRAIN_END]
    print(f"선택지 {len(L):,} · 학습 {len(tr):,} · 검증 {len(te):,}")
    print(f"리그 {L['league'].nunique()}개 · 마켓 {L['market'].nunique()}종\n")

    def briers(sub):
        bm = float(np.mean((sub["p_model"] - sub["won"]) ** 2))
        bp = float(np.mean((sub["p_proto"] - sub["won"]) ** 2))
        return bm, bp

    # ---------------------------------------------------------- 리그 단위
    print("=" * 78)
    print("① 리그 전체 (마켓 합산)")
    print("=" * 78)
    print(f"{'리그':<10}{'n':>8}{'모델':>10}{'프로토':>10}{'차이':>10}"
          f"{'우위확률':>9}  학습기간")
    print("-" * 78)

    rows = []
    for lg, s in te.groupby("league"):
        if len(s) < MIN_N:
            continue
        bm, bp = briers(s)
        m, lo, hi, pb = boot((s["p_model"] - s["won"]) ** 2,
                             (s["p_proto"] - s["won"]) ** 2)
        s_tr = tr[tr["league"] == lg]
        tr_dir = "—"
        if len(s_tr) >= MIN_N:
            bm2, bp2 = briers(s_tr)
            tr_dir = f"{bm2 - bp2:+.4f}"
        rows.append({"league": lg, "n": len(s), "bm": bm, "bp": bp,
                     "diff": bm - bp, "pb": pb, "lo": lo, "hi": hi, "tr": tr_dir})

    rows.sort(key=lambda r: r["diff"])
    for r in rows:
        mark = " ⭐" if r["pb"] > 0.95 else ""
        print(f"{r['league']:<10}{r['n']:>8,}{r['bm']:>10.5f}{r['bp']:>10.5f}"
              f"{r['diff']:>+10.5f}{r['pb']:>8.1%}  {r['tr']}{mark}")

    # ------------------------------------------------------ 리그 × 마켓
    print()
    print("=" * 78)
    print("② 리그 × 마켓 — 모델이 이긴 조합만")
    print("=" * 78)

    cand, tested = [], 0
    for (lg, mk), s in te.groupby(["league", "market"]):
        if len(s) < MIN_N:
            continue
        tested += 1
        bm, bp = briers(s)
        if bm >= bp:
            continue
        m, lo, hi, pb = boot((s["p_model"] - s["won"]) ** 2,
                             (s["p_proto"] - s["won"]) ** 2)
        s_tr = tr[(tr["league"] == lg) & (tr["market"] == mk)]
        tr_diff = np.nan
        if len(s_tr) >= MIN_N:
            bm2, bp2 = briers(s_tr)
            tr_diff = bm2 - bp2
        roi, nb = roi_flat(s)
        cand.append({"league": lg, "market": mk, "n": len(s), "bm": bm, "bp": bp,
                     "diff": bm - bp, "pb": pb, "lo": lo, "hi": hi,
                     "tr_diff": tr_diff, "roi": roi, "n_bet": nb})

    alpha = 0.05 / max(tested, 1)
    print(f"검정한 조합 {tested}개 → Bonferroni 임계 우위확률 {1 - alpha:.3%}\n")
    cand.sort(key=lambda r: r["diff"])
    if not cand:
        print("  모델이 이긴 조합 없음")
    for r in cand:
        surv = "✅" if r["pb"] > 1 - alpha else ("△" if r["pb"] > 0.95 else "✗")
        tr_ok = ("같음" if r["tr_diff"] < 0 else "반대") if np.isfinite(r["tr_diff"]) else "표본부족"
        print(f"{surv} {r['league']:<8}{r['market']:<18}{r['n']:>7,}  "
              f"모델 {r['bm']:.5f} vs 프로토 {r['bp']:.5f}  차이 {r['diff']:+.5f}")
        print(f"     CI [{r['lo']:+.5f}, {r['hi']:+.5f}] · 우위확률 {r['pb']:.2%} · "
              f"학습기간 방향 {tr_ok} · 균등베팅 ROI {r['roi']:+.2%} (n={r['n_bet']:,})")

    print("\n" + "=" * 78)
    print("판정 기준: ✅ Bonferroni 통과 · △ 단일검정만 통과 · ✗ 미달")
    print("Brier 우위는 돈이 아니다. ROI 가 0 을 넘어야 실제로 이긴 것이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
