"""원본 HTML 캐시 → 분석용 데이터셋(CSV) 1회 변환.

553개 회차를 BeautifulSoup으로 매번 다시 파싱하면 분석 한 번에 수 분이 걸린다.
한 번 펼쳐 CSV로 저장해두면 Q0·Q1·Q4·Q5가 전부 초 단위로 돌아간다.

산출물
    data/processed/games.csv   게임행 단위 (Q1 마진 분석용)
    data/processed/bets.csv    선택지 단위 (Q0·Q4·Q5 수익률 분석용)

사용:
    python src/build_dataset.py
"""
from __future__ import annotations

import csv
import gzip
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import to_bets                             # noqa: E402
from wisetoto import CACHE, parse_rows, repair_mojibake   # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "processed"

GAME_FIELDS = ["year", "round", "game_no", "date_text", "sport", "league",
               "market_tag", "market_label", "market_family", "booking_class",
               "market_type", "n_way", "home", "away", "odds", "overround",
               "result", "is_void"]

BET_FIELDS = ["year", "round", "game_no", "sport", "league", "market_family",
              "booking_class", "n_way", "overround", "selection", "sel_index",
              "odds", "won", "profit"]


def main() -> int:
    files = sorted(CACHE.glob("*/*.html.gz"))
    if not files:
        print("캐시가 비어 있습니다. 먼저 python src/collect.py 를 실행하세요.")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    n_games = n_bets = 0

    with (OUT / "games.csv").open("w", newline="", encoding="utf-8") as gf, \
         (OUT / "bets.csv").open("w", newline="", encoding="utf-8") as bf:
        gw = csv.DictWriter(gf, fieldnames=GAME_FIELDS)
        bw = csv.DictWriter(bf, fieldnames=BET_FIELDS)
        gw.writeheader()
        bw.writeheader()

        for i, p in enumerate(files, 1):
            year = int(p.parent.name)
            rnd = int(p.stem.replace(".html", ""))
            # ⚠️ 여기는 `fetch_round` 를 안 거치고 gzip 을 직접 연다.
            #    수집 당시 charset 추측이 빗나가 모지바케로 저장된 회차가 11개 있어서
            #    읽는 쪽에서도 되돌려 줘야 한다. 안 그러면 그 3,429행의 result 가
            #    깨진 채('нҷҲмҠ№'=홈승) 모든 분석에서 조용히 빠진다.
            with gzip.open(p, "rt", encoding="utf-8") as f:
                rows = parse_rows(repair_mojibake(f.read()), year, rnd)

            for r in rows:
                d = asdict(r)
                d["odds"] = ",".join(f"{o:.2f}" for o in r.odds)
                d["overround"] = f"{r.overround:.6f}" if r.overround else ""
                d["market_family"] = r.market_family
                d["booking_class"] = r.booking_class
                d["market_type"] = r.market_type
                d["n_way"] = r.n_way
                gw.writerow({k: d.get(k, "") for k in GAME_FIELDS})
            n_games += len(rows)

            for b in to_bets(rows):
                bw.writerow({
                    "year": b.year, "round": b.round, "game_no": b.game_no,
                    "sport": b.sport, "league": b.league,
                    "market_family": b.market_family,
                    "booking_class": b.booking_class, "n_way": b.n_way,
                    "overround": f"{b.overround:.6f}", "selection": b.selection,
                    "sel_index": b.sel_index, "odds": f"{b.odds:.2f}",
                    "won": int(b.won), "profit": f"{b.profit:.4f}",
                })
                n_bets += 1

            if i % 50 == 0:
                print(f"  {i}/{len(files)} 회차 처리 "
                      f"({time.time()-t0:.0f}초)", flush=True)

    print(f"\n완료 — 회차 {len(files)} · 게임행 {n_games:,} · 베팅레코드 {n_bets:,}")
    print(f"소요 {time.time()-t0:.0f}초")
    print(f"  {OUT/'games.csv'}")
    print(f"  {OUT/'bets.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
