"""파서 검증 (설계서 §2 체크리스트 P1~P6).

이 검증을 통과하지 못하면 이후 모든 수치가 무효다.
특히 P6: 초판 §1이 보고한 2025년 99회차 오버라운드가 재현되는가.
"""
from __future__ import annotations

import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wisetoto import SPORT_NAMES, fetch_round, parse_rows  # noqa: E402

# 초판 §1 보고값 (2025년 99회차). 초판의 '유형' 라벨은 부정확했으나
# 게임 수와 오버라운드 값 자체는 재현 대상이다.
#   초판 라벨                      → 실제 구성
#   2-way 승패        n=86        → 승패(2-way)
#   언더오버          n=182       → 언더오버 전부(전반 포함)
#   핸디캡(2-way)     n=42        → 2-way 핸디캡 전부
#   3-way 승무패      n=204       → 승무패 112 + 승①패 77 + 전반승무패 15
#   핸디캡 승①패      n=112       → 실제로는 '3-way 핸디캡' (승①패 아님)
BASELINE_BOOKING = {
    "2-way":        1.1362,
    "3-way":        1.1494,
    "3-way-핸디캡":  1.1629,
}
BASELINE_COUNTS = {"2-way": 86 + 182 + 42, "3-way": 204, "3-way-핸디캡": 112}


def main(year: int = 2025, rnd: int = 99) -> int:
    html = fetch_round(year, rnd)
    if html is None:
        print("회차 데이터를 가져오지 못했습니다.")
        return 1
    rows = parse_rows(html, year, rnd)

    void = [r for r in rows if r.is_void]
    live = [r for r in rows if not r.is_void]
    print(f"=== {year}년 {rnd}회차 ===")
    print(f"게임행 {len(rows)}건 → 무효/취소 {len(void)}건 제외 → 분석 대상 {len(live)}건")
    print("  (초판이 보고한 '626개 게임행'과 대조)")

    bad_n = [r for r in live if r.n_way not in (2, 3)]
    print(f"\n[P1] 배당 개수 2 또는 3      : {'통과' if not bad_n else f'실패 {len(bad_n)}건'}")
    bad_ov = [r for r in live if r.overround is None or not (1.0 <= r.overround <= 1.40)]
    print(f"[P3] 오버라운드 범위 [1.0,1.4]: {'통과' if not bad_ov else f'실패 {len(bad_ov)}건'}")

    by_sport: dict[str, int] = defaultdict(int)
    for r in live:
        by_sport[r.sport] += 1
    print("\n=== 종목 분포 ===")
    for k, v in sorted(by_sport.items(), key=lambda x: -x[1]):
        print(f"   {SPORT_NAMES.get(k, k or '미상'):4s} {v:4d}")

    # ---- booking_class 별 집계: 이게 진짜 축이다
    agg: dict[str, list[float]] = defaultdict(list)
    for r in live:
        if r.overround is not None:
            agg[r.booking_class].append(r.overround)

    print("\n=== [P6] booking 축별 집계 — 초판 §1 재현 ===")
    print(f"{'booking 구조':<14}{'n(측정)':>8}{'n(초판)':>8}"
          f"{'ov(측정)':>11}{'ov(초판)':>11}{'환급률':>9}{'SD':>10}  판정")
    ok = True
    for cls in ("2-way", "3-way", "3-way-핸디캡"):
        vals = agg.get(cls, [])
        if not vals:
            continue
        n, mean = len(vals), st.fmean(vals)
        sd = st.pstdev(vals) if n > 1 else 0.0
        bov, bn = BASELINE_BOOKING[cls], BASELINE_COUNTS[cls]
        match = abs(mean - bov) < 0.0015 and n == bn
        ok &= match
        print(f"{cls:<14}{n:>8}{bn:>8}{mean:>11.4f}{bov:>11.4f}"
              f"{100/mean:>8.2f}%{sd:>10.4f}  {'✅' if match else '❌'}")

    print(f"\n[P6] 종합: {'✅ 초판 수치 재현됨 (라벨만 정정)' if ok else '❌ 재현 실패'}")

    # ---- 상품(family) × 구조 교차표: booking이 상품과 무관함을 보인다
    cross: dict[tuple, list[float]] = defaultdict(list)
    for r in live:
        if r.overround is not None:
            cross[(r.market_type, r.booking_class)].append(r.overround)
    print("\n=== 상품별 상세 (booking은 구조로만 결정됨을 확인) ===")
    print(f"{'상품':<22}{'booking축':<14}{'n':>5}{'오버라운드':>12}{'환급률':>9}{'SD':>9}")
    for (mt, bc), v in sorted(cross.items(), key=lambda x: -len(x[1])):
        m = st.fmean(v)
        sd = st.pstdev(v) if len(v) > 1 else 0.0
        print(f"{mt:<22}{bc:<14}{len(v):>5}{m:>12.4f}{100/m:>8.2f}%{sd:>9.4f}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
