"""K리그 xG 스냅샷 수집기 — 진짜 xG 를 누수 없이 쌓는다.

왜 스냅샷인가
--------------
FootyStats 는 K리그 팀별 xG/xGA 를 홈·원정 분리로 제공한다(경기 페이지).
그런데 그 값은 **오늘 시점의 시즌 누적**이다. 이미 끝난 경기에 그대로 붙이면
그 경기 결과가 피처에 섞인다 — 데이터 누수다.

그래서 과거를 긁는 대신 **미래를 향해 매일 찍는다.** 오늘 찍어 둔 값은
내일 경기에 대해 100% 경기 전 정보다. snapshot.py(프로토 배당)·info_watch.py
(선발 예고)·overseas_watch.py(해외 배당)와 같은 방식이다.

왜 유효슈팅으로는 부족했나
--------------------------
src/soccer_process.py 결과 — 유효슈팅 차(과정) Brier 개선 +0.00062 <
득실 차(결과) +0.00299. 과정 지표가 결과 지표에 졌다. 야구(FIP)와 정반대다.
단 유효슈팅 계수의 z 는 2.63 으로 득실차(1.30)보다 오히려 강했다.
→ 신호는 있는데 **슛의 질을 못 보는 게 한계**라는 해석. 그 질을 넣은 게 xG 다.

접근 정책 (지킬 것)
-------------------
robots.txt 가 ClaudeBot 에 Crawl-delay: 1 을 명시 허용한다. 경기 페이지
(`-h2h-stats`)는 Disallow 목록에 없다. 반면 CSV 다운로드 `/c-dl.php*` 는
**명시적으로 Disallow** 이므로 이 스크립트는 절대 건드리지 않는다
(경기별 xG 이력이 거기 있더라도, 받으려면 사람이 브라우저로 직접 받아야 한다).
"""
from __future__ import annotations

import html as H
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from runtime_db import RuntimeDatabase, database_enabled

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "xg_snapshots.jsonl"

BASE = "https://footystats.org"
# 리그를 왜 이만큼 붙였나 — 순전히 **판정 시점을 당기기 위해서**다.
#
# 필요 표본은 측정 정밀도에서 나온다. close_market.py 부트스트랩에서 n=307 일 때
# 신뢰구간 반폭이 0.0072 였고, 반폭은 1/√n 로 줄어든다:
#
#     필요 n = (0.126 / 효과크기)²
#     효과 +0.006 가정 → 441경기 (과거 야구 xFIP +0.006은 시간누수로 무효)
#
# K리그1 만 쓰면 연 228경기라 441경기는 **2027년 8월**이다. 네 리그를 합치면
# 연 ~1,258경기라 **2026년 11월**로 당겨진다.
#
# ⚠️ 리그를 섞으면 모델에 리그 효과를 넣어야 한다(리그마다 득점 수준이 다르다).
#    표본만 합치고 그걸 빼먹으면 없는 신호를 만들어낸다.
LEAGUES = {
    "kleague1": "/south-korea/k-league-1",
    "kleague2": "/south-korea/k-league-2",
    "j1": "/japan/j1-league",
    "j2": "/japan/j2-league",
}

# robots 의 ClaudeBot Crawl-delay 는 1s 지만, 그 속도로 연속 요청하면 실제로
# 막힌다(66개 중 62개 HTTP 에러 → 잠시 후 같은 URL 이 200). 서버가 버스트를
# 싫어하는 것이므로 명시 요구치보다 넉넉히 잡고, 막히면 물러섰다 재시도한다.
CRAWL_DELAY = 3.0
RETRIES = 3
BACKOFF = 20.0             # 초 — 실패할 때마다 배로 늘린다
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_TAG = re.compile(r"<[^>]+>")
# 경기 페이지의 팀 블록: ... xG 1.70 1.72 1.69 xGA 1.37 1.37 1.37
#                            (전체 홈  원정)     (전체 홈  원정)
_XG = re.compile(
    r"xG\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+xGA\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"
)

