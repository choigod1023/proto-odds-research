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
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "xg_snapshots.jsonl"

BASE = "https://footystats.org"
# 나중에 J리그를 붙일 자리 — 사용자가 예고한 다음 대상
LEAGUES = {
    "kleague1": "/south-korea/k-league-1",
    "kleague2": "/south-korea/k-league-2",
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


def fixture_links(s: requests.Session, league_path: str) -> list[str]:
    """일정 페이지에서 경기 페이지 URL 을 뽑는다(앵커 #id 는 제거해 중복 제거)."""
    html = _get(s, BASE + league_path + "/fixtures")
    hrefs = set(re.findall(r"href=['\"]([^'\"]+)", html))
    out = {h.split("#")[0] for h in hrefs if "-vs-" in h and "h2h-stats" in h}
    return sorted(out)


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
    """오늘자 스냅샷을 append-only 로 적재. 같은 날 재실행해도 덮어쓰지 않는다."""
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

    # 같은 날 이미 찍은 (리그, 홈, 원정) 은 건너뛴다 — 멱등
    seen: set[tuple] = set()
    if OUT.exists():
        for ln in OUT.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if r.get("snapshot_date") == day:
                seen.add((r.get("league"), r.get("home_team"), r.get("away_team")))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    ok = skip = fail = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for i, href in enumerate(links, 1):
            try:
                rec = parse_match(_get(s, BASE + href))
            except Exception as e:
                fail += 1
                print(f"  [{i}/{len(links)}] 실패 {type(e).__name__} {href[-45:]}")
                continue
            if rec is None:
                fail += 1
                continue
            key = (league, rec["home_team"], rec["away_team"])
            if key in seen:
                skip += 1
                continue
            seen.add(key)
            rec.update(league=league, snapshot_at=stamp, snapshot_date=day, url=href)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            ok += 1
            if ok % 10 == 0:
                print(f"  [{i}/{len(links)}] 적재 {ok} · 중복 {skip} · 실패 {fail}")

    print(f"\n적재 {ok} · 오늘 중복 {skip} · 실패 {fail}  →  {OUT}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    league = args[0] if args and args[0] in LEAGUES else "kleague1"
    limit = int(args[1]) if len(args) > 1 else None
    return collect(league, limit)


if __name__ == "__main__":
    raise SystemExit(main())
