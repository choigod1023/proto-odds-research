"""Q4a — 어느 devig 이 실측에 맞나. 실제 배당 조합으로 겨룬다.

배경
----
사이트의 `p_mkt = (1/o)/ov` 는 multiplicative devig 다 — 마진이 모든 선택지에
같은 비율로 얹혀 있다는 가정. 그런데 이 프로젝트의 실측(236,637 선택지)은
마진이 배당을 따라 단조 증가한다고 말한다:

    1.0-1.3  마진  8.7%   …   3.0-5.0  14.9%   …   5.0+  35.9%

균등(환급률 88% → 12%)이 아니다. 그래서 multiplicative 는 역배 확률을 부풀린다.

무엇을 하나
-----------
정답은 실측이 준다. 배당대 b 의 실제 ROI 가 r 이면, 그 배당의 참 확률은

    p_true = (1 + r) / odds          (ROI = p·odds − 1 의 역)

실제 발매된 배당 조합(picks_v2.json)에 devig 4종을 각각 걸어, 이 p_true 와
얼마나 어긋나는지 잰다. 가장 덜 어긋나는 방식이 답이다.

    python src/devig_pick.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import additive, multiplicative, power, shin       # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GRADES = ROOT / "docs" / "data" / "loss_grades.json"
PICKS = ROOT / "docs" / "data" / "picks_v2.json"

METHODS = {
    "multiplicative(현재)": multiplicative,
    "additive": additive,
    "power": power,
    "shin": shin,
}


def true_prob_fn():
    """배당 → 실측 기반 참 확률. loss_grades 의 배당대별 ROI 를 쓴다."""
    bins = []
    for b in json.loads(GRADES.read_text(encoding="utf-8"))["odds_bins"]:
        lo, hi = b["bin"].replace("+", "-999").split("-")
        bins.append((float(lo), float(hi), b["roi"], b["n"]))

    def f(o: float):
        for lo, hi, roi, _ in bins:
            if lo <= o < hi:
                return (1.0 + roi) / o
        return None
    return f, bins


def markets() -> list[list[float]]:
    """실제 발매된 배당 조합. 같은 게임번호 = 한 마켓."""
    d = json.loads(PICKS.read_text(encoding="utf-8"))
    games = (d.get("live") or []) + (d.get("past") or [])
    by: dict[tuple, list[tuple[int, float]]] = {}
    for g in games:
        for i, o in enumerate(g.get("options") or []):
            v = o.get("배당")
            if not v or v <= 1.0:
                continue
            by.setdefault((g["round"], o.get("게임번호")), []).append((i, v))
    out = []
    for _, opts in by.items():
        odds = [v for _, v in sorted(opts)]
        # 2-way·3-way 만. 선택지가 하나면 devig 이 성립하지 않는다.
        if 2 <= len(odds) <= 3:
            out.append(odds)
    return out


def main() -> int:
    tp, bins = true_prob_fn()
    ms = markets()
    if not ms:
        print("배당 조합을 못 찾았다")
        return 1

    print("실측 기준선 (loss_grades · 236,637 선택지)")
    print(f"  {'배당대':<10}{'ROI':>9}{'마진':>8}{'n':>9}")
    for lo, hi, roi, n in bins:
        lab = f"{lo}-{hi if hi < 900 else '+'}"
        print(f"  {lab:<10}{roi*100:>8.2f}%{-roi*100:>7.1f}%{n:>9,}")

    print(f"\n실제 발매 마켓 {len(ms):,}개 · 선택지 {sum(len(m) for m in ms):,}개")
    print("\n방식별 — 복원확률이 실측 참확률과 얼마나 어긋나나")
    print(f"  {'방식':<22}{'평균절대오차':>12}{'역배(3.0+) 편향':>18}")
    print("  " + "-" * 52)

    results = {}
    for name, fn in METHODS.items():
        err_all, err_long, n_long = [], [], 0
        for odds in ms:
            try:
                ps = fn(odds)
            except Exception:                                  # noqa: BLE001
                continue
            for o, p in zip(odds, ps):
                t = tp(o)
                if t is None:
                    continue
                err_all.append(abs(p - t))
                if o >= 3.0:
                    err_long.append(p - t)          # 부호 유지 — 과대/과소를 본다
                    n_long += 1
        if not err_all:
            continue
        mae = sum(err_all) / len(err_all)
        bias = sum(err_long) / len(err_long) if err_long else 0.0
        results[name] = mae
        print(f"  {name:<22}{mae*100:>11.3f}%p{bias*100:>+16.3f}%p")

    best = min(results, key=results.get)
    cur = results.get("multiplicative(현재)")
    print(f"\n→ 가장 잘 맞는 방식: **{best}** (평균절대오차 {results[best]*100:.3f}%p)")
    if cur and best != "multiplicative(현재)":
        gain = (cur - results[best]) / cur * 100
        print(f"   현재(multiplicative) 대비 오차 {gain:.1f}% 감소")
    print(f"\n※ 역배 편향이 +면 그 방식이 역배 확률을 부풀린다는 뜻이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
