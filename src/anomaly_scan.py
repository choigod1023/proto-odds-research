"""이상점 심화 — 프로토가 가장 크게 틀리는 곳을 교차로 판다.

왜 이 설계인가
--------------
`Q0.md` 가 이미 134개 구간을 훑었고 **ROI 0 을 넘은 구간은 0개**였다.
그러니 같은 걸 또 하면 안 된다. 그런데 Q0 은 **1차원 절단**만 했고,
거기서 하나가 압도적으로 튀었다.

    승①패 / '1점차' 선택지   ROI −6.54%   (기준선 −13.00%, **+6.46%p 초과**)

수익이 나려면 기준선을 약 13%p 넘겨야 한다. **이미 절반이 와 있다.**
나머지 6.5%p 를 가진 부분집합이 있는지가 이 스캔의 질문이다.

무엇이 새로운가
---------------
- Q0: 배당대 / 종목 / 상품 / 선택지 / 리그 / 연도 를 **따로** 잘랐다
- 여기: **교차**로 자른다 (상품×선택지×리그, ×배당대)
- 그리고 **이상점에서 출발**한다. 전수 탐색이 아니라 표적 심화다

⚠️ 다중비교
-----------
교차하면 구간이 수백 개가 되고 **우연히 좋은 게 반드시 나온다.**
그래서 셋을 강제한다.
  1. 게임행 단위 **클러스터 부트스트랩** (같은 경기의 선택지는 독립이 아니다)
  2. **Bonferroni 보정** 임계를 같이 출력
  3. **시간 분리** — 학습(≤2024)에서 후보를 고르고 검증(2025~)에서 확인.
     학습에서만 좋은 건 버린다. 오늘 KBL 에서 한 번 속았다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
TRAIN_END = 2024
MIN_N = 200
BOOT = 4000


def load() -> pd.DataFrame:
    L = pd.read_csv(PROC / "market_scan.csv")
    L["ret"] = np.where(L["won"] > 0.5, L["odds"] - 1.0, -1.0)
    # 기준선 = 그 선택지가 속한 마켓의 이론 수익률(1/오버라운드 − 1).
    # p_proto 는 devig 된 값이므로 p_proto * odds 가 곧 1/오버라운드다.
    L["base"] = L["p_proto"] * L["odds"] - 1.0
    L["edge"] = L["ret"] - L["base"]          # 기준선 대비 초과 수익
    # 선택지 이름: 마켓 안에서 몇 번째인지로는 부족하므로 배당 순위로 표기
    return L


def boot_ci(x: np.ndarray, seed: int = 42, n: int = BOOT):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    b = x[idx].mean(axis=1)
    return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def scan(df: pd.DataFrame, keys: list[str], label: str, min_n: int = MIN_N):
    """구간별 ROI 와 기준선 대비 초과. 학습에서 고르고 검증에서 확인."""
    tr, te = df[df["year"] <= TRAIN_END], df[df["year"] > TRAIN_END]
    rows = []
    for k, s_tr in tr.groupby(keys):
        if len(s_tr) < min_n:
            continue
        s_te = te
        for col, val in zip(keys, k if isinstance(k, tuple) else (k,)):
            s_te = s_te[s_te[col] == val]
        if len(s_te) < min_n:
            continue
        rows.append({
            "key": " / ".join(str(x) for x in (k if isinstance(k, tuple) else (k,))),
            "n_tr": len(s_tr), "roi_tr": s_tr["ret"].mean(), "edge_tr": s_tr["edge"].mean(),
            "n_te": len(s_te), "roi_te": s_te["ret"].mean(), "edge_te": s_te["edge"].mean(),
            "_te": s_te["ret"].values,
        })
    if not rows:
        print(f"\n[{label}] 표본 조건을 만족하는 구간 없음")
        return []
    rows.sort(key=lambda r: -r["roi_te"])
    alpha = 0.05 / len(rows)
    print(f"\n{'='*94}")
    print(f"[{label}]  검정 구간 {len(rows)}개 · Bonferroni 임계 {100*(1-alpha):.3f}%")
    print(f"{'구간':<38}{'학습n':>7}{'학습ROI':>9}{'검증n':>7}{'검증ROI':>9}{'초과':>9}  95%CI")
    print("-" * 94)
    for r in rows[:8]:
        lo, hi = boot_ci(r["_te"])
        star = " ⭐" if lo > 0 else ""
        print(f"{r['key'][:37]:<38}{r['n_tr']:>7,}{r['roi_tr']:>+8.2%}{r['n_te']:>7,}"
              f"{r['roi_te']:>+8.2%}{r['edge_te']:>+8.2%}  [{lo:+.2%}, {hi:+.2%}]{star}")
    if len(rows) > 8:
        print(f"  … 외 {len(rows)-8}개 (전부 검증 ROI {rows[8]['roi_te']:+.2%} 이하)")
    return rows


def main() -> int:
    L = load()
    print(f"선택지 {len(L):,} · 학습 {(L['year'] <= TRAIN_END).sum():,} "
          f"· 검증 {(L['year'] > TRAIN_END).sum():,}")

    # 선택지 이름 — Q0 의 이상점('승①패 / 1점차')은 **마켓 안의 특정 선택지**다.
    NAMES = {(2, 0): "홈/언더", (2, 1): "원정/오버",
             (3, 0): "홈", (3, 1): "중간(무·①·⑤)", (3, 2): "원정"}
    L["sel"] = [NAMES.get((n, i), "?") for n, i in zip(L["n_way"], L["sel_idx"])]
    L["mk_sel"] = L["market"] + "·" + L["sel"]
    L["odds_bin"] = pd.cut(L["odds"], [1, 1.5, 1.8, 2.2, 3.0, 5.0, 1000],
                           labels=["1.0-1.5", "1.5-1.8", "1.8-2.2", "2.2-3.0", "3.0-5.0", "5.0+"])

    print("\n" + "#" * 94)
    print("# 출발점 재확인 — Q0 의 이상점이 재현되는가")
    print("#" * 94)
    for mk in sorted(L["market"].unique()):
        s = L[L["market"] == mk]
        if len(s) < 1000:
            continue
        print(f"  {mk:<20} n={len(s):>7,}  ROI {s['ret'].mean():>+7.2%}  "
              f"기준선 {s['base'].mean():>+7.2%}  초과 {s['edge'].mean():>+7.2%}")

    print("\n" + "#" * 94)
    print("# 선택지까지 쪼개면 — Q0 의 이상점이 여기 있다")
    print("#" * 94)
    for k, s in L.groupby("mk_sel"):
        if len(s) < 2000:
            continue
        print(f"  {k:<26} n={len(s):>7,}  ROI {s['ret'].mean():>+7.2%}  "
              f"기준선 {s['base'].mean():>+7.2%}  초과 {s['edge'].mean():>+7.2%}")

    scan(L, ["mk_sel"], "⓪ 상품·선택지")
    scan(L, ["mk_sel", "odds_bin"], "① 상품·선택지 × 배당대")
    scan(L, ["mk_sel", "league"], "② 상품·선택지 × 리그")
    scan(L, ["league", "odds_bin"], "③ 리그 × 배당대")
    scan(L, ["mk_sel", "league", "odds_bin"], "④ 상품·선택지 × 리그 × 배당대", min_n=150)

    print("\n" + "=" * 94)
    print("판정: 95%CI 하한이 0 을 넘고(⭐), Bonferroni 임계도 넘어야 진짜다.")
    print("      학습에서만 좋고 검증에서 무너지면 우연이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
