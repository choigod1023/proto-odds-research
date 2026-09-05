"""과거 회차 일괄 수집 (설계서 S2).

한 번 수집해 운영 DB(개발 환경은 gzip)에 넣으면 분석에서 다시 사용할 수 있다.
멱등: 캐시가 있는 회차는 요청하지 않으므로 중단 후 재실행해도 안전하다.

사용:
    python src/collect.py 2023 2024 2025
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wisetoto import archive_cached, _session, fetch_round, parse_rows  # noqa: E402

MAX_ROUND = 175          # 상한 (프로토는 연 150회차 내외)
MISS_STREAK_STOP = 8     # 연속 실패가 이만큼이면 그 해는 끝난 것으로 간주
ROUND_INTERVAL = 2.5     # 회차 간 추가 대기(초)


def collect_year(year: int) -> tuple[int, int]:
    sess = _session()
    got = cached = miss = 0
    streak = 0

    for rnd in range(1, MAX_ROUND + 1):
        if archive_cached(year, rnd):
            cached += 1
            streak = 0
            continue
        try:
            html = fetch_round(year, rnd, sess)
        except Exception as e:                      # noqa: BLE001
            print(f"  [{year}-{rnd:03d}] 오류 {type(e).__name__}: {e}", flush=True)
            html = None
            time.sleep(5)

        if html is None:
            miss += 1
            streak += 1
            if streak >= MISS_STREAK_STOP:
                print(f"  [{year}] {rnd}회차부터 연속 {streak}회 실패 → 종료", flush=True)
                break
        else:
            n = len(parse_rows(html, year, rnd))
            got += 1
            streak = 0
            print(f"  [{year}-{rnd:03d}] 게임행 {n:4d}건 수집", flush=True)
        time.sleep(ROUND_INTERVAL)

    print(f"[{year}] 신규 {got} · 캐시재사용 {cached} · 실패 {miss}", flush=True)
    return got, cached


def main(argv: list[str]) -> int:
    years = [int(a) for a in argv[1:]] or [2023, 2024, 2025]
    print(f"수집 대상 연도: {years}", flush=True)
    t0 = time.time()
    for y in years:
        print(f"\n=== {y}년 ===", flush=True)
        collect_year(y)
    print(f"\n총 소요 {time.time()-t0:.0f}초", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
