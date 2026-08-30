"""조합 설계표 — 배당을 올리려면 조합해야 하고, 다리마다 마진이 한 번씩 물린다.

왜 이 파일이 따로 있나
----------------------
`loss_filter.py` 는 **선택지 1개** 기준으로 등급을 매긴다. 그런데 배당을 올리려면
조합해야 하고, **조합하면 다리마다 마진이 한 번씩 물린다** — 같은 선택으로도
1폴 −9.8% 가 2폴에서는 −18.7% 가 된다.

단폴(한경기구매)도 있지만 **'한경기' 로 지정된 경기만** 살 수 있어 아무 경기나
단폴로 갈 수는 없다. 그래서 실전에서는 조합이 기본이다.

규정에서 온 제약 (sportstoto.co.kr/proto_rules.php)
  · **한경기구매(단폴)**: '한경기' 로 지정된 경기만. 단위투표금액 1,000원
  · **조합구매**: 2~10경기. 단위투표금액 100원
  · **같은 경기의 승패·핸디캡·언더오버는 한 장에 못 담는다**
    → 모든 다리는 서로 다른 경기 → 결과가 독립 → 기대값은 그냥 곱셈이다
  · 회차당 1인 10만원 · 투표권당 적중금 상한 1억원

핵심 결과 세 가지
-----------------
1. **다리 하나 추가 = 약 −6%p.** 목표 배당 3배 기준 2폴 −22.8% · 3폴 −29.3% ·
   4폴 −35.6% · 5폴 −41.3%. 목표 배당은 **다리 수가 아니라 다리당 배당**으로 맞춘다.
2. **'저배당 우선' 규칙이 조합에서는 뒤집힌다.** 배당 1.0–1.3 은 다리 하나로 보면
   ROI 가 가장 좋지만(−9.8%), 배당을 만드는 효율은 가장 나쁘다.
   목표 배당을 만들려면 다리를 여러 개 붙여야 하고 다리마다 마진이 물리기 때문이다.
   → **최소 손실이 목표일 때만** 저배당이 맞다.
3. **기대값은 어떤 구조에서도 음수다.** 딸 확률은 오직 분산에서 나오고,
   티켓을 많이 살수록 0 으로 수렴한다(대수의 법칙이 마진을 실현시킨다).

사용:
    python3 src/combo.py            # docs/data/combo.json 생성
    python3 src/combo.py --selftest
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from math import comb
from pathlib import Path

import pandas as pd
from runtime_db import persist_artifact

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "processed" / "bets.csv"
OUT = ROOT / "docs" / "data" / "combo.json"

BINS = [1.0, 1.3, 1.5, 1.8, 2.2, 3.0, 5.0, 99]
LABELS = ["1.0-1.3", "1.3-1.5", "1.5-1.8", "1.8-2.2", "2.2-3.0", "3.0-5.0", "5.0+"]
MIN_N = 500
MIN_LEGS, MAX_LEGS = 2, 10          # 규정
TARGETS = [1.4, 2, 3, 5, 8, 12, 20, 50]


def legs() -> list[dict]:
    """배당대별 다리 하나의 실측. 2-way 만 쓴다(환급률이 가장 좋다)."""
    b = pd.read_csv(SRC)
    b = b[(b["booking_class"] == "2-way") & (b["odds"] > 1.0)]
    b["bin"] = pd.cut(b["odds"], BINS, labels=LABELS)
    out = []
    for lab, g in b.groupby("bin", observed=True):
        if len(g) < MIN_N:
            continue
        h = float(g["won"].mean())
        roi = float(g["profit"].mean())
        if h <= 0:
            continue
        mult = 1 + roi                      # 조합의 곱셈 인자
        o = mult / h                        # 기대값과 정합적인 유효배당
        out.append({
            "bin": str(lab), "n": int(len(g)), "hit": round(h, 4),
            "roi": round(roi, 4), "mult": round(mult, 4), "odds": round(o, 3),
            # 배당 로그 1단위를 사는 데 잃는 로그EV. 낮을수록 효율적이다.
            "cost_per_payout": round(-math.log(mult) / math.log(o), 4),
        })
    return sorted(out, key=lambda r: r["odds"])


def best_combo(L: list[dict], target: float, k: int) -> dict | None:
    """다리 k 개로 목표 배당을 만들 때 기대값이 가장 덜 나쁜 구성."""
    best = None
    for c in itertools.combinations_with_replacement(range(len(L)), k):
        o = math.prod(L[i]["odds"] for i in c)
        if not (target * 0.88 <= o <= target * 1.18):
            continue
        m = math.prod(L[i]["mult"] for i in c)
        if best is None or m > best["_m"]:
            best = {"legs": k, "bins": [L[i]["bin"] for i in c],
                    "odds": round(o, 2),
                    "hit": round(math.prod(L[i]["hit"] for i in c), 5),
                    "roi": round(m - 1, 4), "_m": m}
    if best:
        best.pop("_m")
    return best


def p_profit(hit: float, odds: float, n: int) -> float:
    """티켓 n 장을 같은 구조로 살 때 '원금보다 많이 들고 나올' 확률."""
    need = int(n / odds) + 1
    if need > n:
        return 0.0
    return sum(comb(n, k) * hit**k * (1 - hit)**(n - k) for k in range(need, n + 1))


def build() -> dict:
    L = legs()
    plans = []
    for t in TARGETS:
        cands = [c for k in range(MIN_LEGS, 7) if (c := best_combo(L, t, k))]
        if not cands:
            continue
        best = max(cands, key=lambda c: c["roi"])
        plans.append({
            "target": t, "best": best,
            "by_legs": {str(c["legs"]): c["roi"] for c in cands},
            # 다리를 하나 더 붙이면 평균 얼마나 손해인가
            "cost_per_extra_leg": round(
                (min(c["roi"] for c in cands) - best["roi"]) / max(1, len(cands) - 1), 4),
        })

    b = pd.read_csv(SRC)
    b = b[b["odds"] > 1.0]
    any_roi = float(b["profit"].mean())
    tw = b[b["booking_class"] == "2-way"]
    lo = float(tw[tw["odds"] <= 1.3]["profit"].mean())
    baseline = []
    for k in (1, 2, 3):
        baseline.append({
            "legs": k,
            # 1폴은 '한경기' 로 지정된 경기만 가능하다 (아무 경기나 되는 게 아니다)
            "buyable": True, "restricted": k < MIN_LEGS,
            "any": round((1 + any_roi)**k - 1, 4),
            "best": round((1 + lo)**k - 1, 4),
            "saving": round((1 + lo)**k - (1 + any_roi)**k, 4),
        })

    variance = []
    for c in (p for p in plans if p["best"]["legs"] == 2):
        bb = c["best"]
        variance.append({
            "label": " × ".join(bb["bins"]), "target": c["target"],
            "odds": bb["odds"], "hit": bb["hit"], "roi": bb["roi"],
            "p_profit": {str(n): round(p_profit(bb["hit"], bb["odds"], n), 4)
                         for n in (5, 10, 30, 100, 300)},
        })

    return {
        "generated_at": pd.Timestamp.now("UTC").isoformat(timespec="seconds"),
        "rules_source": "https://www.sportstoto.co.kr/proto_rules.php",
        "constraints": {"min_legs": MIN_LEGS, "max_legs": MAX_LEGS,
                        "same_game_multi_market": False,
                        "per_round_limit_krw": 100_000,
                        "max_payout_krw": 100_000_000},
        "legs": L, "baseline": baseline, "plans": plans, "variance": variance,
        "note": "기대값은 어떤 구조에서도 음수다. 딸 확률은 분산에서만 나오고, "
                "티켓을 많이 살수록 0 으로 수렴한다. 이건 이기는 도구가 아니다.",
    }


def _selftest() -> int:
    """조합 산술이 규정·수학과 어긋나지 않는가."""
    L = legs()
    bad = []
    print("조합표 자기검사")
    print(f"  다리 {len(L)}개 구간")
    for r in L:
        if not (0 < r["hit"] < 1):
            bad.append(f"{r['bin']} 적중률 {r['hit']} 범위 밖")
        if r["mult"] >= 1:
            bad.append(f"{r['bin']} mult {r['mult']} ≥ 1 — +EV 구간은 존재하지 않아야 한다")
        if abs(r["hit"] * r["odds"] - r["mult"]) > 2e-3:
            bad.append(f"{r['bin']} 적중률×배당 ≠ mult")
    print("  ✅ 다리 성질 (0<적중률<1 · mult<1 · 적중률×배당=mult)")

    d = build()

    # 다리를 늘리면 나빠져야 한다 — 마진이 한 번 더 물리니까.
    # 예외: 2폴로 목표를 맞추려다 5.0+ 를 써야 하는 경우엔 3폴이 낫다.
    n_exc = 0
    for p in d["plans"]:
        ks = sorted(p["by_legs"], key=int)
        rois = [p["by_legs"][k] for k in ks]
        if any(b > a + 1e-9 for a, b in zip(rois, rois[1:])):
            if "5.0+" in best_combo(L, p["target"], int(ks[0]))["bins"]:
                n_exc += 1
            else:
                bad.append(f"목표 {p['target']}× : 다리를 늘렸는데 좋아진다 {p['by_legs']}")
    print(f"  ✅ 목표 {len(d['plans'])}개 — 다리 추가는 손해 (5.0+ 회피 예외 {n_exc}건)")

    one = [x for x in d["baseline"] if x["legs"] == 1][0]
    if not one["restricted"]:
        bad.append("단폴이 '지정 경기 한정' 으로 표시되지 않는다")
    print("  ✅ 단폴 = 지정 경기 한정 표시")

    for v in d["variance"]:
        if v["p_profit"]["300"] > v["p_profit"]["10"]:
            bad.append(f"{v['label']}: 300장이 10장보다 딸 확률이 높다")
    print("  ✅ 티켓이 늘수록 원금초과 확률 감소 (대수의 법칙)")

    if bad:
        print("\n🔴 " + "\n🔴 ".join(bad))
        return 1
    print("\n✅ 조합표 자기검사 통과")
    return 0


def main() -> int:
    d = build()
    persist_artifact("combo", d, OUT)

    print("배당을 올리려면 조합해야 하고, 다리마다 마진이 한 번씩 물린다")
    for x in d["baseline"]:
        tag = "   ← '한경기' 지정 경기만" if x["restricted"] else ""
        print(f"  {x['legs']}폴  아무거나 {x['any']*100:+7.2f}%   "
              f"최적 {x['best']*100:+7.2f}%   절약 {x['saving']*100:5.2f}%p{tag}")
    print("\n목표 배당별 최적 조합")
    for p in d["plans"]:
        bb = p["best"]
        print(f"  {p['target']:>4}× → {bb['legs']}폴 {' + '.join(bb['bins']):<26}"
              f" 실배당 {bb['odds']:>5.2f}×  적중 {bb['hit']*100:>5.2f}%  "
              f"ROI {bb['roi']*100:>6.1f}%")
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
