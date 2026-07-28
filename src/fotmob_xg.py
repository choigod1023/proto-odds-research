"""FotMob 경기별 xG 이력 — 기다릴 필요가 없었다.

왜 이걸 새로 만들었나
---------------------
`xG확보.md` 에서 "FootyStats 는 시즌 누적만 주므로 과거는 누수라 못 쓴다,
매일 스냅샷을 찍어 전진 누적하자" 로 결론냈다. 그 계산대로면 검증에 필요한
441경기가 K리그1 만으로 **2027년 8월**, 네 리그를 합쳐도 2026년 11월이었다.

**한 경로에 너무 빨리 못 박은 결론이었다.** FotMob 은 **경기별 xG 를 과거까지**
준다. 스냅샷을 기다릴 이유가 없다.

무엇을 주는가 (FootyStats 보다 낫다)
------------------------------------
    expected_goals              경기별 홈/원정 xG
    expected_goals_non_penalty  npxG — 페널티 제외, 문헌 표준
    expected_goals_on_target    xGOT — 슛의 질까지
    expected_goals_open_play / _set_play

전·후반 분리도 있고 시즌 2023~2026 이 전부 남아 있다.

접근 정책 (지킨 선)
-------------------
FotMob robots.txt 는 `User-agent: * / Disallow: /api/*` 다. xG 가 들어 있는
`/api/data/matchDetails` 는 **크롤러에게 금지**돼 있다(Googlebot 등만 허용).
그래서 **이 수집기는 API 를 쓰지 않는다.**

대신 `Allow: /` 인 **경기 페이지 HTML** 을 받는다. Next.js 앱이라 페이지 안에
`__NEXT_DATA__` 로 같은 데이터가 통째로 박혀 있다 — 허용된 경로에서 합법적으로
같은 것을 얻는다. FootyStats 의 `/c-dl.php` 를 건드리지 않은 것과 같은 원칙이다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "fotmob_xg.jsonl"

BASE = "https://www.fotmob.com"
# FotMob 리그 ID. J리그는 사용자가 예고한 다음 대상이다.
LEAGUES = {
    "kleague1": (9080, "k-league-1"),
    "kleague2": (9081, "k-league-2"),
    "j1": (9074, "j1-league"),
    "j2": (9075, "j2-league"),
}

DELAY = 2.5
RETRIES = 3
BACKOFF = 20.0
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# robots 가 막은 경로 — 요청 자체를 만들지 않는다
FORBIDDEN = ("/api/", "/auth/", "/info", "/health")

_NEXT = re.compile(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# 뽑을 지표. npxG·xGOT 가 문헌이 실제로 쓰는 것들이다.
WANT = {
    "expected_goals": "xg",
    "expected_goals_non_penalty": "npxg",
    "expected_goals_on_target": "xgot",
    "expected_goals_open_play": "xg_open",
    "expected_goals_set_play": "xg_set",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en"})
    return s


def _get(s: requests.Session, url: str) -> str:
    if any(f in url for f in FORBIDDEN):
        raise PermissionError(f"robots.txt Disallow 경로: {url}")
    wait = BACKOFF
    for attempt in range(RETRIES):
        r = s.get(url, timeout=40)
        if r.ok:
            time.sleep(DELAY)
            return r.text
        if attempt == RETRIES - 1:
            r.raise_for_status()
        print(f"      {r.status_code} — {wait:.0f}s 대기 후 재시도", flush=True)
        time.sleep(wait)
        wait *= 2
    raise RuntimeError("unreachable")


def _next_data(html: str) -> dict:
    m = _NEXT.search(html)
    if not m:
        raise ValueError("__NEXT_DATA__ 없음")
    return json.loads(m.group(1))


def fixtures(s: requests.Session, league: str, season: str | None = None) -> list[dict]:
    """리그 경기 목록 — 종료된 경기만. 리그 페이지는 robots 허용 경로다."""
    lid, slug = LEAGUES[league]
    url = f"{BASE}/leagues/{lid}/matches/{slug}"
    if season:
        url += f"?season={season}"
    nd = _next_data(_get(s, url))
    props = nd.get("props", {}).get("pageProps", {})

    out, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            if o.get("pageUrl", "").startswith("/matches/") and o.get("id"):
                st = o.get("status") or {}
                if st.get("finished") and not st.get("cancelled"):
                    if o["id"] not in seen:
                        seen.add(o["id"])
                        out.append({"id": str(o["id"]), "url": o["pageUrl"].split("#")[0],
                                    "utc": st.get("utcTime")})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(props)
    out.sort(key=lambda r: r["utc"] or "")
    return out


def parse_match(html: str) -> dict | None:
    """경기 페이지에서 팀 단위 xG 계열 지표를 뽑는다."""
    nd = _next_data(html)
    p = nd.get("props", {}).get("pageProps", {})
    g = p.get("general") or {}
    if not g.get("matchId"):
        return None

    vals: dict[str, list] = {}

    def walk(o):
        if isinstance(o, dict):
            k = o.get("key")
            if k in WANT and o.get("type") == "text":
                st = o.get("stats")
                if isinstance(st, list) and len(st) == 2 and WANT[k] not in vals:
                    try:
                        vals[WANT[k]] = [float(st[0]), float(st[1])]
                    except (TypeError, ValueError):
                        pass
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    # 전반/후반이 아니라 **경기 전체(All)** 만 본다. WANT 별로 최초 1회만 담는다.
    # 통계가 통째로 없는 경기가 있다(중계 미비 등) — 각 단계가 None 일 수 있으므로
    # `or {}` 로 내려간다. 없으면 조용히 건너뛴다.
    node = p.get("content") or {}
    for key in ("stats", "Periods", "All"):
        node = (node or {}).get(key) or {}
    walk(node)
    if "xg" not in vals:
        return None

    ht, at = g.get("homeTeam") or {}, g.get("awayTeam") or {}
    rec = {
        "match_id": str(g["matchId"]),
        "utc": g.get("matchTimeUTCDate"),
        "league_name": g.get("leagueName"),
        "home_team": ht.get("name"), "away_team": at.get("name"),
    }
    for name, (h, a) in vals.items():
        rec[f"h_{name}"], rec[f"a_{name}"] = h, a
    return rec


def collect(league: str, season: str | None = None, limit: int | None = None) -> int:
    lock = OUT.with_suffix(".lock")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - lock.stat().st_mtime
        if age < 3600:
            print(f"이미 수집 중입니다 ({age/60:.0f}분 전 시작).")
            return 1
        lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    try:
        return _collect(league, season, limit)
    finally:
        lock.unlink(missing_ok=True)


def _collect(league: str, season: str | None, limit: int | None) -> int:
    s = _session()
    try:
        fx = fixtures(s, league, season)
    except Exception as e:                                # noqa: BLE001
        print(f"일정 실패: {type(e).__name__}: {e}")
        return 1

    have = set()
    if OUT.exists():
        for ln in OUT.read_text(encoding="utf-8").splitlines():
            try:
                have.add(json.loads(ln)["match_id"])
            except (json.JSONDecodeError, KeyError):
                pass

    todo = [f for f in fx if f["id"] not in have]
    if limit:
        todo = todo[:limit]
    print(f"{league}{'/' + season if season else ''}: 종료 경기 {len(fx)} · "
          f"기수집 {len(fx) - len([f for f in fx if f['id'] not in have])} · "
          f"수집 대상 {len(todo)}", flush=True)

    # 두 가지를 구분한다.
    #   fail  — 요청이 깨진 것(레이트리밋 등). 쌓이면 중단해야 한다.
    #   noxg  — 그 경기에 xG 가 아예 없는 것(중계 데이터 미비). 정상이며 중단 사유가
    #           아니다. 둘을 섞어 세면 멀쩡한 수집을 스스로 끊는다.
    ok = fail = noxg = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for i, f in enumerate(todo, 1):
            try:
                rec = parse_match(_get(s, BASE + f["url"]))
            except Exception as e:                        # noqa: BLE001
                fail += 1
                print(f"  [{i}/{len(todo)}] 실패 {type(e).__name__} {f['url'][-40:]}",
                      flush=True)
                if fail >= 8:
                    print("  연속 실패 — 중단. 다음 실행에서 이어서 받는다.")
                    break
                continue
            if rec is None:
                noxg += 1
                continue
            rec["league"] = league
            rec["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            ok += 1
            if ok % 20 == 0:
                print(f"  [{i}/{len(todo)}] 적재 {ok} · xG없음 {noxg} · 실패 {fail}", flush=True)

    cov = ok / max(ok + noxg, 1)
    print(f"\n적재 {ok} · xG없음 {noxg} (커버리지 {cov:.0%}) · 실패 {fail}  →  {OUT}")
    return 0


def main() -> int:
    a = sys.argv[1:]
    league = a[0] if a and a[0] in LEAGUES else "kleague1"
    season = a[1] if len(a) > 1 and a[1] != "-" else None
    limit = int(a[2]) if len(a) > 2 else None
    return collect(league, season, limit)


if __name__ == "__main__":
    raise SystemExit(main())
