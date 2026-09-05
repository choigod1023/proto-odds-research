"""Bounded, read-only result adapters; the caller owns HTTP and persistence.

``fetch(url)`` must return decoded text and raise on HTTP errors (including
403/429), without retries. Transport exceptions propagate unchanged. An empty
list means a validated schedule contained no eligible terminal games; malformed
or incomplete responses raise. Dates are inclusive provider schedule dates:
KST for Naver, MLB's officialDate for MLB (not UTC kickoff dates).

See docs/results-source-notes.md for observed schemas, coverage and call limits.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode

Fetch = Callable[[str], str]
NAVER_API = "https://api-gw.sports.naver.com/schedule/games"
MLB_API = "https://statsapi.mlb.com/api/v1"
KST = timezone(timedelta(hours=9))
MAX_RANGE_DAYS = 31
MAX_RESULTS = 1000
NAVER_PAGE_SIZE = 100
NAVER_MAX_PAGES = 20
MLB_MAX_BOXSCORES = 5

# Source: live_scores.CATS (baseball), court_info.NAVER_CATS (court sports).
# Parameter names differ: court_info uses superCategoryId, not upperCategoryId.
# Cup aliases are deliberately omitted: their category also includes league games.
NAVER_LEAGUES = {
    "KBO": ("upperCategoryId", "kbaseball", "kbo", "야구", "runs"),
    "MLB": ("upperCategoryId", "wbaseball", "mlb", "야구", "runs"),
    "NPB": ("upperCategoryId", "wbaseball", "npb", "야구", "runs"),
    "NBA": ("superCategoryId", "basketball", "nba", "농구", "points"),
    "KBL": ("superCategoryId", "basketball", "kbl", "농구", "points"),
    "WKBL": ("superCategoryId", "basketball", "wkbl", "농구", "points"),
    "KOVO남": ("superCategoryId", "volleyball", "kovo", "배구", "sets"),
    "KOVO여": ("superCategoryId", "volleyball", "wkovo", "배구", "sets"),
}
NAVER_FINAL = frozenset({"RESULT", "END", "ENDED"})
NAVER_CANCELLED = frozenset({"CANCEL", "CANCELED", "CANCELLED", "POSTPONED"})
MLB_BATTING_METRICS = ("runs", "hits", "atBats", "baseOnBalls", "strikeOuts", "homeRuns")


class ResultsSourceError(RuntimeError):
    """Invalid source JSON/schema; never a successful no-games response."""


class UnsupportedLeagueError(ValueError):
    """No verified category mapping exists for the requested league."""


class PartialResultsError(ResultsSourceError):
    """Incomplete collection, with explicitly labelled bounded partial results.

    ``reason`` is output_limit, pagination, changed_total, or duplicate_event.
    These records must not be treated as a complete range or a no-games result.
    MLB output_limit partials contain schedule scores only, without boxscores.
    """

    status = "partial"

    def __init__(self, reason: str, partial_results: list[dict], source_url: str):
        super().__init__(f"Incomplete results ({reason}): {source_url}")
        self.reason = reason
        self.partial_results = partial_results
        self.source_url = source_url


def _bounds(since: date, until: date, limit: int) -> None:
    if type(since) is not date or type(until) is not date:
        raise ValueError("since and until must be date objects")
    if not 0 <= (until - since).days < MAX_RANGE_DAYS:
        raise ValueError(f"Use an inclusive range of 1..{MAX_RANGE_DAYS} days")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"limit must be an integer in 1..{MAX_RESULTS}")


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ResultsSourceError(f"Expected object: {label}")
    return value


def _array(value, label: str) -> list:
    if not isinstance(value, list):
        raise ResultsSourceError(f"Expected array: {label}")
    return value


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResultsSourceError(f"Missing/invalid text: {label}")
    return value


def _id(value, label: str) -> str:
    if type(value) is int and value >= 0:
        return str(value)
    return _text(value, label)


def _integer(value, label: str) -> int:
    # Never coerce floats, booleans, nulls or missing scores to a plausible score.
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise ResultsSourceError(f"Missing/invalid nonnegative integer: {label}")


def _flag(row: dict, name: str) -> bool:
    value = row.get(name, False)
    if type(value) is not bool:
        raise ResultsSourceError(f"Invalid boolean: {name}")
    return value


def _json(fetch: Fetch, url: str) -> dict:
    body = fetch(url)  # No retry, fallback, or HTTP exception suppression.
    if not isinstance(body, str):
        raise ResultsSourceError(f"fetch must return text: {url}")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ResultsSourceError(f"Invalid JSON: {url}") from exc
    return _object(data, url)


def _day(value, since: date, until: date) -> date:
    try:
        parsed = date.fromisoformat(_text(value, "schedule date"))
    except ValueError as exc:
        raise ResultsSourceError("Invalid schedule date") from exc
    if not since <= parsed <= until:
        raise ResultsSourceError("Source returned a game outside the requested date range")
    return parsed


def _kickoff(value, *, naver: bool = False) -> datetime:
    text = _text(value, "kickoff")
    try:
        # The legacy KST format is explicitly parsed in soccer_info._naver_start.
        if naver and "/" in text:
            parsed = datetime.strptime(text, "%m/%d/%Y %H:%M:%S")
        else:
            if "T" not in text and " " not in text:
                raise ValueError("Date without a confirmed time")
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if not naver:
                raise ValueError("MLB kickoff must include its timezone")
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise ResultsSourceError(f"Invalid/unconfirmed kickoff: {text}") from exc


def _ordered(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (row["kickoff_at"], row["event_id"]), reverse=True)


def _limited(rows: list[dict], limit: int, url: str) -> list[dict]:
    ordered = _ordered(rows)
    if len(ordered) > limit:
        raise PartialResultsError("output_limit", ordered[:limit], url)
    return ordered


def _naver_game(raw: dict, league: str, since: date, until: date) -> dict | None:
    _, _, category, sport, unit = NAVER_LEAGUES[league]
    if raw.get("categoryId") != category:
        raise ResultsSourceError("Naver returned a different category")
    game_day = _day(raw.get("gameDate"), since, until)
    status = _text(raw.get("statusCode"), "statusCode")
    cancelled = _flag(raw, "cancel") or status in NAVER_CANCELLED
    if not cancelled and (_flag(raw, "suspended") or status not in NAVER_FINAL):
        return None
    if _flag(raw, "timeTbd"):
        raise ResultsSourceError("Terminal Naver game has an unconfirmed kickoff")
    kickoff = _kickoff(raw.get("gameDateTime"), naver=True)
    if kickoff.astimezone(KST).date() != game_day:
        raise ResultsSourceError("Naver kickoff and KST schedule date disagree")
    event_id = _id(raw.get("gameId"), "gameId")
    rec = {
        "provider": "naver", "event_id": event_id, "league": league, "sport": sport,
        "kickoff_at": kickoff.isoformat(), "status": "cancelled" if cancelled else "final",
        "score_unit": unit, "metrics": {"home": {}, "away": {}},
        "source_url": f"https://m.sports.naver.com/game/{quote(event_id, safe='')}",
        "metric_status": "not_available",
    }
    for side in ("home", "away"):
        rec[f"{side}_id"] = _id(raw.get(f"{side}TeamCode"), f"{side}TeamCode")
        rec[f"{side}_name"] = _text(raw.get(f"{side}TeamName"), f"{side}TeamName")
        rec[f"{side}_score"] = None if cancelled else _integer(
            raw.get(f"{side}TeamScore"), f"{side}TeamScore")
        # R/H/E checked against the same game's official MLB boxscore. The fourth
        # value includes hit-by-pitches: do NOT label it baseOnBalls.
        rheb = raw.get(f"{side}TeamRheb")
        if not cancelled and sport == "야구" and rheb not in (None, []):
            rheb = _array(rheb, "TeamRheb")
            if len(rheb) != 4:
                raise ResultsSourceError("Unexpected TeamRheb layout")
            metrics = {name: _integer(rheb[i], name)
                       for i, name in enumerate(("runs", "hits", "errors"))}
            if metrics["runs"] != rec[f"{side}_score"]:
                raise ResultsSourceError("Naver RHE runs disagree with final score")
            rec["metrics"][side] = metrics
    if any(rec["metrics"].values()):
        rec["metric_status"] = "available"
    return rec


def collect_naver(fetch: Fetch, league: str, since: date, until: date, limit: int) -> list[dict]:
    """Collect the complete bounded range, paginating before applying limit.

    At most 20 requests, each requesting 100 games. Repeated/overlapping pages,
    changing totals and exhausted pagination raise PartialResultsError; so does
    an output limit smaller than the number of terminal records. WNBA and cup
    aliases have no verified separate category and raise UnsupportedLeagueError.
    """
    _bounds(since, until, limit)
    if league not in NAVER_LEAGUES:
        raise UnsupportedLeagueError(f"No verified Naver category for {league!r}")
    parent_key, parent, category, _, _ = NAVER_LEAGUES[league]
    seen: set[str] = set()
    rows: list[dict] = []
    total = None
    for page in range(1, NAVER_MAX_PAGES + 1):
        url = NAVER_API + "?" + urlencode({
            "fields": "all", parent_key: parent, "categoryId": category,
            "fromDate": since.isoformat(), "toDate": until.isoformat(),
            "size": NAVER_PAGE_SIZE, "page": page,
        })
        data = _json(fetch, url)
        if data.get("success") is not True or data.get("code") != 200:
            raise ResultsSourceError(f"Naver error envelope: {url}")
        result = _object(data.get("result"), "Naver result")
        games = _array(result.get("games"), "Naver games")
        count = _integer(result.get("gameTotalCount"), "gameTotalCount")
        if total is not None and count != total:
            raise PartialResultsError("changed_total", _ordered(rows)[:limit], url)
        total = count
        for raw in games:
            raw = _object(raw, "Naver game")
            event_id = _id(raw.get("gameId"), "gameId")
            if event_id in seen:
                raise PartialResultsError("duplicate_event", _ordered(rows)[:limit], url)
            seen.add(event_id)
            rec = _naver_game(raw, league, since, until)
            if rec is not None:
                rows.append(rec)
        if len(seen) > total:
            raise ResultsSourceError("Naver returned more games than gameTotalCount")
        if len(seen) == total:
            return _limited(rows, limit, url)
        if not games:
            raise PartialResultsError("pagination", _ordered(rows)[:limit], url)
    raise PartialResultsError("pagination", _ordered(rows)[:limit], url)


def _mlb_game(raw: dict, schedule_url: str) -> dict | None:
    state = _object(raw.get("status"), "MLB status")
    code = _text(state.get("codedGameState"), "codedGameState")
    # MLB's abstractGameState='Final' also includes postponements/cancellations.
    # 'O' means Game Over, before the official F final; suspended T/U stays live.
    cancelled = code in {"C", "D"}
    if not cancelled and code != "F":
        return None
    if state.get("abstractGameState") != "Final":
        raise ResultsSourceError("Contradictory MLB final state")
    if _flag(state, "startTimeTBD"):
        raise ResultsSourceError("Terminal MLB game has an unconfirmed kickoff")
    teams = _object(raw.get("teams"), "MLB teams")
    rec = {
        "provider": "mlb", "event_id": _id(raw.get("gamePk"), "gamePk"),
        "league": "MLB", "sport": "야구",
        "kickoff_at": _kickoff(raw.get("gameDate")).isoformat(),
        "status": "cancelled" if cancelled else "final", "score_unit": "runs",
        "source_url": schedule_url, "metrics": {"home": {}, "away": {}},
        "metric_status": "not_available",
    }
    for side in ("home", "away"):
        entry = _object(teams.get(side), f"MLB {side}")
        team = _object(entry.get("team"), f"MLB {side} team")
        rec[f"{side}_id"] = _id(team.get("id"), "team id")
        rec[f"{side}_name"] = _text(team.get("name"), "team name")
        rec[f"{side}_score"] = None if cancelled else _integer(entry.get("score"), "score")
    return rec


def _mlb_boxscore(fetch: Fetch, rec: dict) -> None:
    url = f"{MLB_API}/game/{quote(rec['event_id'], safe='')}/boxscore"
    box = _json(fetch, url)
    teams = _object(box.get("teams"), "MLB boxscore teams")
    for side in ("home", "away"):
        entry = _object(teams.get(side), f"MLB boxscore {side}")
        team = _object(entry.get("team"), "boxscore team")
        if _id(team.get("id"), "boxscore team id") != rec[f"{side}_id"]:
            raise ResultsSourceError("MLB boxscore team disagrees with schedule")
        stats = _object(entry.get("teamStats"), "teamStats")
        batting = _object(stats.get("batting", {}), "batting")
        metrics = {key: _integer(batting[key], key) for key in MLB_BATTING_METRICS
                   if key in batting and batting[key] is not None}
        if "runs" in metrics and metrics["runs"] != rec[f"{side}_score"]:
            raise ResultsSourceError("MLB boxscore runs disagree with final score")
        rec["metrics"][side] = metrics
    if any(rec["metrics"].values()):
        rec["metric_status"] = "available"
        rec["source_url"] = url


def collect_mlb(fetch: Fetch, since: date, until: date, limit: int) -> list[dict]:
    """One schedule request plus up to five boxscores for the newest finals.

    Remaining records retain final scores with metric_status='not_available'.
    Missing stats are never zeros. Output overflow raises before boxscore calls.
    No hydration, roster, Statscast, authentication, or persistence is performed.
    """
    _bounds(since, until, limit)
    url = MLB_API + "/schedule?" + urlencode({
        "sportId": 1, "startDate": since.isoformat(), "endDate": until.isoformat(),
    })
    data = _json(fetch, url)
    total = _integer(data.get("totalGames"), "MLB totalGames")
    days = _array(data.get("dates"), "MLB dates")
    rows: list[dict] = []
    seen: dict[str, dict] = {}
    received = 0
    for day in days:
        day = _object(day, "MLB day")
        schedule_day = _day(day.get("date"), since, until)
        games = _array(day.get("games"), "MLB games")
        day_total = _integer(day.get("totalGames"), "MLB day totalGames")
        if len(games) != day_total:
            raise PartialResultsError("pagination", _ordered(rows)[:limit], url)
        received += len(games)
        for raw in games:
            raw = _object(raw, "MLB game")
            if _day(raw.get("officialDate"), since, until) != schedule_day:
                raise ResultsSourceError("MLB officialDate disagrees with schedule day")
            event_id = _id(raw.get("gamePk"), "gamePk")
            if event_id in seen:
                if seen[event_id] != raw:
                    raise PartialResultsError("duplicate_event", _ordered(rows)[:limit], url)
                continue
            seen[event_id] = raw
            rec = _mlb_game(raw, url)
            if rec is not None:
                rows.append(rec)
    if received != total:
        raise PartialResultsError("pagination", _ordered(rows)[:limit], url)
    rows = _limited(rows, limit, url)
    finals = [rec for rec in rows if rec["status"] == "final"]
    for rec in finals[:MLB_MAX_BOXSCORES]:
        _mlb_boxscore(fetch, rec)
    return rows
