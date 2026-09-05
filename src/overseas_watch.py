"""해외 배당 실시간 스냅샷 — 프로토와 같은 시점에 기록한다.

왜 실시간이어야 하는가 [사실]
------------------------------
BetExplorer **결과** 페이지의 배당은 마감 시점 값이다. 경기 상세의 `data-created` 로 확인했다:

    10x10bet  1.89/1.99  created=2026-07-26 10:55   (경기 18:30 → 약 7시간 전)
    1xBet     1.94/1.96  created=2026-07-26 10:57

프로토 배당은 최대 60시간 전에 굳는다.
→ **결과 페이지 배당은 프로토 베팅 시점에 알 수 없는 값이다.**
   그걸로 계산한 +EV 는 낙관 편향이 있고, 그대로 전략이 되지 못한다.

다행히 **예정 경기(fixtures) 페이지에도 배당이 있다.** MLB 에서 확인(12경기).
→ 프로토가 발매 중일 때 해외 배당을 같이 찍으면 **동시점 대조**가 된다.
   이 스크립트가 그 역할을 한다.

산출물
------
    data/raw/overseas/live_odds.csv   (관측시각, 리그, 팀, 배당, 킥오프)

`snapshot.py`(프로토 배당)와 같은 주기로 돌려야 짝이 맞는다.

사용:
    python src/overseas_watch.py              # 1회
    python src/overseas_watch.py --loop 900   # 15분마다
"""
from __future__ import annotations

import csv
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from runtime_db import RuntimeDatabase, database_enabled

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "overseas"
LOG = OUT / "live_odds.csv"
BASE = "https://www.betexplorer.com"
GAP = 2.0

FIELDS = ["observed_at", "league", "home_en", "away_en", "kickoff",
          "odds", "overround", "payout"]

# 프로토가 발매하는 리그 중 BetExplorer 에 예정 경기가 있는 것
SOURCES = {
    "MLB": "/baseball/usa/mlb/fixtures/",
    "KBO": "/baseball/south-korea/kbo/fixtures/",
    "NPB": "/baseball/japan/npb/fixtures/",
    "K리그1": "/football/south-korea/k-league-1/fixtures/",
}

_TAG = re.compile(r"<[^>]+>")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml"})
    return s


def parse_fixtures(html: str) -> list[dict]:
    """예정 경기 행 → (팀, 킥오프, 배당).

    결과 페이지와 달리 스코어가 없고 킥오프 시각이 들어간다.
    """
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        if "data-odd" not in tr:
            continue
        m_a = re.search(r'class="in-match"[^>]*>(.*?)</a>', tr, re.S)
        odds = re.findall(r'data-odd="([\d.]+)"', tr)
        if not m_a or len(odds) < 2:
            continue
        txt = _TAG.sub("", m_a.group(1)).strip()
        if " - " not in txt:
            continue
        home_en, away_en = [t.strip() for t in txt.split(" - ", 1)]
        m_dt = re.search(r'no-wrap">([^<]*)<', tr)
        rows.append({
            "home_en": home_en, "away_en": away_en,
            "kickoff": (m_dt.group(1).strip() if m_dt else ""),
            "odds": [float(o) for o in odds[:3]],
        })
    return rows


def _append(rows: list[dict]) -> None:
    if not rows:
        return
    if database_enabled():
        db = RuntimeDatabase()
        db.append_events("overseas_live_odds", rows)
        return
    new = not LOG.exists()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def poll(sess: requests.Session) -> int:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = []
    for league, path in SOURCES.items():
        try:
            r = sess.get(BASE + path, timeout=25)
            games = parse_fixtures(r.text) if r.status_code == 200 else []
        except Exception as e:                       # noqa: BLE001
            print(f"  [{league}] 오류 {type(e).__name__}", flush=True)
            games = []
        for g in games:
            od = g["odds"]
            # 2-way 는 앞의 둘, 3-way(축구)는 셋을 다 쓴다
            use = od if len(od) >= 3 else od[:2]
            if any(o <= 1 for o in use):
                continue
            ov = sum(1 / o for o in use)
            out.append({
                "observed_at": ts, "league": league,
                "home_en": g["home_en"], "away_en": g["away_en"],
                "kickoff": g["kickoff"],
                "odds": ",".join(f"{o:.2f}" for o in use),
                "overround": f"{ov:.5f}", "payout": f"{100/ov:.2f}"})
        print(f"  {league:8} 예정 {len(games):3d}경기", flush=True)
        time.sleep(GAP)

    _append(out)
    print(f"[{ts}] 해외 배당 {len(out)}건 기록", flush=True)
    return len(out)


def main(argv: list[str]) -> int:
    loop = 0
    if "--loop" in argv:
        loop = int(argv[argv.index("--loop") + 1])
    sess = _session()
    while True:
        poll(sess)
        if not loop:
            return 0
        time.sleep(loop)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
