"""Bounded, observable sports-history collection into SQLite; no CSV required.

Examples:
  python src/sports_history_collect.py collect --db data/runtime/history.sqlite3 --source mlb --since 2026-09-04 --until 2026-09-04 --limit 30
  python src/sports_history_collect.py inventory --db data/runtime/history.sqlite3
  python src/sports_history_collect.py team --db data/runtime/history.sqlite3 --provider mlb --league MLB --team-id 147
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse, urljoin, unquote

import requests

from runtime_db import RuntimeDatabase
from sports_history import SportsHistoryStore, canonical_json, utc_stamp


SEASON_CATEGORIES = {
    "NBA": ("nba", "bk"), "KBL": ("kbl", "bk"), "WKBL": ("wkbl", "bk"),
    "KOVO남": ("kovo", "vl"), "KOVO여": ("wkovo", "vl"),
}
SOURCE_GUIDE = {
    "mlb": "MLB 공식 일정·박스스코어 / 경기별 runs / --since --until",
    "naver:KBO": "KBO 최근 종료 경기 / runs / xwOBA 제공으로 간주하지 않음",
    "naver:NPB": "NPB 최근 종료 경기 / runs / xwOBA 제공으로 간주하지 않음",
    "naver:NBA": "NBA 최근 종료 경기 / points / 비시즌이면 날짜 구간에 경기가 없을 수 있음",
    "naver:KBL": "KBL 최근 종료 경기 / points",
    "naver:WKBL": "WKBL 최근 종료 경기 / points",
    "naver:KOVO남": "KOVO 남자 종료 경기 / sets",
    "naver:KOVO여": "KOVO 여자 종료 경기 / sets",
    "fotmob:kleague1": "K리그1 HTML 경기별 xG / API 금지 경로 미사용",
    "fotmob:kleague2": "K리그2 HTML 경기별 xG / 미제공은 누락 상태",
    "fotmob:j1": "J1 HTML 경기별 xG / 수집 가능 여부는 현재 제공사 정책에 따름",
    "fotmob:j2": "J2 HTML 경기별 xG / 수집 가능 여부는 현재 제공사 정책에 따름",
    "statsbomb": "공개 과거 표본 xG / --competition --season --since --until 필수 지정 / 실시간 대체 아님",
    "mlb-expected": "MLB 선수 시즌 expectedStatistics / --player-id --season / 경기별 xG 아님",
    **{f"naver-stats:{league}": "현재 조회한 지정 시즌 팀 통계 / --season / 과거 예측에 소급 적용 금지"
       for league in SEASON_CATEGORIES},
}


class FetchError(RuntimeError):
    pass


class FetchClient:
    """One request at a time, strict budgets, no auth/403/429 bypass or retries."""
    HOSTS = {"api-gw.sports.naver.com", "statsapi.mlb.com", "www.fotmob.com",
             "raw.githubusercontent.com", "baseballsavant.mlb.com", "cdn.nba.com",
             "en.volleyballworld.com"}

    def __init__(self, db, *, max_requests=30, timeout=15, session=None, interval=1.0):
        self.db = db
        self.max_requests = max_requests
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "proodd-research/1.0 (+https://github.com/choigod1023/proto-odds-research)"})
        self.interval = interval
        self.calls = 0
        self._last = {}

    def __call__(self, url):
        for _ in range(4):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in self.HOSTS or parsed.username or parsed.password:
                raise FetchError("unapproved source URL")
            if parsed.hostname == "www.fotmob.com" and unquote(parsed.path).startswith(("/api", "/auth", "/info", "/health", "/contact_us")):
                raise FetchError("FotMob robots-disallowed path")
            if self.calls >= self.max_requests:
                raise FetchError("request budget exhausted; narrow dates or increase --max-requests explicitly")
            delay = max(self.interval, 2.5 if parsed.hostname == "www.fotmob.com" else 0)
            elapsed = time.monotonic() - self._last.get(parsed.hostname, 0)
            if elapsed < delay:
                time.sleep(delay - elapsed)
            self.calls += 1
            self._last[parsed.hostname] = time.monotonic()
            observed = utc_stamp()
            try:
                with self.session.get(url, timeout=(5, self.timeout), stream=True, allow_redirects=False) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.headers.get("Location", ""))
                        continue
                    if response.status_code != 200:
                        raise FetchError(f"HTTP {response.status_code}: {url}")
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 20 * 1024 * 1024:
                            raise FetchError("response exceeds 20 MiB limit")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    # JSON and the selected HTML providers use UTF-8; no guessed
                    # browser cookies or anti-bot impersonation are introduced.
                    body = raw.decode("utf-8-sig")
                    observed = utc_stamp()
                    digest = hashlib.sha256(raw).hexdigest()
                    key = "sports_response:" + hashlib.sha256((url + digest).encode()).hexdigest()
                    self.db.put_document_if_absent(key, {"url": url, "observed_at": observed,
                        "content_type": response.headers.get("Content-Type"), "sha256": digest, "body": body})
                    self.db.append_events("sports_fetches", [{"url": url, "observed_at": observed,
                        "status": "ok", "bytes": size, "response_document": key}])
                    return body
            except (requests.RequestException, FetchError, UnicodeError) as exc:
                self.db.append_events("sports_fetches", [{"url": url, "observed_at": utc_stamp(),
                    "status": "failed", "error": str(exc)[:500]}])
                raise FetchError(str(exc)) from exc
        raise FetchError("too many redirects")


def collect_source(source, fetch, *, since, until, limit, competition=None, season=None, player_ids=None):
    # Imports are lazy so inventory/query remain independent of network adapters.
    if source == "mlb":
        from sports_sources_results import collect_mlb
        return collect_mlb(fetch, since=since, until=until, limit=limit)
    if source == "mlb-expected":
        if not player_ids or season is None:
            raise ValueError("MLB expected statistics need --player-id and --season")
        from sports_metric_sources import collect_mlb_expected
        return collect_mlb_expected(fetch, player_ids=player_ids, season=int(season), limit=limit)
    if source.startswith("naver-stats:"):
        league = source.split(":", 1)[1]
        if league not in SEASON_CATEGORIES or season is None:
            raise ValueError("choose a supported league and explicit --season for season statistics")
        from sports_metric_sources import collect_naver_season_metrics
        category, sport = SEASON_CATEGORIES[league]
        return collect_naver_season_metrics(fetch, category=category, season=str(season), sport=sport, league=league, limit=limit)
    if source.startswith("naver:"):
        from sports_sources_results import collect_naver
        return collect_naver(fetch, league=source.split(":", 1)[1], since=since, until=until, limit=limit)
    if source.startswith("fotmob:"):
        from sports_sources_soccer import collect_fotmob
        return collect_fotmob(fetch, league=source.split(":", 1)[1], since=since, until=until, limit=limit)
    if source == "statsbomb":
        if competition is None or season is None:
            raise ValueError("StatsBomb needs explicit --competition and --season; historical data is not a live fallback")
        from sports_sources_soccer import collect_statsbomb
        return collect_statsbomb(fetch, competition=competition, season=int(season),
                                 since=since, until=until, limit=limit)
    raise ValueError(f"unknown source: {source}")


def run_collection(db, sources, *, since, until, limit=3, max_requests=30,
                   timeout=15, competition=None, season=None, player_ids=None, client=None):
    if until < since or (until - since).days > 30:
        raise ValueError("use a date window of at most 31 days")
    if not 1 <= limit <= 100 or not 1 <= max_requests <= 300 or not 1 <= timeout <= 30:
        raise ValueError("limits: records 1..100, requests 1..300, timeout 1..30 seconds")
    store = SportsHistoryStore(db)
    from sports_sources_results import PartialResultsError
    client = client or FetchClient(db, max_requests=max_requests, timeout=timeout)
    reports = []
    for source in dict.fromkeys(sources):
        snapshots = source == "mlb-expected" or source.startswith("naver-stats:")
        scope_label = f"season={season}" if snapshots else f"{since}..{until}"
        print(f"Collecting {source} ({scope_label}, limit={limit})", flush=True)
        start_calls = getattr(client, "calls", 0)
        report = {"source": source, "since": since.isoformat(), "until": until.isoformat(), "limit": limit}
        if snapshots:
            report.update(since=None, until=None, season=str(season) if season is not None else None)
        try:
            partial_reason = None
            try:
                rows = collect_source(source, client, since=since, until=until, limit=limit,
                                      competition=competition, season=season, player_ids=player_ids)
            except PartialResultsError as exc:
                if exc.reason != "output_limit":
                    raise  # broken pagination or conflicting records are not trustworthy samples
                rows, partial_reason = exc.partial_results, exc.reason
            if not isinstance(rows, list) or len(rows) > limit:
                raise ValueError("adapter exceeded the record limit or returned invalid collection")
            observed = utc_stamp()
            added = (store.record_metric_snapshots if snapshots else store.record_games)(rows, observed_at=observed)
            latest = None if snapshots else max((r.get("kickoff_at") or r.get("game_date") or "" for r in rows), default=None)
            report.update(status="partial" if partial_reason else "ok" if rows else "no_metrics" if snapshots else "no_completed_games", records=len(rows),
                inserted_versions=added, latest_game=latest,
                scope="season_snapshot" if snapshots else "match_history",
                metric_records=sum(any(v is not None for v in r.get("metrics", {}).values()) if snapshots
                    else any(v is not None for values in r.get("metrics", {}).values() for v in values.values()) for r in rows),
                note="bounded sample, not proof of complete league coverage")
            if partial_reason:
                report.update(reason=partial_reason, action="narrow the date window or increase --limit; only the bounded sample was saved")
        except Exception as exc:
            # Failed runs must never replace or delete the last successful history.
            report.update(status="failed", records=0, inserted_versions=0,
                          error=f"{type(exc).__name__}: {exc}"[:800])
        report.update(observed_at=utc_stamp(), requests=getattr(client, "calls", 0) - start_calls)
        db.append_events("sports_collection_runs", [report])
        reports.append(report)
        print(canonical_json(report), flush=True)
    payload = {"generated_at": utc_stamp(), "runs": reports, "inventory": store.inventory(),
               "metric_snapshots": len(store.metric_snapshots())}
    db.store_artifact("sports_history_collection", payload)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sources", "collect", "inventory", "team", "metrics"))
    parser.add_argument("--db", type=Path, help="explicit DB; use a separate DB for first smoke test")
    parser.add_argument("--source", action="append", default=[])
    today = datetime.now(timezone(timedelta(hours=9))).date()
    parser.add_argument("--since", type=date.fromisoformat, default=today - timedelta(days=7))
    parser.add_argument("--until", type=date.fromisoformat, default=today)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--max-requests", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--competition", type=int)
    parser.add_argument("--season", help="source-native season ID; preserves leading zeroes such as KOVO 022")
    parser.add_argument("--player-id", type=int, action="append")
    parser.add_argument("--provider")
    parser.add_argument("--league")
    parser.add_argument("--team-id")
    parser.add_argument("--subject-id")
    parser.add_argument("--as-of", help="timezone-aware knowledge cutoff")
    args = parser.parse_args(argv)
    if args.command == "sources":
        print(json.dumps(SOURCE_GUIDE, ensure_ascii=False, indent=2))
        return 0
    if not args.db:
        parser.error("--db is required; first run should use an isolated database")
    if args.command == "collect" and not args.source:
        parser.error("collect requires at least one --source")
    if args.command == "team" and not all((args.provider, args.league, args.team_id)):
        parser.error("team requires --provider, --league, --team-id")
    db = RuntimeDatabase(args.db)
    if args.command == "collect":
        result = run_collection(db, args.source, since=args.since, until=args.until, limit=args.limit,
            max_requests=args.max_requests, timeout=args.timeout, competition=args.competition, season=args.season,
            player_ids=args.player_id)
        return 1 if any(row["status"] in {"failed", "partial"} for row in result["runs"]) else 0
    store = SportsHistoryStore(db)
    if args.command == "metrics":
        result = store.metric_snapshots(provider=args.provider, league=args.league,
                                       subject_id=args.subject_id, as_of=args.as_of)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    result = store.inventory() if args.command == "inventory" else store.team_form(
        args.provider, args.league, args.team_id, as_of=args.as_of, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
