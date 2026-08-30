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
from runtime_db import RuntimeDatabase, database_enabled  # noqa: E402

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

    # ⚠️ 최종 파일에 직접 쓰면 **중간에 죽었을 때 잘린 파일이 그대로 남는다.**
    #    2026-08-08 에 정확히 그랬다: 재빌드가 5400초 제한에 걸려 죽었고
    #    games.csv 가 2025 중간에서 끊긴 채 19MB 로 남았다. 크기가 멀쩡해
    #    아무도 눈치채지 못했다.
    #    그리고 glob 이 정렬돼 있어 **잘리는 건 언제나 맨 뒤, 즉 올해**다.
    #    그 결과 build_forms(season=올해) 가 0건이 되고, 사이트 해설 249건 중
    #    56% 가 "이번 시즌 기록이 충분히 쌓이지 않았다" 로 나갔다.
    #    → 임시 파일에 쓰고 **다 끝난 뒤에만** 갈아끼운다.
    tmp_g = OUT / "games.csv.tmp"
    tmp_b = OUT / "bets.csv.tmp"
    years_seen: set[int] = set()

    with tmp_g.open("w", newline="", encoding="utf-8") as gf, \
         tmp_b.open("w", newline="", encoding="utf-8") as bf:
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
            if rows:
                years_seen.add(year)

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

    # ⚠️ 갈아끼우기 전에 **올해가 들어 있는지** 확인한다.
    #    이 데이터셋을 읽는 build_forms 는 season=올해 로 거른다. 올해가 없으면
    #    최근폼이 통째로 비고, 사이트는 "이번 시즌 기록이 충분히 쌓이지 않았다" 만
    #    반복한다. 그 상태로 갈아끼우느니 **직전 데이터셋을 지키는 게 낫다.**
    cur_year = max(int(p.parent.name) for p in files)
    if cur_year not in years_seen:
        print(f"🔴 최신 연도({cur_year}) 행이 0건 — 교체하지 않고 중단한다. "
              f"수집된 연도: {sorted(years_seen)}")
        tmp_g.unlink(missing_ok=True)
        tmp_b.unlink(missing_ok=True)
        return 1

    # 운영에서는 DB가 정본이다. 두 임시 CSV가 모두 완주한 뒤 DB 트랜잭션으로
    # 각각 교체하고, 호환 CSV는 DB에서 다시 만든다.
    if database_enabled():
        db = RuntimeDatabase()
        db.replace_dataset_csv("processed_games", tmp_g)
        db.replace_dataset_csv("processed_bets", tmp_b)
        db.export_dataset_csv("processed_games", OUT / "games.csv")
        db.export_dataset_csv("processed_bets", OUT / "bets.csv")
        tmp_g.unlink(missing_ok=True)
        tmp_b.unlink(missing_ok=True)
    else:
        tmp_g.replace(OUT / "games.csv")
        tmp_b.replace(OUT / "bets.csv")

    print(f"\n완료 — 회차 {len(files)} · 게임행 {n_games:,} · 베팅레코드 {n_bets:,}")
    print(f"소요 {time.time()-t0:.0f}초 · 연도 {sorted(years_seen)}")
    print(f"  {OUT/'games.csv'}")
    print(f"  {OUT/'bets.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