FORBIDDEN = ("/c-dl.php", "/api/club", "/api/team", "/api/match", "/matches?")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en"})
    return s


def _get(s: requests.Session, url: str) -> str:
    """robots 가 금지한 경로는 요청 자체를 만들지 않는다. 막히면 물러섰다 재시도."""
    if any(f in url for f in FORBIDDEN):
        raise PermissionError(f"robots.txt Disallow 경로: {url}")

    wait = BACKOFF
    for attempt in range(RETRIES):
        r = s.get(url, timeout=30)
        if r.ok:
            time.sleep(CRAWL_DELAY)
            return r.text
        if attempt == RETRIES - 1:
            r.raise_for_status()
        print(f"      {r.status_code} — {wait:.0f}s 대기 후 재시도")
        time.sleep(wait)
        wait *= 2
    raise RuntimeError("unreachable")


def _text(html: str) -> str:
    return " ".join(H.unescape(_TAG.sub(" ", html)).split())


# 일정 페이지의 경기 블록. `<ul class='match row cf z8436148'>` 하나가 편성 경기
# 하나이고, 안에 data-time(킥오프 유닉스 시각)과 h2h 링크가 같이 들어 있다.
_BLOCK = re.compile(r"<ul class='match row[^']*'.*?</ul>", re.S)
_TIME = re.compile(r"data-time='(\d+)'")
_H2H = re.compile(r"href='([^']*h2h-stats)'")


def fixture_links(s: requests.Session, league_path: str,
                  horizon_days: int = 14) -> list[str]:
    """다가오는 **편성 경기**의 페이지 URL 만, 킥오프 이른 순으로.

    ⚠️ 전에는 페이지의 h2h 링크를 전부(66개) 긁었다. 그런데 편성되지 않은 팀
    조합은 일반 H2H 페이지로 빠져 xG 블록이 없다 — 60요청 중 56실패가 그것이다.
    낭비이기도 하고 429 를 자초하는 원인이기도 했다.

    일정 페이지에는 경기별로 킥오프 시각이 붙어 있으므로, 가까운 경기만 고른다.
    한 라운드면 리그의 모든 팀이 한 번씩 나오므로 팀 커버리지도 충분하다.
    """
    html = _get(s, BASE + league_path + "/fixtures")
    now = time.time()
    out: list[tuple[int, str]] = []
    seen: set[str] = set()
    for blk in _BLOCK.findall(html):
        t, h = _TIME.search(blk), _H2H.search(blk)
        if not (t and h):
            continue
        ts, href = int(t.group(1)), h.group(1).split("#")[0]
        if ts < now or ts > now + horizon_days * 86400:
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append((ts, href))
    out.sort()
    return [h for _, h in out]


def parse_match(html: str) -> dict | None:
    """경기 페이지에서 양 팀의 xG/xGA(전체·홈·원정)를 뽑는다.

    페이지에는 홈팀 블록, 원정팀 블록 순으로 두 번 등장한다.
    """
    txt = _text(html)
    hits = _XG.findall(txt)
    if len(hits) < 2:
        return None

    # 팀명은 <title> 에서 — 본문에는 `var page_form = 'overall'` 같은 JS 가 섞인다
    title = re.search(r"<title>\s*(.+?)\s+vs\s+(.+?)\s+Stats[,\s]", html, re.S)
    if not title:
        return None

    def blk(h):
        return {
            "xg_overall": float(h[0]), "xg_home": float(h[1]), "xg_away": float(h[2]),
            "xga_overall": float(h[3]), "xga_home": float(h[4]), "xga_away": float(h[5]),
        }

    return {
        "home_team": title.group(1).strip(),
        "away_team": title.group(2).strip(),
        "home": blk(hits[0]),
        "away": blk(hits[1]),
    }


