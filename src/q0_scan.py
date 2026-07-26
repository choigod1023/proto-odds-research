"""Q0 — 과거 데이터에 +EV 구간이 존재하는가.

핵심 아이디어
--------------
배당이 완벽히 정확하다면, **어느 선택지에 걸든 수익률은 정확히 같다.**

    적중확률 p_i = (1/o_i) / 오버라운드   (마진 균등 배분 가정)
    수익률   = p_i · o_i − 1 = 1/오버라운드 − 1

즉 이론 수익률은 booking 구조만으로 정해진다:

    2-way        1/1.1364 − 1 = −12.00%
    3-way        1/1.1494 − 1 = −13.00%
    3-way 핸디캡  1/1.1629 − 1 = −14.01%

**이 기준선에서 벗어나는 구간이 곧 시장이 틀린 지점이다.**
Q0은 그런 구간이 존재하는지, 존재한다면 통계적으로 유의한지를 스캔한다.

⚠️ 다중검정 주의: 구간을 많이 쪼갤수록 우연히 플러스가 나오는 조합이 생긴다.
   그래서 점추정이 아니라 **부트스트랩 신뢰구간**으로 판정하고,
   검정한 구간 수를 함께 보고한다.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import Bet, to_bets                       # noqa: E402
from wisetoto import CACHE, parse_rows              # noqa: E402

import gzip                                          # noqa: E402

SEED = 42
N_BOOT = 5000
MIN_N = 200          # 이보다 적은 구간은 판정하지 않는다 (표본 부족)

THEORETICAL = {"2-way": 1 / 1.1364 - 1, "3-way": 1 / 1.1494 - 1,
               "3-way-핸디캡": 1 / 1.1629 - 1}


# ------------------------------------------------------------ 적재

def load_all_bets() -> list[Bet]:
    bets: list[Bet] = []
    files = sorted(CACHE.glob("*/*.html.gz"))
    for p in files:
        year = int(p.parent.name)
        rnd = int(p.stem.replace(".html", ""))
        with gzip.open(p, "rt", encoding="utf-8") as f:
            html = f.read()
        bets.extend(to_bets(parse_rows(html, year, rnd)))
    print(f"캐시 {len(files)}개 회차 → 베팅 레코드 {len(bets):,}건")
    return bets


# ------------------------------------------------------------ 통계

def roi(bets: list[Bet]) -> float:
    return st.fmean(b.profit for b in bets) if bets else float("nan")


def bootstrap_ci(bets: list[Bet], n_boot: int = N_BOOT,
                 alpha: float = 0.05) -> tuple[float, float]:
    """게임 단위 클러스터 부트스트랩.

    같은 게임행의 선택지들은 '정확히 하나만 적중'하도록 묶여 있어 서로 독립이 아니다.
    베팅 단위로 재추출하면 신뢰구간이 실제보다 좁게 나오므로 게임 단위로 묶어 뽑는다.
    """
    clusters: dict[tuple, list[Bet]] = defaultdict(list)
    for b in bets:
        clusters[(b.year, b.round, b.game_no)].append(b)
    groups = list(clusters.values())
    if len(groups) < 30:
        return (float("nan"), float("nan"))

    rng = random.Random(SEED)
    k = len(groups)
    out = []
    for _ in range(n_boot):
        tot = cnt = 0.0
        for _ in range(k):
            for b in groups[rng.randrange(k)]:
                tot += b.profit
                cnt += 1
        out.append(tot / cnt if cnt else 0.0)
    out.sort()
    return (out[int(n_boot * alpha / 2)], out[int(n_boot * (1 - alpha / 2))])


# ------------------------------------------------------------ 스캔

def odds_bucket(o: float) -> str:
    for lo, hi in ((1.0, 1.5), (1.5, 1.8), (1.8, 2.2), (2.2, 3.0), (3.0, 5.0)):
        if lo <= o < hi:
            return f"{lo:.1f}–{hi:.1f}"
    return "5.0+"


def report_baseline(bets: list[Bet]) -> None:
    print("\n" + "=" * 78)
    print("1) 기준선 검증 — 매핑이 맞다면 실측 ROI가 이론값 근처여야 한다")
    print("=" * 78)
    print(f"{'booking 구조':<16}{'n':>9}{'이론 ROI':>11}{'실측 ROI':>11}{'차이':>10}  판정")
    by: dict[str, list[Bet]] = defaultdict(list)
    for b in bets:
        by[b.booking_class].append(b)
    for cls, v in sorted(by.items(), key=lambda x: -len(x[1])):
        th = THEORETICAL.get(cls, float("nan"))
        ac = roi(v)
        d = ac - th
        ok = abs(d) < 0.03
        print(f"{cls:<16}{len(v):>9,}{th:>10.2%}{ac:>11.2%}{d:>+10.2%}  "
              f"{'✅ 정상' if ok else '⚠️ 이탈'}")


def scan(bets: list[Bet], keyfn, title: str, min_n: int = MIN_N) -> list[tuple]:
    groups: dict[str, list[Bet]] = defaultdict(list)
    for b in bets:
        groups[keyfn(b)].append(b)

    rows = []
    for k, v in groups.items():
        if len(v) < min_n:
            continue
        th = THEORETICAL.get(v[0].booking_class, -0.12)
        # 같은 구간 안에 booking이 섞일 수 있으므로 가중 평균으로 기준선 계산
        th = st.fmean(THEORETICAL.get(b.booking_class, -0.12) for b in v)
        rows.append((k, len(v), roi(v), th, roi(v) - th))
    rows.sort(key=lambda x: -x[2])

    print("\n" + "-" * 78)
    print(f"■ {title}   (표본 {min_n}건 이상 구간만)")
    print("-" * 78)
    print(f"{'구간':<28}{'n':>8}{'실측 ROI':>11}{'기준선':>10}{'초과':>10}")
    for k, n, r, th, ex in rows:
        flag = "  ⭐" if r > 0 else ("  ↑" if ex > 0.03 else "")
        print(f"{str(k):<28}{n:>8,}{r:>11.2%}{th:>10.2%}{ex:>+10.2%}{flag}")
    return rows


def main() -> int:
    bets = load_all_bets()
    if not bets:
        print("캐시가 비어 있습니다. 먼저 python src/collect.py 를 실행하세요.")
        return 1

    report_baseline(bets)

    print("\n" + "=" * 78)
    print("2) 구간 스캔 — 기준선을 넘는 조합이 있는가")
    print("=" * 78)

    n_tests = 0
    n_tests += len(scan(bets, lambda b: odds_bucket(b.odds), "배당 구간별"))
    n_tests += len(scan(bets, lambda b: f"{b.sport}/{b.market_family}", "종목 × 상품"))
    n_tests += len(scan(bets, lambda b: f"{b.market_family}/{b.selection}", "상품 × 선택지"))
    n_tests += len(scan(bets, lambda b: b.league, "리그별", min_n=300))
    n_tests += len(scan(
        bets, lambda b: f"{b.market_family}/{b.selection}/{odds_bucket(b.odds)}",
        "상품 × 선택지 × 배당구간", min_n=300))

    # ---- 후보 구간에 부트스트랩 CI
    print("\n" + "=" * 78)
    print("3) 플러스 후보 구간의 부트스트랩 신뢰구간 (게임 단위 클러스터, 시드 42)")
    print("=" * 78)
    cand: dict[str, list[Bet]] = defaultdict(list)
    for b in bets:
        cand[f"{b.market_family}/{b.selection}/{odds_bucket(b.odds)}"].append(b)
    pos = [(k, v) for k, v in cand.items() if len(v) >= 300 and roi(v) > 0]
    if not pos:
        print("  ROI가 플러스인 구간이 없습니다.")
    for k, v in sorted(pos, key=lambda x: -roi(x[1]))[:10]:
        lo, hi = bootstrap_ci(v)
        verdict = "✅ 유의(하한>0)" if lo > 0 else "❌ 0을 포함 → 우연과 구분 안 됨"
        print(f"  {k:<40} n={len(v):>6,}  ROI={roi(v):+.2%}  "
              f"95%CI=[{lo:+.2%}, {hi:+.2%}]  {verdict}")

    print(f"\n※ 검정한 구간 수: 약 {n_tests}개. 다중검정 때문에 우연히 플러스가 나오는 "
          f"구간이 생길 수 있으므로, 신뢰구간 하한이 0을 넘는 것만 후보로 본다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
