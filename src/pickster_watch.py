"""공개 MLB 픽스터 관측기.

TailSlips 공개 HTML에 표시되는 X(구 Twitter) 픽을 저빈도로 관측한다. 이 파일은
사이트의 비공개 ``/api/`` 경로를 호출하지 않는다. 첫 실행 때 이미 끝난 픽은
``baseline``으로 표시하며, 이후 **경기 전에 처음 본 픽**만 성과 검증에 쓴다.

사용::

    python src/pickster_watch.py                  # 공개 페이지 1회 관측
    python src/pickster_watch.py --loop 900       # 15분마다
    python src/pickster_watch.py --selftest

원본 HTML은 저장하지 않는다. 화면에서 읽힌 정규화 레코드와 변경 이벤트만 JSONL로
보존한다. 공개 페이지 구조가 바뀌면 조용히 0건으로 성공하지 않고 예외를 낸다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "picksters"
STATE = OUT / "_state.json"
LEADERBOARD_LOG = OUT / "tailslips_leaderboard.jsonl"
PICK_LOG = OUT / "tailslips_pick_events.jsonl"
CROWD_LOG = OUT / "tailslips_crowd.jsonl"

BASE_URL = "https://tailslips.com/"
SLATE_URL = "https://tailslips.com/slate"
SOURCE = "tailslips_public_html"

_NUM = r"[+\-\N{MINUS SIGN}]?\d+(?:\.\d+)?"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slate_date(observed_at: str) -> str:
    """MLB 슬레이트 날짜는 TailSlips 기준 미국 동부 달력일로 고정한다."""
    dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def _number(value: str | None) -> float | None:
    if not value:
        return None
    m = re.search(_NUM, value.replace(",", ""))
    if not m:
        return None
    return float(m.group().replace("\N{MINUS SIGN}", "-"))


def _integer(value: str | None) -> int | None:
    x = _number(value)
    return int(x) if x is not None else None


def _pct(value: str | None) -> float | None:
    return _number(value)


def _identity(link: Tag) -> tuple[str, str, str]:
    """capper 링크에서 (slug, 표시명, @handle)을 분리한다."""
    href = str(link.get("href") or "")
    slug = href.rstrip("/").split("/")[-1]
    text = link.get_text(" ", strip=True)
    hm = re.search(r"@([A-Za-z0-9_]+)", text)
    handle = hm.group(1) if hm else slug
    name = text[: hm.start()].strip() if hm else text
    return slug, name, handle


def _market_counts(text: str) -> dict[str, int]:
    patterns = {
        "moneyline": r"\bML\b|Moneyline", "spread": r"\bSpread\b",
        "total": r"\bTotal\b|\b[OU]\d", "prop": r"\bProp\b",
        "parlay": r"\bParlay\b",
    }
    return {name: len(re.findall(pattern, text, re.I)) for name, pattern in patterns.items()
            if re.search(pattern, text, re.I)}


def parse_leaderboard(html: str, observed_at: str | None = None) -> list[dict]:
    """최근 30일 리더보드의 데스크톱 행과 상위 3개 카드를 정규화한다."""
    observed_at = observed_at or _now()
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []

    # 상위 3명은 표가 아니라 카드로 렌더된다.
    for card in soup.select("div.relative.h-full.flex.flex-col.rounded-2xl"):
        children = card.find_all(recursive=False)
        link = card.select_one('a[href^="/cappers/"]')
        if not link:
            continue
        text = card.get_text(" ", strip=True)
        perf = re.search(
            rf"Net profit\s+({_NUM})\s+({_NUM})% ROI\s*·\s*"
            rf"({_NUM})% win\s*·\s*([\d,]+) picks", text, re.I,
        )
        if not perf:
            continue
        slug, name, handle = _identity(link)
        rank_el = card.select_one("span")
        rank = _integer(rank_el.get_text(" ", strip=True) if rank_el else "")
        trait = None
        for child in children:
            ct = child.get_text(" ", strip=True)
            if "·" in ct and re.search(r"\d+%$", ct) and "ROI" not in ct:
                trait = ct.split("·", 1)[0].strip()
                break
        followers = None
        fm = re.search(r"([\d.]+)([KMB]?) followers", text, re.I)
        if fm:
            mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
            followers = int(float(fm.group(1)) * mult[fm.group(2).upper()])
        live = re.search(r"(\d+) live", text, re.I)
        out.append({
            "observed_at": observed_at, "source": SOURCE, "window": "last_30_days",
            "rank": rank, "slug": slug, "name": name, "handle": handle,
            "n_picks": int(perf.group(4).replace(",", "")),
            "win_rate_pct": _pct(perf.group(3)), "net_units": _number(perf.group(1)),
            "roi_pct": _pct(perf.group(2)), "trait": trait,
            "recent_market_counts": _market_counts(text),
            "offers_paid_picks": "offers paid picks" in text.lower(),
            "deleted_count": _integer(re.search(r"×\s*(\d+) deleted", text).group(1))
            if re.search(r"×\s*(\d+) deleted", text) else 0,
            "live_count": int(live.group(1)) if live else 0,
            "followers": followers,
        })

    # 4위 이하는 표 행. 첫 행은 헤더라 rank가 숫자가 아니다.
    for row in soup.select("div.hidden.sm\\:grid"):
        children = row.find_all(recursive=False)
        if len(children) < 7:
            continue
        rank = _integer(children[0].get_text(" ", strip=True))
        link = row.select_one('a[href^="/cappers/"]')
        if rank is None or not link:
            continue
        slug, name, handle = _identity(link)
        identity_text = link.get_text(" ", strip=True)
        dm = re.search(r"×\s*(\d+) deleted", identity_text)
        recent_text = children[2].get_text(" ", strip=True)
        recent_counts = _market_counts(recent_text)
        inferred_trait = max(recent_counts, key=recent_counts.get) if recent_counts else None
        out.append({
            "observed_at": observed_at, "source": SOURCE, "window": "last_30_days",
            "rank": rank, "slug": slug, "name": name, "handle": handle,
            "n_picks": _integer(children[3].get_text(" ", strip=True)),
            "win_rate_pct": _pct(children[4].get_text(" ", strip=True)),
            "net_units": _number(children[5].get_text(" ", strip=True)),
            "roi_pct": _pct(children[6].get_text(" ", strip=True)),
            "trait": inferred_trait, "recent_market_counts": recent_counts,
            "offers_paid_picks": "offers paid picks" in identity_text.lower(),
            "deleted_count": int(dm.group(1)) if dm else 0,
            "live_count": 0, "followers": None,
        })

    # 반응형 마크업의 중복이 있어도 slug당 가장 구체적인 한 행만 남긴다.
    dedup: dict[str, dict] = {}
    for row in sorted(out, key=lambda r: (r["rank"] is None, r["rank"] or 10_000)):
        if row["slug"] not in dedup or (row.get("trait") and not dedup[row["slug"]].get("trait")):
            dedup[row["slug"]] = row
    rows = sorted(dedup.values(), key=lambda r: r["rank"] or 10_000)
    if html.strip() and len(rows) < 5:
        raise ValueError(f"TailSlips 리더보드 구조 변경 의심: {len(rows)}명만 파싱")
    return rows


def _game_header(section: Tag) -> str:
    direct = section.find_all(recursive=False)
    if len(direct) >= 2:
        return direct[1].get_text(" ", strip=True)
    return section.get_text(" ", strip=True)[:160]


def _teams(header: str) -> tuple[str | None, str | None]:
    # 헤더의 투수 이름 이니셜(C., J.)은 제외하고 첫 두 구단 약어만 쓴다.
    left = header.split(" vs ", 1)[0]
    tokens = re.findall(r"\b[A-Z]{2,3}\b", left)
    excluded = {"FINAL", "TOP", "BOT", "END", "LIVE", "AM", "PM", "ET"}
    teams = [x for x in tokens if x not in excluded]
    return (teams[0], teams[1]) if len(teams) >= 2 else (None, None)


def _pick_market(selection: str) -> str:
    s = f" {selection.upper()} "
    if re.search(r"\bML\b|MONEYLINE", s):
        return "moneyline"
    if re.search(r"\b[OU]\s*\d|OVER|UNDER|TOTAL", s):
        return "total"
    if re.search(r"\s[+\-]\d+(?:\.5)?(?:\s|$)", s):
        return "spread"
    if any(x in s for x in (" HR ", " RBI", " HIT", " K ", "SO ", "BASES", "PROP")):
        return "player_prop"
    if "PARLAY" in s:
        return "parlay"
    return "other"


def _game_started(header: str) -> bool:
    """미채점(pending)과 경기 전을 혼동하지 않기 위한 보수적 상태 판정."""
    h = header.upper()
    return bool(re.search(r"\b(FINAL|TOP|BOT|MID|END|DELAY|SUSP)\b", h))


def _parse_pick_row(row: Tag, header: str, team_a: str | None,
                    team_b: str | None, observed_at: str) -> dict | None:
    link = row.select_one('a[href^="/cappers/"]')
    if not link:
        return None
    slug, name, handle = _identity(link)
    raw = " ".join(row.get_text(" ", strip=True).split())
    # 순위(#4)는 리더보드가 바뀔 때 달라지므로 pick identity에서 제외한다.
    # 링크 자체의 화면 텍스트가 앞에 한 번 더 붙는 반응형 DOM도 있어, 해당 capper
    # handle의 마지막 등장 뒤를 실제 선택 텍스트로 본다.
    handles = list(re.finditer(rf"@{re.escape(handle)}\b", raw, flags=re.I))
    clean = raw[handles[-1].end():].strip() if handles else re.sub(r"^#\d+\s+", "", raw)
    result_m = re.search(r"\s([WLPV])(?=\s|$)", clean)
    result = result_m.group(1) if result_m else None
    selection = clean[:result_m.start()].strip() if result_m else clean.strip()
    tail = clean[result_m.end():].strip() if result_m else ""
    parlay_m = re.search(r"in\s+(\d+)\s*-?leg", tail, re.I)
    net_m = re.search(rf"({_NUM})u\b", tail)
    odds_all = re.findall(r"(?<![\d.])([+\-]\d{3,4})(?![\d.])", selection)
    american_odds = int(odds_all[-1]) if odds_all else None
    slate_date = _slate_date(observed_at)
    # 점수/FINAL/TOP/BOT가 바뀌어도 같은 픽이어야 결과 이벤트가 최초 관측에 붙는다.
    # MLB는 같은 대진이 연속되므로 동부 날짜까지 포함한다.
    matchup = "-".join(sorted(x for x in (team_a, team_b) if x))
    key_text = "|".join((slate_date, matchup, slug,
                         re.sub(r"\s+", " ", selection).lower()))
    pick_id = hashlib.sha256(key_text.encode()).hexdigest()[:24]
    return {
        "pick_id": pick_id, "identity_version": 2,
        "observed_at": observed_at, "source": SOURCE,
        "slate_date": slate_date, "game_header": header, "team_a": team_a, "team_b": team_b,
        "game_started": _game_started(header),
        "slug": slug, "name": name, "handle": handle,
        "selection": selection, "market_type": _pick_market(selection),
        "american_odds": american_odds, "result": result,
        "net_units": _number(net_m.group(1)) if net_m else None,
        "is_parlay_leg": bool(parlay_m),
        "parlay_legs": int(parlay_m.group(1)) if parlay_m else None,
        "raw_text": raw,
    }


def parse_slate(html: str, observed_at: str | None = None) -> tuple[list[dict], list[dict]]:
    """슬레이트에서 픽과 경기별 공개 군중 집계를 읽는다."""
    observed_at = observed_at or _now()
    slate_date = _slate_date(observed_at)
    soup = BeautifulSoup(html, "html.parser")
    picks: list[dict] = []
    games: list[dict] = []
    sections = [s for s in soup.find_all("section") if "scroll-mt" in " ".join(s.get("class", []))]
    for section in sections:
        header = _game_header(section)
        team_a, team_b = _teams(header)
        game_picks: list[dict] = []
        for div in section.find_all("div"):
            classes = div.get("class", [])
            if "grid-cols-[auto_1fr]" not in classes:
                continue
            parsed = _parse_pick_row(div, header, team_a, team_b, observed_at)
            if parsed:
                game_picks.append(parsed)
        # 같은 반응형 행이 중복되면 한 pick_id만 유지한다.
        unique = {p["pick_id"]: p for p in game_picks}
        game_picks = list(unique.values())
        picks.extend(game_picks)
        ml = [p for p in game_picks if p["market_type"] == "moneyline" and not p["is_parlay_leg"]]
        side_counts: dict[str, int] = {}
        for p in ml:
            first = p["selection"].split()[0].upper() if p["selection"] else ""
            if first and first in {team_a, team_b}:
                side_counts[first] = side_counts.get(first, 0) + 1
        games.append({
            "observed_at": observed_at, "source": SOURCE, "game_header": header,
            "slate_date": slate_date,
            "team_a": team_a, "team_b": team_b, "n_unique_picks": len(game_picks),
            "n_straight_moneyline": len(ml), "moneyline_capper_counts": side_counts,
        })
    if html.strip() and not sections:
        raise ValueError("TailSlips 슬레이트 경기 section을 찾지 못함")
    return picks, games


def _append_jsonl(path: Path, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    from runtime_db import RuntimeDatabase, database_enabled
    if database_enabled():
        streams = {
            LEADERBOARD_LOG: "pickster_leaderboard",
            PICK_LOG: "pickster_pick_events",
            CROWD_LOG: "pickster_crowd",
        }
        stream = streams.get(path, "pickster:" + path.name)
        db = RuntimeDatabase()
        inserted = db.append_events(stream, rows)
        db.export_events(stream, path)
        return inserted
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(rows)


def _load_state() -> dict:
    from runtime_db import RuntimeDatabase, database_enabled
    if database_enabled():
        saved = RuntimeDatabase().get_document("pickster_state")
        if saved is not None:
            saved.setdefault("picks", {})
            return saved
    if not STATE.exists():
        return {"picks": {}, "leaderboard_hash": None, "crowd_hash": None}
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        state.setdefault("picks", {})
        return state
    except (OSError, json.JSONDecodeError):
        return {"picks": {}, "leaderboard_hash": None, "crowd_hash": None}


def _save_state(state: dict) -> None:
    from runtime_db import RuntimeDatabase, database_enabled
    if database_enabled():
        db = RuntimeDatabase()
        db.put_document("pickster_state", state,
                        generated_at=state.get("last_success_at"))
        db.export_document("pickster_state", STATE, indent=None)
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(STATE)


def _stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _fetch(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def collect_once(session: requests.Session, leaderboard_html: str | None = None,
                 slate_html: str | None = None) -> dict:
    observed_at = _now()
    leaderboard_html = leaderboard_html if leaderboard_html is not None else _fetch(session, BASE_URL)
    slate_html = slate_html if slate_html is not None else _fetch(session, SLATE_URL)
    leaderboard = parse_leaderboard(leaderboard_html, observed_at)
    picks, crowd = parse_slate(slate_html, observed_at)
    state = _load_state()
    first_run = not state.get("initialized_at")
    # v1은 변하는 점수 header를 ID에 넣어 pregame→FINAL 연결이 끊겼다. 아직 전향
    # 판정 완료 표본이 없을 때 발견했으므로 상태만 v2로 교체하고, 로그의 v1은 분석에서
    # 제외한다. baseline 원본은 감사 목적으로 그대로 둔다.
    if state.get("pick_identity_version") != 2:
        state["picks"] = {}
        state["pick_identity_version"] = 2

    # observed_at은 내용 해시에서 제외해야 변화가 없을 때 로그가 불어나지 않는다.
    leaderboard_content = [{k: v for k, v in r.items() if k != "observed_at"} for r in leaderboard]
    leader_hash = _stable_hash(leaderboard_content)
    n_leader = 0
    if leader_hash != state.get("leaderboard_hash"):
        n_leader = _append_jsonl(LEADERBOARD_LOG, [{
            "observed_at": observed_at, "source": SOURCE, "source_url": BASE_URL,
            "window": "last_30_days", "is_baseline": first_run, "rows": leaderboard,
        }])
        state["leaderboard_hash"] = leader_hash

    events: list[dict] = []
    seen: set[str] = set()
    for pick in picks:
        pid = pick["pick_id"]
        seen.add(pid)
        old = state["picks"].get(pid)
        # slate_date 필드 추가 자체가 477개 baseline을 변경 이벤트로 만들지 않게 한다.
        fingerprint = _stable_hash({k: v for k, v in pick.items()
                                    if k not in {"observed_at", "raw_text", "slate_date"}})
        if old is None:
            event_type = "baseline" if first_run else "first_observed"
            event = {**pick, "event_type": event_type, "is_baseline": first_run,
                     "eligible_pre_event": bool(
                         not first_run and pick["result"] is None and not pick["game_started"])}
            events.append(event)
            state["picks"][pid] = {
                "first_seen": observed_at, "last_seen": observed_at,
                "first_result": pick["result"], "last_result": pick["result"],
                "eligible_pre_event": event["eligible_pre_event"], "fingerprint": fingerprint,
            }
        else:
            old["last_seen"] = observed_at
            if fingerprint != old.get("fingerprint"):
                if old.get("last_result") is None and pick["result"] is not None:
                    event_type = "graded"
                else:
                    event_type = "changed"
                events.append({**pick, "event_type": event_type, "is_baseline": False,
                               "eligible_pre_event": bool(old.get("eligible_pre_event"))})
                old["last_result"] = pick["result"]
                old["fingerprint"] = fingerprint

    # 더 이상 보이지 않는 것을 곧바로 삭제라고 단정하지 않는다. 페이지 필터/경기 종료
    # 때문일 수 있어 상태에 마지막 관측 시각만 보존한다.
    n_events = _append_jsonl(PICK_LOG, events)

    crowd_content = [{k: v for k, v in r.items() if k != "observed_at"} for r in crowd]
    crowd_hash = _stable_hash(crowd_content)
    n_crowd = 0
    if crowd_hash != state.get("crowd_hash"):
        n_crowd = _append_jsonl(CROWD_LOG, [{
            "observed_at": observed_at, "source": SOURCE, "source_url": SLATE_URL,
            "is_baseline": first_run, "games": crowd,
        }])
        state["crowd_hash"] = crowd_hash

    state["initialized_at"] = state.get("initialized_at") or observed_at
    state["last_success_at"] = observed_at
    state["last_seen_pick_count"] = len(seen)
    state["current_pick_ids"] = sorted(seen)
    _save_state(state)
    return {
        "observed_at": observed_at, "leaderboard_cappers": len(leaderboard),
        "slate_picks": len(picks), "games": len(crowd),
        "leaderboard_snapshots_written": n_leader,
        "pick_events_written": n_events, "crowd_snapshots_written": n_crowd,
        "baseline": first_run,
    }


def _selftest() -> None:
    leaderboard = """
    <div class="relative h-full flex flex-col rounded-2xl"><span>1</span>
      <a href="/cappers/alpha">Alpha @alpha</a><div>2 live</div>
      <div>Moneyline grinder · 90%</div>
      <div>Net profit +12.5 +10.0% ROI · 55% win · 120 picks</div>
      <div>Tracked since May 2026 · 2.5K followers</div></div>
    """ + "".join(
        f'<div class="hidden sm:grid"><div>{i:02d}</div><a href="/cappers/c{i}">C{i} @c{i}</a>'
        f'<div>x</div><div>{100+i}</div><div>52%</div><div>+2.0</div><div>+2%</div></div>'
        for i in range(4, 9)
    )
    rows = parse_leaderboard(leaderboard, "2026-01-01T00:00:00+00:00")
    assert len(rows) == 6 and rows[0]["followers"] == 2500 and rows[0]["trait"] == "Moneyline grinder"

    slate = """
    <section class="relative scroll-mt-10"><div></div><div>NYY 3 FINAL BAL 1 C. A vs S. B</div><div>
      <div class="grid grid-cols-[auto_1fr]"><a href="/cappers/alpha">@alpha</a>#1 @alpha NYY ML -110 W +0.91u</div>
      <div class="grid grid-cols-[auto_1fr]"><a href="/cappers/beta">@beta</a>#2 @beta BAL ML +120 L -1u in 4-leg</div>
    </div></section>
    """
    picks, games = parse_slate(slate, "2026-01-01T00:00:00+00:00")
    assert len(picks) == 2 and games[0]["moneyline_capper_counts"] == {"NYY": 1}
    assert picks[0]["american_odds"] == -110 and picks[0]["net_units"] == .91
    assert picks[0]["slate_date"] == "2025-12-31"
    assert picks[0]["game_started"] is True
    assert picks[0]["identity_version"] == 2
    pregame = slate.replace("NYY 3 FINAL BAL 1", "NYY 7:05 PM BAL").replace(" W +0.91u", "")
    pre_picks, _ = parse_slate(pregame, "2026-01-01T00:00:00+00:00")
    assert pre_picks[0]["pick_id"] == picks[0]["pick_id"]
    assert pre_picks[0]["game_started"] is False and pre_picks[0]["result"] is None
    assert picks[1]["is_parlay_leg"] and picks[1]["parlay_legs"] == 4
    assert math.isclose(_number("\N{MINUS SIGN}1.5") or 0, -1.5)
    print("✅ pickster_watch selftest 통과")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--leaderboard-html", type=Path)
    ap.add_argument("--slate-html", type=Path)
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return 0
    session = requests.Session()
    session.headers.update({
        "User-Agent": "proto-odds-research/1.0 (public research collector)",
        "Accept": "text/html,application/xhtml+xml", "Referer": BASE_URL,
    })
    while True:
        try:
            result = collect_once(
                session,
                args.leaderboard_html.read_text(encoding="utf-8") if args.leaderboard_html else None,
                args.slate_html.read_text(encoding="utf-8") if args.slate_html else None,
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001 - 상시 수집기는 다음 주기에 복구한다.
            print(f"[pickster_watch] {type(exc).__name__}: {exc}", flush=True)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(max(args.loop, 300))


if __name__ == "__main__":
    raise SystemExit(main())
