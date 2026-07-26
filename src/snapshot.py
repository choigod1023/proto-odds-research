"""Q2 — 발매 중 배당 변동 스냅샷 수집기.

프로젝트 전체가 "배당이 발매 시점에 굳고 경기까지 안 변한다"는 가정 위에 있는데,
프로토 규정은 "발매 중 변경 가능"이라고 적혀 있다. 그 충돌을 실측으로 푼다.

동작
----
1. 아직 정산되지 않은('경기전'이 남아 있는) 회차를 자동 탐지
2. 각 회차의 전 게임행 배당을 타임스탬프와 함께 CSV에 누적
3. 직전 스냅샷과 배당이 달라진 행만 골라 변동 로그에 기록

산출물
------
    data/raw/snapshots/odds_timeseries.csv   모든 스냅샷 (append)
    data/raw/snapshots/changes.csv           변동이 감지된 건만

사용:
    python src/snapshot.py            # 1회 실행
    python src/snapshot.py --loop 900 # 900초(15분)마다 반복
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wisetoto import BASE, _session, get_master_seq, parse_rows  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "snapshots"
TS_FILE = OUT / "odds_timeseries.csv"
CH_FILE = OUT / "changes.csv"

UNPLAYED = {"경기전", "", "-"}
SCAN_RANGE = 12          # 최신 회차 기준 앞뒤로 훑을 범위
REQUEST_GAP = 2.5

FIELDS = ["ts", "year", "round", "game_no", "sport", "league", "market_family",
          "n_way", "market_label", "home", "away", "date_text", "odds", "result"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_live_rounds(sess, year: int, start_hint: int) -> list[int]:
    """미정산 게임행이 남아 있는 회차를 찾는다."""
    live = []
    for rnd in range(max(1, start_hint), start_hint + SCAN_RANGE):
        seq = get_master_seq(year, rnd, sess)
        time.sleep(REQUEST_GAP)
        if not seq:
            break
        rows = _fetch(sess, year, rnd, seq)
        if rows is None:
            break
        pending = sum(1 for r in rows if r.result in UNPLAYED)
        if pending:
            live.append(rnd)
            print(f"  발매중: {year}-{rnd}회차 (미정산 {pending}/{len(rows)})", flush=True)
        time.sleep(REQUEST_GAP)
    return live


def _fetch(sess, year: int, rnd: int, seq: str | None = None):
    seq = seq or get_master_seq(year, rnd, sess)
    if not seq:
        return None
    r = sess.get(f"{BASE}/util/gameinfo/get_proto_list.htm", params={
        "game_category": "pt1", "game_year": year, "game_round": rnd,
        "game_month": "", "game_day": "", "game_info_master_seq": seq,
        "sports": "", "sort": "", "tab_type": "proto",
    }, timeout=40)
    r.raise_for_status()
    return parse_rows(r.text, year, rnd)


def _load_last() -> dict[tuple, str]:
    """직전 스냅샷의 (회차,경기번호) → 배당문자열"""
    last: dict[tuple, str] = {}
    if not TS_FILE.exists():
        return last
    with TS_FILE.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            last[(row["year"], row["round"], row["game_no"])] = row["odds"]
    return last


def snap(year: int, rounds: list[int]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    sess = _session()
    last = _load_last()
    ts = _now()

    new_rows, changes = [], []
    for rnd in rounds:
        try:
            rows = _fetch(sess, year, rnd)
        except Exception as e:                       # noqa: BLE001
            print(f"  [{year}-{rnd}] 오류 {type(e).__name__}: {e}", flush=True)
            continue
        if rows is None:
            continue
        for r in rows:
            odds_s = ",".join(f"{o:.2f}" for o in r.odds)
            key = (str(year), str(rnd), r.game_no)
            rec = {"ts": ts, "year": year, "round": rnd, "game_no": r.game_no,
                   "sport": r.sport, "league": r.league,
                   "market_family": r.market_family, "n_way": r.n_way,
                   "market_label": r.market_label, "home": r.home, "away": r.away,
                   "date_text": r.date_text, "odds": odds_s, "result": r.result}
            new_rows.append(rec)
            prev = last.get(key)
            if prev is not None and prev != odds_s:
                changes.append({**rec, "prev_odds": prev})
        time.sleep(REQUEST_GAP)

    _append(TS_FILE, FIELDS, new_rows)
    if changes:
        _append(CH_FILE, FIELDS + ["prev_odds"], changes)

    print(f"[{ts}] 스냅샷 {len(new_rows)}행 · 배당변동 {len(changes)}건", flush=True)
    for c in changes[:15]:
        print(f"    변동 {c['round']}-{c['game_no']} {c['league']} "
              f"{c['home']}/{c['away']} {c['market_family']}: "
              f"{c['prev_odds']} → {c['odds']}", flush=True)
    return len(changes)


def _append(path: Path, fields: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def main(argv: list[str]) -> int:
    loop = 0
    if "--loop" in argv:
        loop = int(argv[argv.index("--loop") + 1])

    year = datetime.now().year
    sess = _session()
    print(f"발매 중인 회차 탐지 ({year}년)...", flush=True)
    # 캐시에 있는 최신 회차 다음부터 훑는다
    from wisetoto import CACHE
    have = sorted(int(p.stem.replace(".html", ""))
                  for p in (CACHE / str(year)).glob("*.html.gz")) if (CACHE / str(year)).exists() else []
    # 캐시 최신 회차보다 조금 앞에서부터 훑는다.
    # 캐시에 있어도 그 시점엔 미정산이었을 수 있으므로(수집 당시 '경기전') 뒤로 물러선다.
    hint = (max(have) - 3) if have else 1
    rounds = find_live_rounds(sess, year, hint)
    if not rounds:
        print("발매 중인 회차를 찾지 못했습니다.")
        return 1
    print(f"대상: {year}년 {rounds}회차", flush=True)

    while True:
        snap(year, rounds)
        if not loop:
            return 0
        time.sleep(loop)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
