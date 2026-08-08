"""아카이브(html.gz)가 연도별로 실제 파싱되는지 확인한다.

2026-08-08: data/raw/wisetoto/2026 에 95회차가 있는데 games.csv 의 2026 행이 0이다.
build_dataset 은 CACHE.glob("*/*.html.gz") 로 전 연도를 훑으므로 코드 경로는 맞다.
그렇다면 **읽거나 파싱하는 단계에서 조용히 0행이 되고 있다**는 뜻이다.

    python src/diag_archive.py
"""
from __future__ import annotations

import gzip
import sys
import traceback
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wisetoto import CACHE, parse_rows, repair_mojibake      # noqa: E402


def probe(p: Path) -> tuple[int, str]:
    year = int(p.parent.name)
    rnd = int(p.stem.replace(".html", ""))
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:                                   # noqa: BLE001
        return -1, f"읽기 실패 {type(e).__name__}: {e}"
    size = len(html)
    try:
        rows = parse_rows(repair_mojibake(html), year, rnd)
    except Exception as e:                                   # noqa: BLE001
        return -2, f"파싱 예외 {type(e).__name__}: {e} (html {size:,}자)"
    return len(rows), f"html {size:,}자"


def main() -> int:
    for ydir in sorted(CACHE.glob("*")):
        if not ydir.is_dir():
            continue
        files = sorted(ydir.glob("*.html.gz"))
        if not files:
            continue
        # 연도마다 앞·중간·뒤에서 표본을 뽑는다
        picks = [files[0], files[len(files) // 2], files[-1]]
        print(f"=== {ydir.name}  ({len(files)}개 파일)")
        for p in picks:
            n, note = probe(p)
            flag = "🔴" if n <= 0 else "  "
            print(f"  {flag} {p.name:<16} 행 {n:>5}   {note}")

        # 전수 스캔은 shared-cpu-1x 에서 10분을 넘긴다. 필요할 때만 켠다.
        #   python src/diag_archive.py --full
        if ydir.name == "2026" and "--full" in sys.argv:
            zero = []
            total = 0
            for p in files:
                n, _ = probe(p)
                total += max(n, 0)
                if n <= 0:
                    zero.append((p.name, n))
            print(f"  → 2026 전수: 총 {total:,}행 · 0행 파일 {len(zero)}/{len(files)}개")
            for name, n in zero[:10]:
                print(f"      {name} (n={n})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
