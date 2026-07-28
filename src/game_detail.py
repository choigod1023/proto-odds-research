"""경기 상세 수집 — 투수 개인기록(야구) · 라인업(축구).

왜 필요한가
------------
· 야구: `pitcher_impact.py` 는 "그 투수 등판 경기에서 **팀이** 내준 점수"를 대리지표로 썼다.
  거기엔 불펜 실점이 섞여 선발을 제대로 못 잰다. 3개 리그에서 효과가 재현되지 않은 것도
  지표가 거칠어서일 수 있다. → **자책점·이닝**으로 교체한다.
· 축구: 라인업은 경기 1시간 전에야 공개된다. 배당이 굳는 것보다 훨씬 늦으므로
  **정보 시차가 가장 클 후보**다. 그런데 스케줄 API 에는 없다.

엔드포인트 (2026-07-27 확인)
    야구  GET /schedule/games/{gameId}/record
          → result.recordData.pitchersBoxscore.{home,away}[]
            inn(이닝) er(자책점) r(실점) kk(삼진) bb(볼넷) hr era pcode name
    축구  GET /schedule/games/{gameId}/lineup
          → result.lineUpData.lineup.{home,away}.{players, formation, row}

⚠️ 경기당 1요청이라 비용이 크다. 캐시를 두고 이미 받은 경기는 건너뛴다.
   비상업 연구 목적, 요청 간격 준수.

사용:
    python src/game_detail.py baseball kbo 2026     # KBO 2026 투수기록
    python src/game_detail.py soccer kleague        # K리그 라인업
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "detail"
API = "https://api-gw.sports.naver.com"
GAP = 1.0

# 종목 → (upperCategoryId, categoryId) 목록
CATS = {
    "kbo": ("kbaseball", "kbo"), "mlb": ("wbaseball", "mlb"),
    "npb": ("wbaseball", "npb"),
    "kleague": ("kfootball", "kleague"), "mls": ("wfootball", "mls"),
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json", "Referer": "https://m.sports.naver.com/"})
    return s


def list_games(sess, league: str, y0: int, y1: int) -> list[dict]:
    up, cid = CATS[league]
    out, d = [], date(y0, 1, 1)
    end = min(date(y1, 12, 31), date.today())
    while d <= end:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        hi = min(nxt - timedelta(days=1), end)
        try:
            r = sess.get(f"{API}/schedule/games", params={
                "fields": "basic,statusNum", "upperCategoryId": up,
                "categoryId": cid, "fromDate": d.isoformat(),
                "toDate": hi.isoformat(), "size": 500}, timeout=25)
            gs = r.json().get("result", {}).get("games", [])
            out += [g for g in gs if not g.get("cancel")]
        except Exception as e:                       # noqa: BLE001
            print(f"  일정 {d}~{hi} 오류 {type(e).__name__}", flush=True)
        d = nxt
        time.sleep(0.6)
    return out


def parse_baseball(res: dict) -> dict | None:
    rd = (res or {}).get("recordData") or {}
    pb = rd.get("pitchersBoxscore") or {}
    if not pb:
        return None
    keep = ("pcode", "name", "inn", "er", "r", "kk", "bb", "hit", "hr", "era",
            "bf", "wls")
    out = {}
    for side in ("home", "away"):
        rows = pb.get(side) or []
        out[side] = [{k: p.get(k) for k in keep} for p in rows]
    return out or None


# 이닝별 타격 결과 문자열에서 안타 종류를 읽는다.
#
# ⚠️ 표기 규칙을 정확히 잡아야 한다. 실제 어휘를 표본으로 확인한 결과:
#
#   방향이 **1글자가 아니라 2글자**인 경우가 있다
#       우2(우익 2루타) · **우중2**(우중간 2루타) · 좌중안(좌중간 안타)
#   숫자가 **앞**에 오면 그건 수비 위치다
#       2땅(2루수 땅볼) · 3비(3루수 뜬공) · 2직(2루수 직선타) — 전부 아웃
#
# 그래서 접두사가 아니라 **접미사**로 판정한다. 앞자리로 잡으면 우중2/좌중2 를
# 통째로 놓쳐 2루타의 40% 가 사라진다(단타로 오분류 → wOBA 가 장타력을 잃는다).
_HIT_SUFFIX = (("홈", "hr"), ("3", "t"), ("2", "d"), ("안", "s"))


def _batter_hits(rec: dict) -> dict:
    """이닝 칸을 훑어 단타·2루타·3루타·홈런을 센다.

    ⚠️ 한 이닝에 두 번 이상 타석에 서면 **한 칸에 '/' 로 이어 적는다**:
       '좌중안/2땅' = 좌중간 안타 + 2루수 땅볼, '우2/4구' = 2루타 + 볼넷.
    칸 전체로 판정하면 마지막 것만 보게 돼 안타를 흘린다.
    """
    out = {"s": 0, "d": 0, "t": 0, "hr": 0}
    for k, v in rec.items():
        if not (k.startswith("inn") and isinstance(v, str) and v):
            continue
        for pa in v.split("/"):
            pa = pa.strip()
            if not pa:
                continue
            for suf, key in _HIT_SUFFIX:
                if pa.endswith(suf):
                    out[key] += 1
                    break
    return out


def parse_baseball_batters(res: dict) -> dict | None:
    """타자 박스스코어 — **투수의 FIP 에 대응하는 타선의 과정 지표** 재료.

    지금 모델은 투수만 정교하다(FIP·xFIP). 타선은 '팀 득점 평균'뿐인데
    그건 결과 지표라 운이 크게 섞인다. ERA 를 쓰던 것과 같은 실수다.

    박스스코어에서 뽑을 수 있는 것:
      · 과정 — wOBA(장타 가중), K%·BB%(가장 안정적인 타자 지표)
      · 운   — BABIP = (안타−홈런)/(타수−삼진−홈런). 야구의 대표적 운 지표.

    구장도 함께 담는다. 파크팩터는 언더오버 격차에 직결된다.
    """
    rd = (res or {}).get("recordData") or {}
    bb = rd.get("battersBoxscore") or {}
    if not bb.get("home") or not bb.get("away"):
        return None

    out = {"stadium": (rd.get("gameInfo") or {}).get("stadium")}
    for side in ("home", "away"):
        agg = {"ab": 0, "hit": 0, "hr": 0, "bb": 0, "kk": 0,
               "run": 0, "rbi": 0, "s": 0, "d": 0, "t": 0, "hr_parsed": 0}
        for p in bb.get(side) or []:
            for k in ("ab", "hit", "hr", "bb", "kk", "run", "rbi"):
                agg[k] += int(p.get(k) or 0)
            for k, v in _batter_hits(p).items():
                agg["hr_parsed" if k == "hr" else k] += v
        # 검산용: 파싱한 안타 합이 박스스코어 hit 와 맞는지 나중에 확인할 수 있게
        # 둘 다 남긴다. 어긋나면 표기 규칙을 놓친 것이다.
        agg["hits_parsed"] = agg["s"] + agg["d"] + agg["t"] + agg["hr_parsed"]
        out[side] = agg
    return out


def parse_baseball_batters_indiv(res: dict) -> dict | None:
    """타자 박스스코어를 **선수 단위 그대로** 남긴다.

    왜 따로 만드나
    --------------
    `parse_baseball_batters` 는 선수 행을 팀 합계로 접어 버린다. 그래서
    `batter_process.py` 의 "타선은 죽었다"는 판정은 **팀 시즌 평균**으로 낸 것이고,
    **그날 실제로 나온 9명이 누구인지는 한 번도 보지 않았다.**

    투수는 개인 단위(FIP·xFIP)로 재서 통했다(+0.006). 타선만 팀 단위로 재고
    죽었다고 결론 낸 셈이라, 비교가 공정하지 않다. 이 파서는 그 구멍을 메운다.

    남기는 것
    ---------
    · `playerCode`·`name` — 선수 식별. walk-forward 개인 성적 누적의 키
    · **`batOrder`** — 그날의 타순. 라인업 구성 자체가 경기마다 바뀌는 정보다
    · `pos` — 포지션(대타·대주자 구분에 필요)
    · ab·hit·hr·bb·kk·run·rbi·sb + 이닝 칸에서 파싱한 단타/2루타/3루타
    """
    rd = (res or {}).get("recordData") or {}
    bb = rd.get("battersBoxscore") or {}
    if not bb.get("home") or not bb.get("away"):
        return None

    gi = rd.get("gameInfo") or {}
    out = {"stadium": gi.get("stadium"), "gtime": gi.get("gtime")}
    for side in ("home", "away"):
        rows = []
        for p in bb.get(side) or []:
            rec = {"code": str(p.get("playerCode") or ""),
                   "name": p.get("name"),
                   "order": p.get("batOrder"),
                   "pos": p.get("pos")}
            for k in ("ab", "hit", "hr", "bb", "kk", "run", "rbi", "sb"):
                rec[k] = int(p.get(k) or 0)
            hits = _batter_hits(p)
            rec["s"], rec["d"], rec["t"] = hits["s"], hits["d"], hits["t"]
            rows.append(rec)
        out[side] = rows
    return out


def parse_soccer_shots(res: dict) -> dict | None:
    """축구 슈팅 통계 — **xG 의 대용품**.

    네이버에 xG 는 없지만 선수별 `shots`·`shotsOnGoal` 이 있다.
    실제 골은 운이 크게 섞이므로, 문헌이 xG 를 쓰는 이유와 같은 논리로
    **유효슈팅이 골보다 나은 과정 지표**다.
    """
    rd = (res or {}).get("recordData") or {}
    out = {}
    for side, key in (("home", "homePlayerStats"), ("away", "awayPlayerStats")):
        pl = rd.get(key) or []
        if not pl:
            return None
        out[side] = {
            "shots": sum(float(p.get("shots") or 0) for p in pl),
            "sog": sum(float(p.get("shotsOnGoal") or 0) for p in pl),
            "goals": sum(float(p.get("goals") or 0) for p in pl),
            "fouls": sum(float(p.get("foulsCommitted") or 0) for p in pl),
            "n_players": len(pl),
        }
    return out or None


def parse_soccer(res: dict) -> dict | None:
    ld = (res or {}).get("lineUpData") or {}
    lu = ld.get("lineup") or {}
    if not lu:
        return None
    out = {}
    for side in ("home", "away"):
        d = lu.get(side) or {}
        players = d.get("players") or []
        flat = []
        for row in players:
            items = row if isinstance(row, list) else [row]
            for p in items:
                if isinstance(p, dict):
                    # 실제 필드명: name / pos / shirtNumber / positionOrder
                    flat.append({k: p.get(k) for k in
                                 ("playerId", "name", "pos", "shirtNumber",
                                  "positionOrder", "changed", "goal",
                                  "assists", "yellowCardCnt", "redCardCnt")})
        out[side] = {"formation": d.get("formation"), "players": flat}
    subs = ld.get("substitution") or {}
    out["substitution"] = {s: subs.get(s) for s in ("home", "away")}
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    kind, league = argv[1], argv[2]
    yrs = [int(a) for a in argv[3:] if a.isdigit()]
    y0 = yrs[0] if yrs else 2023
    y1 = yrs[1] if len(yrs) > 1 else date.today().year
    # kind: baseball(투수기록) / batters(야구 타자기록) / lineup(축구 라인업)
    #       / shots(축구 슈팅)
    path = "lineup" if kind == "lineup" else "record"
    parse = {"baseball": parse_baseball, "batters": parse_baseball_batters,
             "batters_indiv": parse_baseball_batters_indiv,
             "lineup": parse_soccer, "shots": parse_soccer_shots}[kind]

    RAW.mkdir(parents=True, exist_ok=True)
    out_file = RAW / f"{league}_{kind}_{y0}_{y1}.json"
    cache = json.loads(out_file.read_text(encoding="utf-8")) if out_file.exists() else {}
    print(f"{league.upper()} {kind} {y0}~{y1} · 기존 캐시 {len(cache)}경기")

    sess = _session()
    games = list_games(sess, league, y0, y1)
    todo = [g for g in games if g.get("gameId") not in cache
            and g.get("statusCode") != "BEFORE"]
    print(f"일정 {len(games)}경기 · 수집 대상 {len(todo)}경기 "
          f"(예상 {len(todo)*GAP/60:.0f}분)", flush=True)

    got = 0
    for i, g in enumerate(todo, 1):
        gid = g["gameId"]
        try:
            r = sess.get(f"{API}/schedule/games/{gid}/{path}", timeout=20)
            if r.status_code == 200:
                d = parse(r.json().get("result"))
                if d:
                    cache[gid] = {"gameId": gid, "date": g.get("gameDate"),
                                  "home": g.get("homeTeamName"),
                                  "away": g.get("awayTeamName"),
                                  "home_score": g.get("homeTeamScore"),
                                  "away_score": g.get("awayTeamScore"),
                                  "data": d}
                    got += 1
        except Exception as e:                       # noqa: BLE001
            print(f"  {gid} 오류 {type(e).__name__}", flush=True)
            time.sleep(2)
        if i % 100 == 0:
            out_file.write_text(json.dumps(cache, ensure_ascii=False),
                                encoding="utf-8")
            print(f"  {i}/{len(todo)} · 확보 {got}", flush=True)
        time.sleep(GAP)

    out_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"\n완료 · 총 {len(cache)}경기 (신규 {got}) → {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
