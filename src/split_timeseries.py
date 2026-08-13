"""단일 odds_timeseries.csv 를 일별 샤드로 쪼갠다 (1회성 마이그레이션).

왜
--
2026-08-13: 단일 파일이 138MB 가 되어 **GitHub 100MB 파일 한도**에 걸렸다.
pre-receive 훅이 push 를 거부해 수집은 되는데 밖으로 못 나가는 상태가 3일 갔다.
같은 파일이 2026-08-06 의 디스크 폭발(loose object 2.67GiB) 원인이기도 했다 —
30분마다 커밋할 때마다 100MB 넘는 blob 이 통째로 새로 생겼기 때문이다.

무엇을
------
ts 컬럼(ISO8601)의 연-월-일로 갈라 odds_timeseries_YYYYMMDD.csv 로 쓴다.
(월별로 해봤더니 2026-08 한 달이 106MB 라 여전히 한도를 넘었다.)
원본은 .bak 으로 남긴다. 행 수가 맞는지 확인한 뒤에만 원본을 치운다.

    python src/split_timeseries.py          # 확인만(쓰지 않음)
    python src/split_timeseries.py --write  # 실제로 쪼갠다
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "snapshots"
SRC = OUT / "odds_timeseries.csv"


def main(argv: list[str]) -> int:
    write = "--write" in argv
    if not SRC.exists():
        print(f"원본이 없다: {SRC} — 이미 쪼갰거나 수집 전이다")
        return 0

    size = SRC.stat().st_size
    print(f"원본 {SRC.name} · {size:,}B ({size/1024/1024:.1f}MB)")

    counts: Counter = Counter()
    writers: dict = {}
    handles: dict = {}
    total = 0

    ragged = 0
    with SRC.open(newline="", encoding="utf-8") as f:
        # ⚠️ 헤더보다 열이 많은 행이 섞여 있다. 기본값이면 남는 값이 None 키로 들어가고
        #    DictWriter 가 "fields not in fieldnames: None" 으로 죽는다.
        #    마이그레이션이 그 몇 행 때문에 통째로 멈추면 안 되므로, 남는 값은
        #    세어서 보고만 하고 알려진 열만 옮긴다.
        rd = csv.DictReader(f, restkey="_extra")
        fields = rd.fieldnames or []
        for row in rd:
            total += 1
            if row.pop("_extra", None) is not None:
                ragged += 1
            # ts 는 '2026-08-13T08:03:31+00:00' 형태. 앞 10자가 연-월-일이다.
            ym = str(row.get("ts", ""))[:10].replace("-", "")
            if len(ym) != 8 or not ym.isdigit():
                ym = "unknown"
            counts[ym] += 1
            if not write:
                continue
            if ym not in writers:
                p = OUT / f"odds_timeseries_{ym}.csv"
                # ⚠️ 이미 그날 샤드가 있으면(수집기가 새 코드로 먼저 돌았다면)
                #    덮어쓰지 말고 이어 붙인다. 그 사이 모은 행을 잃으면 안 된다.
                new = not p.exists()
                h = p.open("a", newline="", encoding="utf-8")
                w = csv.DictWriter(h, fieldnames=fields, extrasaction="ignore")
                if new:
                    w.writeheader()
                handles[ym], writers[ym] = h, w
            writers[ym].writerow(row)

    for h in handles.values():
        h.close()

    print(f"총 {total:,}행" + (f" · 열이 남는 행 {ragged:,}건(남는 값은 버림)" if ragged else ""))
    for ym in sorted(counts):
        mb = (OUT / f"odds_timeseries_{ym}.csv").stat().st_size / 1024 / 1024 \
            if write and (OUT / f"odds_timeseries_{ym}.csv").exists() else 0
        print(f"  {ym}: {counts[ym]:>9,}행" + (f"  → {mb:.1f}MB" if write else ""))

    if not write:
        print("\n확인만 했다. 실제로 쪼개려면 --write 를 붙일 것")
        return 0

    # 쪼갠 결과의 행 수가 원본과 같은지 확인한 뒤에만 원본을 치운다.
    got = 0
    for ym in counts:
        p = OUT / f"odds_timeseries_{ym}.csv"
        with p.open(newline="", encoding="utf-8") as f:
            got += sum(1 for _ in csv.DictReader(f))
    print(f"\n검증 — 원본 {total:,}행 vs 샤드 합계 {got:,}행")
    if got < total:
        print("🔴 행이 줄었다. 원본을 그대로 둔다 — 수동 확인 필요")
        return 1

    bak = SRC.with_suffix(".csv.bak")
    SRC.rename(bak)
    print(f"완료 — 원본은 {bak.name} 로 옮겼다 (확인 후 지울 것)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