def collect(league: str = "kleague1", limit: int | None = None) -> int:
    """오늘자 스냅샷을 append-only 로 적재. 같은 날 재실행해도 덮어쓰지 않는다.

    멱등 검사는 시작 시점에 한 번만 파일을 읽으므로, 두 프로세스가 겹쳐 돌면
    둘 다 "없다"고 판단해 같은 레코드를 두 번 쓴다. 정기 수집기라 실제로 겹쳤다.
    → 락으로 막는다.
    """
    lock = OUT.with_suffix(".lock")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - lock.stat().st_mtime
        if age < 3600:
            print(f"이미 수집 중입니다 ({age/60:.0f}분 전 시작). 중복 실행 방지.")
            return 1
        print(f"오래된 락({age/3600:.1f}시간) 무시하고 진행")
        lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    try:
        return _collect(league, limit)
    finally:
        lock.unlink(missing_ok=True)


def _collect(league: str, limit: int | None) -> int:
    s = _session()
    path = LEAGUES[league]
    try:
        links = fixture_links(s, path)
    except Exception as e:
        print(f"일정 페이지 실패: {type(e).__name__}: {e}")
        return 1

    if limit:
        links = links[:limit]
    print(f"{league}: 경기 페이지 {len(links)}개 (crawl-delay {CRAWL_DELAY}s)")

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    day = stamp[:10]

    # 오늘 이미 찍은 팀은 건너뛴다 — 멱등
    done: set[tuple] = set()
    db = RuntimeDatabase() if database_enabled() else None
    if db:
        existing = db.events("xg_snapshots")
    elif OUT.exists():
        existing = []
        for ln in OUT.read_text(encoding="utf-8").splitlines():
            try:
                existing.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    else:
        existing = []
    for r in existing:
            if r.get("snapshot_date") == day and r.get("league") == league:
                done.add((league, r["home_team"]))
                done.add((league, r["away_team"]))

    ok = fail = 0
    fh = None if db else OUT.open("a", encoding="utf-8")
    try:
        for i, href in enumerate(links, 1):
            # 이 페이지의 두 팀이 URL 슬러그에 들어 있다. 둘 다 오늘 이미 찍었으면
            # 요청 자체를 건너뛴다 — 66페이지가 실제로는 6~7요청으로 줄고,
            # 그래야 429 를 자초하지 않는다.
            if _covered(done, league, href.rsplit("/", 1)[-1]):
                continue
            try:
                rec = parse_match(_get(s, BASE + href))
            except Exception as e:
                fail += 1
                print(f"  [{i}] 실패 {type(e).__name__} {href[-45:]}")
                if fail >= 5:
                    print("  연속 실패 — 중단(레이트리밋 추정). 내일 다시 찍는다.")
                    break
                continue
            if rec is None:
                fail += 1
                continue
            if (league, rec["home_team"]) in done and (league, rec["away_team"]) in done:
                continue
            done.add((league, rec["home_team"]))
            done.add((league, rec["away_team"]))
            rec.update(league=league, snapshot_at=stamp, snapshot_date=day, url=href)
            if db:
                db.append_events("xg_snapshots", [rec], observed_at_key="snapshot_at")
            else:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()          # 중간에 죽어도 지금까지 건 남는다
            ok += 1
            print(f"  [{i}] {rec['home_team'][:20]} / {rec['away_team'][:20]}"
                  f"  (팀 {len(done)})")
    finally:
        if fh:
            fh.close()

    print(f"\n적재 {ok}경기 · 팀 {len({t for _, t in done})} · 실패 {fail}  →  {OUT}")
    return 0


def _slug_seen(team: str, slug: str) -> bool:
    """팀명이 URL 슬러그에 들어 있는지 — 느슨한 대조."""
    key = re.sub(r"[^a-z]", "", team.lower())[:6]
    return bool(key) and key in slug.replace("-", "")


def _covered(done: set[tuple], league: str, slug: str) -> bool:
    teams = [t for lg, t in done if lg == league]
    return sum(_slug_seen(t, slug) for t in teams) >= 2


def main() -> int:
    args = sys.argv[1:]
    league = args[0] if args and args[0] in LEAGUES else "kleague1"
    limit = int(args[1]) if len(args) > 1 else None
    return collect(league, limit)


if __name__ == "__main__":
    raise SystemExit(main())
