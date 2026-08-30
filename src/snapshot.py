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
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wisetoto import BASE, _session, get_master_seq, parse_rows  # noqa: E402
from runtime_db import RuntimeDatabase, database_enabled  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "snapshots"
CH_FILE = OUT / "changes.csv"

# ⚠️ 예전엔 단일 파일(odds_timeseries.csv)에 계속 append 했다. 그게 두 번 터졌다.
#
#   1. **GitHub 100MB 파일 한도** — 2026-08-13 에 138MB 가 되어 pre-receive 훅이
#      push 를 거부했다. 수집은 되는데 밖으로 못 나가는 상태로 3일이 갔다.
#   2. **git 비대화** — 30분마다 커밋하는데 그때마다 100MB 넘는 blob 이 통째로
#      새로 생긴다. 2026-08-06 에 loose object 2.67GiB 가 3GB 볼륨을 다 먹고
#      모든 쓰기가 ENOSPC 로 실패했다.
#
# 쪼개면 둘 다 풀린다. 지난 샤드는 더 이상 안 바뀌므로 git 이 한 번만 저장하고,
# 커밋마다 움직이는 건 오늘 것뿐이다.
# ⚠️ **일별**이다. 월별로 했더니 2026-08 한 달만 868,787행 ≈ 106MB 로 여전히
#    한도를 넘었다(하루 약 67,000행). 일 단위면 약 8MB 로 안전하게 묶인다.
LEGACY_TS = OUT / "odds_timeseries.csv"      # 쪼개기 전 단일 파일(남아 있으면 같이 읽는다)


def ts_file(when: datetime | None = None) -> Path:
    """그날의 스냅샷 샤드 경로."""
    d = when or datetime.now(timezone.utc)
    return OUT / f"odds_timeseries_{d:%Y%m%d}.csv"


def ts_files() -> list[Path]:
    """읽을 때 쓰는 전체 목록 — 일별 샤드 + (남아 있다면) 옛 단일 파일."""
    files = sorted(OUT.glob("odds_timeseries_*.csv"))
    if LEGACY_TS.exists():
        files.insert(0, LEGACY_TS)
    return files


def load_timeseries():
    """모든 샤드를 이어 붙인다. 읽는 쪽은 파일 경로를 직접 알 필요가 없다."""
    import pandas as pd                      # 수집 루프에는 필요 없는 무거운 의존이다
    fs = ts_files()
    if not fs:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)

UNPLAYED = {"경기전", "", "-"}
SCAN_RANGE = 12          # 최신 회차 기준 앞뒤로 훑을 범위
REQUEST_GAP = 2.5

FIELDS = ["ts", "year", "round", "game_no", "sport", "league", "market_family",
          "n_way", "market_label", "home", "away", "date_text", "odds", "result"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def probe_latest_round(sess, year: int) -> int:
    """캐시 없이 그 해의 최신 회차를 찾는다 — 성큼 건너뛰고 이분 탐색으로 좁힌다.

    회차를 1부터 하나씩 세면 요청이 수십 번 나간다. 존재 여부만 보면 되므로
    2배씩 뛰어 상한을 잡은 뒤(≈8회) 이분 탐색으로 좁힌다(≈7회).
    캐시가 빈 첫 부팅에만 드는 비용이다.
    """
    lo = 1
    if not get_master_seq(year, lo, sess):
        return 1
    time.sleep(REQUEST_GAP)

    hi = 2
    while hi < 400 and get_master_seq(year, hi, sess):
        time.sleep(REQUEST_GAP)
        lo, hi = hi, hi * 2
    # lo = 존재 확인됨, hi = 없음(또는 상한)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if get_master_seq(year, mid, sess):
            lo = mid
        else:
            hi = mid
        time.sleep(REQUEST_GAP)
    print(f"  캐시 없음 — 최신 회차 탐색 결과 {year}-{lo}회차", flush=True)
    return lo


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
    # ⚠️ 오늘·어제 샤드만 읽는다. 배당 변동은 **직전 스냅샷과의 비교**라서
    #    최신 값만 있으면 되고, 전 기간을 읽으면 15분마다 130MB 를 훑게 된다.
    #    자정 직후에도 값이 안 끊기도록 어제 것을 함께 본다.
    now = datetime.now(timezone.utc)
    files = [ts_file(now - timedelta(days=1)), ts_file(now)]
    for p in files:
        if not p.exists():
            continue
        with p.open(newline="", encoding="utf-8") as f:
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
        # ⚠️ 배당이 아직 공개되지 않은 행(odds=[])이 섞여 있다.
        #    2026-88·89회차처럼 경기 목록만 먼저 뜨고 배당은 나중에 붙는다.
        #    이걸 세면 '변동 0건'이 실제보다 안정적으로 보인다.
        priced = [r for r in rows if r.odds]
        unpriced = len(rows) - len(priced)
        if unpriced:
            print(f"  [{year}-{rnd}] 배당 미공개 {unpriced}행 제외 "
                  f"(배당 있는 행 {len(priced)})", flush=True)

        for r in priced:
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

    # DB가 운영 원본이다. CSV는 기존 분석 코드용 호환 export다.
    database = RuntimeDatabase()
    database.insert_odds(new_rows)
    if database_enabled():
        database.export_odds_csv(ts_file(), day=ts[:10])
    else:
        _append(ts_file(), FIELDS, new_rows)
    if changes:
        if database_enabled():
            database.append_events("odds_changes", changes)
            database.export_events_csv("odds_changes", CH_FILE,
                                       FIELDS + ["prev_odds"])
        else:
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
    # 캐시가 비어 있으면(새 서버 첫 부팅 등) 1회차부터 훑게 되는데, SCAN_RANGE 가
    # 12 라 7월의 60번대 회차엔 영영 못 닿는다. fly.io 로 옮기고 나서 실제로
    # "발매 중인 회차를 찾지 못했습니다" 로 죽었다(캐시 *.html.gz 는 gitignore).
    # → 캐시가 없으면 서버에 직접 물어 최신 회차를 찾는다.
    hint = (max(have) - 3) if have else max(1, probe_latest_round(sess, year) - 3)
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
