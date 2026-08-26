"""과거 회차 일괄 수집 (설계서 S2).

한 번 긁어 gzip 캐시에 넣어두면 Q0·Q1·Q4·Q5가 전부 이 캐시를 재파싱해서 돌아간다.
정산 완료 회차만 불변 캐시로 취급하고, 경기전·진행중 결과가 남은 회차는 다시 받는다.

사용:
    python src/collect.py                  # 2023~KST 현재연도
    python src/collect.py 2025 2026        # 명시한 연도만
"""
from __future__ import annotations

import sys
import time
import gzip
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wisetoto import _cache_path, _session, fetch_round, parse_rows  # noqa: E402

MAX_ROUND = 175          # 상한 (프로토는 연 150회차 내외)
MISS_STREAK_STOP = 8     # 연속 실패가 이만큼이면 그 해는 끝난 것으로 간주
ROUND_INTERVAL = 2.5     # 회차 간 추가 대기(초)
FINAL_RESULTS = {"홈승", "홈패", "무승부", "핸디승", "핸디패", "핸디무",
                 "오버", "언더", "홀", "짝", "①", "⑤", "홈", "원정", "무득",
                 "취소", "연기", "중단", "무효"}
IN_PLAY_RESULT = re.compile(r"^\d+회[초말]$")
KST = ZoneInfo("Asia/Seoul")


def default_years(now: datetime | None = None) -> list[int]:
    current = (now or datetime.now(KST))
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    else:
        current = current.astimezone(KST)
    return list(range(2023, current.year + 1))


def round_settlement(html: str, year: int, rnd: int) -> tuple[bool, int]:
    rows = parse_rows(html, year, rnd)
    if not rows:
        return False, 0
    # ``is_void``에는 취소뿐 아니라 배당 1.00 잠금행도 포함된다. 결과가 아직
    # ``경기전``인 잠금행을 정산으로 오인하지 말고 결과 문자열만으로 확정한다.
    complete = all(row.result in FINAL_RESULTS and not IN_PLAY_RESULT.match(row.result)
                   for row in rows)
    return complete, len(rows)


def _write_cache_meta(path: Path, *, complete: bool, row_count: int,
                      collected_at: str | None = None) -> None:
    """캐시 메타를 쓴다. 기존 파일을 읽은 시각을 수집 시각으로 위장하지 않는다."""
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    previous = {}
    if meta_path.exists():
        try:
            previous = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    observed = collected_at or previous.get("collected_at")
    timing_status = ("network_fetch_recorded" if collected_at else
                     previous.get("timing_status", "legacy_collection_time_unknown"))
    meta = {"collected_at": observed, "timing_status": timing_status,
            "metadata_updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "settlement_complete": complete, "row_count": row_count}
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_year(year: int) -> tuple[int, int]:
    sess = _session()
    got = cached = miss = 0
    streak = 0

    for rnd in range(1, MAX_ROUND + 1):
        cache_path = _cache_path(year, rnd)
        if cache_path.exists():
            try:
                with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
                    settled, row_count = round_settlement(handle.read(), year, rnd)
            except (OSError, EOFError):
                settled, row_count = False, 0
            if settled:
                _write_cache_meta(cache_path, complete=True, row_count=row_count)
                cached += 1
                streak = 0
                continue
        try:
            # 미정산 캐시는 서버의 최신 정산 상태로 명시적으로 교체한다.
            html = fetch_round(year, rnd, sess, use_cache=False)
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
            complete, n = round_settlement(html, year, rnd)
            _write_cache_meta(
                cache_path, complete=complete, row_count=n,
                collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            got += 1
            streak = 0
            state = "정산완료" if complete else "미정산·다음 실행 재수집"
            print(f"  [{year}-{rnd:03d}] 게임행 {n:4d}건 수집 · {state}", flush=True)
        time.sleep(ROUND_INTERVAL)

    print(f"[{year}] 신규 {got} · 캐시재사용 {cached} · 실패 {miss}", flush=True)
    return got, cached


def main(argv: list[str]) -> int:
    years = [int(a) for a in argv[1:]] or default_years()
    print(f"수집 대상 연도: {years}", flush=True)
    t0 = time.time()
    for y in years:
        print(f"\n=== {y}년 ===", flush=True)
        collect_year(y)
    print(f"\n총 소요 {time.time()-t0:.0f}초", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
