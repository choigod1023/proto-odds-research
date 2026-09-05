"""원본 HTML 캐시 → 분석용 데이터셋 1회 변환.

553개 회차를 BeautifulSoup으로 매번 다시 파싱하면 분석 한 번에 수 분이 걸린다.
운영에서는 DB 문서를 읽고 두 데이터셋을 DB에 원자적으로 교체한다.
개발 환경에서는 gzip fixture를 읽고 CSV를 생성한다.

산출물
    data/processed/games.csv   게임행 단위 (Q1 마진 분석용)
    data/processed/bets.csv    선택지 단위 (Q0·Q4·Q5 수익률 분석용)

사용:
    python src/build_dataset.py
"""
from __future__ import annotations

import csv
import gzip
import json
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


def _game_record(row) -> dict:
    record = asdict(row)
    record.update(
        odds=",".join(f"{o:.2f}" for o in row.odds),
        overround=f"{row.overround:.6f}" if row.overround else "",
        market_family=row.market_family, booking_class=row.booking_class,
        market_type=row.market_type, n_way=row.n_way,
    )
    return {key: record.get(key, "") for key in GAME_FIELDS}


def _bet_record(bet) -> dict:
    return {
        "year": bet.year, "round": bet.round, "game_no": bet.game_no,
        "sport": bet.sport, "league": bet.league,
        "market_family": bet.market_family, "booking_class": bet.booking_class,
        "n_way": bet.n_way, "overround": f"{bet.overround:.6f}",
        "selection": bet.selection, "sel_index": bet.sel_index,
        "odds": f"{bet.odds:.2f}", "won": int(bet.won), "profit": f"{bet.profit:.4f}",
    }


class _IncompleteArchive(ValueError):
    pass


def _database_records(connection, *, bets: bool, stats: dict):
    """Stream one round at a time; both passes use the same SQLite read snapshot.

    The paired writer must consume/stage these generators before acquiring the
    live DB write lock. A failed parse or latest-year guard aborts both datasets.
    """
    years_seen = set()
    latest_year = None
    count = 0
    started = time.time()
    cursor = connection.execute(
        """SELECT name,payload_json FROM documents WHERE name GLOB 'archive:*'
           ORDER BY CAST(substr(name,9,4) AS INTEGER),
                    CAST(substr(name,14) AS INTEGER),name""")
    try:
        for index, document in enumerate(cursor, 1):
            parts = document["name"].split(":")
            if (len(parts) != 3 or len(parts[1]) != 4
                    or not parts[1].isdigit() or not parts[2].isdigit()
                    or int(parts[2]) < 1):
                raise _IncompleteArchive(f"Invalid archive name: {document['name']}")
            year, rnd = int(parts[1]), int(parts[2])
            latest_year = max(latest_year or year, year)
            html = json.loads(document["payload_json"])
            if not isinstance(html, str):
                raise _IncompleteArchive(f"Invalid archive payload: {document['name']}")
            rows = parse_rows(repair_mojibake(html), year, rnd)
            if rows:
                years_seen.add(year)
            records = (_bet_record(b) for b in to_bets(rows)) if bets else (
                _game_record(row) for row in rows)
            for record in records:
                count += 1
                yield record
            if index % 50 == 0:
                print(f"  DB {'bets' if bets else 'games'}: {index} 회차 처리 "
                      f"({time.time()-started:.0f}초)", flush=True)
        if latest_year not in years_seen:
            raise _IncompleteArchive(
                f"최신 연도({latest_year}) 행이 0건 — 두 데이터셋을 교체하지 않습니다.")
        stats["bets" if bets else "games"] = count
        stats["years"] = sorted(years_seen)
    finally:
        cursor.close()


def _build_database() -> int:
    database = RuntimeDatabase()
    connection = database.connect()
    started = time.time()
    stats = {}
    try:
        # Pin inputs across both passes even while live collectors update archives.
        connection.execute("BEGIN")
        rounds = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE name GLOB 'archive:*'").fetchone()[0]
        if not rounds:
            print("DB 캐시가 비어 있습니다. 먼저 python src/collect.py 를 실행하세요.")
            return 1
        database.replace_datasets_rows({
            "processed_games": (_database_records(connection, bets=False, stats=stats), GAME_FIELDS),
            "processed_bets": (_database_records(connection, bets=True, stats=stats), BET_FIELDS),
        })
    except _IncompleteArchive as error:
        print(error)
        return 1
    finally:
        connection.rollback()
        connection.close()
    print(f"\n완료 — 회차 {rounds} · 게임행 {stats['games']:,} · 베팅레코드 {stats['bets']:,}")
    print(f"소요 {time.time()-started:.0f}초 · 연도 {stats['years']}")
    print("  DB: processed_games, processed_bets")
    return 0


def main() -> int:
    if database_enabled():
        return _build_database()
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
                gw.writerow(_game_record(r))
            n_games += len(rows)
            if rows:
                years_seen.add(year)

            for b in to_bets(rows):
                bw.writerow(_bet_record(b))
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

    tmp_g.replace(OUT / "games.csv")
    tmp_b.replace(OUT / "bets.csv")

    print(f"\n완료 — 회차 {len(files)} · 게임행 {n_games:,} · 베팅레코드 {n_bets:,}")
    print(f"소요 {time.time()-t0:.0f}초 · 연도 {sorted(years_seen)}")
    print(f"  {OUT/'games.csv'}")
    print(f"  {OUT/'bets.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
