"""정보 공개 시각 관측기 — 배당이 굳은 뒤 무엇이, 언제 공개되는가.

왜 필요한가
------------
`findings/정보시차_선발.md` 에서 회차 첫경기일 대비 간격(lag)을 정보량의 **대리지표**로
썼는데, **무효로 드러났다.** 정보가 적을수록 배당이 더 정확하다는 뒤집힌 결과가
야구·축구·농구에서 일관되게 나왔다. lag=0 경기(회차 첫날)의 매치업 성격이 달라 교란된 것이다.

→ 대리지표로는 답할 수 없다. **실제 공개 시각을 관측**하는 수밖에 없다.

무엇을 기록하나
---------------
경기 전 경기를 주기적으로 조회해, 각 필드가 **비어 있다가 채워지는 순간**을 기록한다.

    KBO   homeStarterName / awayStarterName  (네이버 스포츠 공개 API)
          → 비어 있음 → 채워짐 = 선발 예고 시각

이 시각을 `snapshot.py` 가 기록하는 **배당 확정·변동 시각**과 맞대면 답이 나온다:

    예고 시점에 배당이 움직인다  → 시장도 그때 알았다 → **시차 존재**
    움직이지 않는다              → 이미 알고 있었다   → **시차 없음**

종목 커버리지 (2026-07-27 확인)
    KBO  kbaseball/kbo   ✅ 선발
    MLB  wbaseball/mlb   ✅ 선발
    NPB  wbaseball/npb   ✅ 선발
    축구 (K리그·MLS)      ❌ 라인업 없음.
         `/schedule/games/{id}/preview` 에는 팀 기록·득점 상위 선수만 있고
         라인업은 경기 직전에야 공개된다 → 시차 검증 대상으로는 오히려 더 좋지만
         관측 경로를 따로 찾아야 한다
    KBL·NBA·V리그·EPL     — 7월 현재 시즌 오프. 검증은 개막 후

사용:
    python src/info_watch.py              # 1회
    python src/info_watch.py --loop 1800  # 30분마다
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from runtime_db import RuntimeDatabase, database_enabled

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "info_watch"
LOG = OUT / "starter_announcements.csv"
CHANGE_LOG = OUT / "starter_changes.jsonl"
STATE = OUT / "_state.json"
API = "https://api-gw.sports.naver.com/schedule/games"

FIELDS = ["observed_at", "gameId", "game_datetime", "league",
          "home", "away", "field", "value", "hours_before_game", "is_baseline"]

# (upperCategoryId, categoryId, 표시명) — 선발 필드를 제공하는 것만.
# ⚠️ upperCategoryId 가 리그마다 다르다. KBO 는 kbaseball, MLB/NPB 는 wbaseball 이다.
TARGETS = [
    ("kbaseball", "kbo", "KBO"),
    ("wbaseball", "mlb", "MLB"),
    ("wbaseball", "npb", "NPB"),
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json", "Referer": "https://m.sports.naver.com/",
    })
    return s


def _load_state() -> dict:
    if database_enabled():
        stored = RuntimeDatabase().get_document("info_watch_state")
        return stored or {}
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def _save_state(st: dict) -> None:
    if database_enabled():
        db = RuntimeDatabase()
        db.put_document("info_watch_state", st)
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _append(rows: list[dict]) -> None:
    if not rows:
        return
    if database_enabled():
        db = RuntimeDatabase()
        db.append_events("starter_announcements", rows)
        db.export_events_csv("starter_announcements", LOG, FIELDS)
        return
    new = not LOG.exists()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def _append_changes(rows: list[dict]) -> None:
    """선발 교체는 기존 CSV 스키마를 깨지 않도록 별도 append-only 로그에 둔다."""
    if not rows:
        return
    if database_enabled():
        db = RuntimeDatabase()
        db.append_events("starter_changes", rows)
        db.export_events("starter_changes", CHANGE_LOG)
        return
    CHANGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with CHANGE_LOG.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def transition(old: str, new: str) -> str | None:
    """공백→값은 발표, 값→다른 값은 실제로 중요한 선발 변경이다."""
    old, new = (old or "").strip(), (new or "").strip()
    if new and not old:
        return "starter_announced"
    if new and old and new != old:
        return "starter_changed"
    return None


def poll(sess: requests.Session, days_ahead: int = 5) -> int:
    st = _load_state()
    # ⚠️ 첫 폴링은 이미 공개돼 있던 값을 처음 보는 것이라 '공개 시각'이 아니다.
    #    기준선으로 표시하고 분석에서 제외한다.
    baseline = not st
    now = datetime.now(timezone.utc)
    today = date.today()
    events = []
    changes = []

    for up, cid, name in TARGETS:
        try:
            r = sess.get(API, params={
                "fields": "basic,statusNum,homeStarterName,awayStarterName",
                "upperCategoryId": up, "categoryId": cid,
                "fromDate": today.isoformat(),
                "toDate": (today + timedelta(days=days_ahead)).isoformat(),
                "size": 200}, timeout=25)
            r.raise_for_status()
            games = r.json().get("result", {}).get("games", [])
        except Exception as e:                       # noqa: BLE001
            print(f"  [{name}] 오류 {type(e).__name__}: {e}", flush=True)
            continue

        for g in games:
            gid = g.get("gameId")
            if not gid or g.get("statusCode") != "BEFORE":
                continue      # 이미 시작한 경기는 관측 대상이 아니다
            gdt = g.get("gameDateTime")
            for fld in ("homeStarterName", "awayStarterName"):
                val = (g.get(fld) or "").strip()
                key = f"{gid}|{fld}"
                had = st.get(key, "")
                event_type = transition(had, val)
                if event_type == "starter_announced":
                    # ⭐ 비어 있다가 채워지는 순간
                    hrs = None
                    if gdt:
                        try:
                            gt = datetime.fromisoformat(gdt).replace(
                                tzinfo=timezone(timedelta(hours=9)))
                            hrs = round((gt - now).total_seconds() / 3600, 2)
                        except ValueError:
                            pass
                    events.append({
                        "observed_at": now.isoformat(timespec="seconds"),
                        "gameId": gid, "game_datetime": gdt, "league": name,
                        "home": g.get("homeTeamName"), "away": g.get("awayTeamName"),
                        "field": fld, "value": val, "hours_before_game": hrs,
                        "is_baseline": int(baseline)})
                elif event_type == "starter_changed":
                    hrs = None
                    if gdt:
                        try:
                            gt = datetime.fromisoformat(gdt).replace(
                                tzinfo=timezone(timedelta(hours=9)))
                            hrs = round((gt - now).total_seconds() / 3600, 2)
                        except ValueError:
                            pass
                    changes.append({
                        "observed_at": now.isoformat(timespec="seconds"),
                        "event_type": event_type, "gameId": gid,
                        "game_datetime": gdt, "league": name,
                        "home": g.get("homeTeamName"), "away": g.get("awayTeamName"),
                        "field": fld, "previous_value": had, "value": val,
                        "hours_before_game": hrs, "is_baseline": False,
                    })
                if val:
                    st[key] = val
                elif key not in st:
                    st[key] = ""

    _append(events)
    _append_changes(changes)
    _save_state(st)
    ts = now.isoformat(timespec="seconds")
    print(f"[{ts}] 신규 공개 {len(events)}건 · 선발 변경 {len(changes)}건 · "
          f"추적 중 {len(st)}필드", flush=True)
    for e in events:
        h = e["hours_before_game"]
        print(f"    {e['league']} {e['away']}@{e['home']} {e['field']}={e['value']}"
              f"  경기 {h}시간 전", flush=True)
    for e in changes:
        print(f"    ⚠️ {e['league']} {e['away']}@{e['home']} {e['field']} "
              f"{e['previous_value']} → {e['value']}  경기 {e['hours_before_game']}시간 전",
              flush=True)
    return len(events) + len(changes)


def summarise() -> None:
    """지금까지 관측된 공개 시각 분포."""
    if not LOG.exists():
        print("아직 관측 기록이 없습니다.")
        return
    import statistics as stx
    rows = list(csv.DictReader(LOG.open(encoding="utf-8")))
    real = [r for r in rows if r.get("is_baseline") != "1"]
    n_base = len(rows) - len(real)
    hrs = [float(r["hours_before_game"]) for r in real
           if r.get("hours_before_game") not in (None, "", "None")]
    if not hrs:
        print(f"관측 {len(rows)}건 (기준선 {n_base}) · 실제 공개 포착 0건 "
              f"— 다음 폴링부터 잡힌다")
        return
    print(f"\n실제 공개 포착 {len(hrs)}건 (기준선 {n_base} 제외) · 경기 전 공개 시각(시간)")
    print(f"  중앙값 {stx.median(hrs):.1f}h · 최소 {min(hrs):.1f}h · 최대 {max(hrs):.1f}h")


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        assert transition("", "화이트") == "starter_announced"
        assert transition("화이트", "문동주") == "starter_changed"
        assert transition("화이트", "화이트") is None
        assert transition("화이트", "") is None
        print("✅ info_watch 자기검사 통과 (발표/교체 구분)")
        return 0
    loop = 0
    if "--loop" in argv:
        loop = int(argv[argv.index("--loop") + 1])
    sess = _session()
    while True:
        poll(sess)
        summarise()
        if not loop:
            return 0
        time.sleep(loop)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
