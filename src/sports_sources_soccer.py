"""Read-only soccer adapters; network policy and persistence belong to the caller.

``fetch(url: str) -> str`` must do one bounded GET, raise on HTTP errors and
challenges, and never retry, proxy, or follow an unchecked redirect. No network
library, DB, clock, or scheduler is used here. A positive limit permits one index
request plus at most ``limit`` detail requests and records; zero makes no calls.
Date bounds are inclusive (UTC for FotMob, source match_date for StatsBomb).
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone
from html.parser import HTMLParser

Fetch = Callable[[str], str]
FOTMOB = "https://www.fotmob.com"
STATSBOMB = "https://raw.githubusercontent.com/hudl/open-data/master/data"
LEAGUES = {
    "kleague1": (9080, "k-league-1"),
    "kleague2": (9081, "k-league-2"),
    "j1": (223, "j-league"),
    "j2": (8974, "j-league-2"),
}


def _bounds(limit: int, since: date | None, until: date | None) -> None:
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    for value in (since, until):
        if value is not None and type(value) is not date:
            raise ValueError("date bounds must be datetime.date values")
    if since is not None and until is not None and since > until:
        raise ValueError("since must not be after until")


def _in_range(day: date, since: date | None, until: date | None) -> bool:
    return (since is None or day >= since) and (until is None or day <= until)


def _id(value: object) -> str:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError(f"invalid source ID: {value!r}")
    return str(value)


def _score(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid final score: {value!r}")
    return value


def _name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing team/competition name")
    return value


def _metric(value: object) -> float | None:
    if value is None or value in ("", "-", "—"):
        return None
    if isinstance(value, bool):
        raise ValueError("boolean xG")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"invalid xG: {value!r}")
    return number


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("FotMob kickoff requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _walk(value: object) -> Iterator[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


class _NextData(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def _page_props(html: str) -> dict:
    # Same embedded payload and All-period approach as fotmob_xg.py, factored
    # here to avoid that legacy module's requests/runtime_db import dependency.
    parser = _NextData()
    parser.feed(html)
    if not parser.parts:
        raise ValueError("FotMob __NEXT_DATA__ missing (challenge or schema change)")
    props = json.loads("".join(parser.parts))["props"]["pageProps"]
    if not isinstance(props, dict):
        raise ValueError("invalid FotMob pageProps")
    return props


def _final(status: dict) -> bool:
    reason = status.get("reason") or {}
    text = " ".join(str(v).lower() for v in reason.values())
    return status.get("finished") is True and not any(
        status.get(key) for key in ("cancelled", "awarded", "abandoned", "postponed")
    ) and not any(word in text for word in ("cancel", "abandon", "award", "postpon"))


def _match_url(page_url: str) -> str:
    # Only observed public HTML paths; no host overrides, query redirects,
    # percent-encoded traversal, backslashes, or robots-disallowed endpoints.
    path = page_url.split("#", 1)[0]
    if not re.fullmatch(r"/matches/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+", path):
        raise ValueError(f"not a public FotMob match path: {page_url!r}")
    return FOTMOB + path


def _fixture_rows(props: dict) -> list[dict]:
    """Require an explicit fixture list; unrelated page data is not a fallback."""
    if any(props.get(key) for key in ("error", "errors", "errorMessage", "notFound")):
        raise ValueError("FotMob listing reports an error")
    status_code = props.get("statusCode")
    if isinstance(status_code, int) and status_code >= 400:
        raise ValueError("FotMob listing reports an error status")
    # Current league HTML uses fixtures.allMatches. Flat fixtures/matches lists
    # and matches.allMatches are supported explicitly, never via a page-wide walk.
    key = "fixtures" if "fixtures" in props else "matches"
    if key not in props:
        raise ValueError("FotMob listing missing recognized fixtures container")
    rows = props[key]
    if isinstance(rows, dict):
        if "allMatches" not in rows:
            raise ValueError(f"FotMob listing {key}.allMatches missing")
        rows = rows["allMatches"]
    if not isinstance(rows, list):
        raise ValueError(f"FotMob listing {key} must contain a fixtures list")
    for row in rows:
        if (not isinstance(row, dict) or "id" not in row
                or not isinstance(row.get("status"), dict)
                or not isinstance(row.get("pageUrl"), str)
                or not row["pageUrl"].startswith("/matches/")):
            raise ValueError("FotMob listing contains an invalid fixture row")
    return rows


def parse_fotmob_fixtures(html: str, since: date, until: date) -> list[dict]:
    """Validate listing, then deduplicate final fixtures latest first; no I/O."""
    fixtures: dict[str, dict] = {}
    for item in _fixture_rows(_page_props(html)):
        status = item.get("status") or {}
        if not _final(status):
            continue
        kickoff = _utc(status["utcTime"])
        if not _in_range(kickoff.date(), since, until):
            continue
        event_id = _id(item["id"])
        fixtures.setdefault(event_id, {
            "id": event_id, "url": _match_url(item["pageUrl"]), "kickoff": kickoff,
        })
    return sorted(fixtures.values(), key=lambda f: (f["kickoff"], int(f["id"])), reverse=True)


def _fotmob_metrics(props: dict) -> dict:
    node = props.get("content") or {}
    for key in ("stats", "Periods", "All"):
        node = node.get(key) or {}
    metrics: dict[str, dict] = {"home": {}, "away": {}}
    keys = {"expected_goals": "xg", "expected_goals_non_penalty": "npxg"}
    for item in _walk(node):
        name = keys.get(item.get("key"))
        if name is None or item.get("type") != "text":
            continue
        values = item.get("stats")
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError("invalid FotMob metric pair")
        for side, value in zip(("home", "away"), values):
            number = _metric(value)
            if number is not None:
                if name in metrics[side] and metrics[side][name] != number:
                    raise ValueError("conflicting FotMob All-period metrics")
                metrics[side][name] = number
    return {side: values for side, values in metrics.items() if "xg" in values}


def _metric_status(metrics: dict) -> str:
    if not metrics:
        return "not_available"
    return "available" if all("xg" in metrics.get(s, {}) for s in ("home", "away")) else "partial"


def collect_fotmob(
    fetch: Fetch, league: str = "kleague1", *, since: date, until: date, limit: int,
) -> list[dict]:
    """One current league HTML page + at most limit match HTML pages.

    No pagination, retries, API requests, or backfill to replace skipped matches.
    Match-page IDs must agree with the listing: public matchup URLs can change
    which match they display over time. Errors propagate instead of misattribution.
    """
    _bounds(limit, since, until)
    if since is None or until is None:
        raise ValueError("FotMob requires since and until")
    if league not in LEAGUES:
        raise ValueError(f"unsupported FotMob league: {league}")
    if limit == 0:
        return []
    league_id, slug = LEAGUES[league]
    listing_url = f"{FOTMOB}/leagues/{league_id}/matches/{slug}"
    fixtures = parse_fotmob_fixtures(fetch(listing_url), since, until)
    records = []
    for fixture in fixtures[:limit]:
        props = _page_props(fetch(fixture["url"]))
        general, header = props["general"], props["header"]
        if _id(general["matchId"]) != fixture["id"]:
            raise ValueError("FotMob match-page ID differs from fixture")
        source_league = general.get("parentLeagueId") or general.get("leagueId")
        if source_league is not None and _id(source_league) != str(league_id):
            raise ValueError("FotMob match-page league differs from requested league")
        if (not _final(header.get("status") or {})
                or not _final({**general, "finished": general.get("finished", True)})):
            continue
        kickoff = _utc(general["matchTimeUTCDate"])
        if not _in_range(kickoff.date(), since, until):
            continue
        home, away = general["homeTeam"], general["awayTeam"]
        teams = {_id(team["id"]): team for team in header["teams"]}
        home_id, away_id = _id(home["id"]), _id(away["id"])
        if home_id == away_id:
            raise ValueError("home and away IDs must differ")
        metrics = _fotmob_metrics(props)
        records.append({
            "provider": "fotmob", "event_id": fixture["id"], "league": league,
            "sport": "축구", "kickoff_at": kickoff.isoformat(),
            "game_date": kickoff.date().isoformat(), "time_precision": "instant",
            "status": "final", "home_id": home_id, "away_id": away_id,
            "home_name": _name(home["name"]), "away_name": _name(away["name"]),
            "home_score": _score(teams[home_id]["score"]),
            "away_score": _score(teams[away_id]["score"]), "score_unit": "goals",
            "metrics": metrics, "metric_status": _metric_status(metrics),
            "source_url": fixture["url"], "listing_url": listing_url,
            "metric_scope": "provider_match_all_periods",
        })
    return sorted(records, key=lambda r: (r["kickoff_at"], int(r["event_id"])), reverse=True)


def _statsbomb_metrics(events: list, home_id: str, away_id: str) -> dict:
    shots: dict[str, list] = {home_id: [], away_id: []}
    seen: set[str] = set()
    for event in events:
        if event.get("type", {}).get("name") != "Shot" and event.get("type", {}).get("id") != 16:
            continue
        period = event["period"]
        if type(period) is not int or period not in (1, 2, 3, 4, 5):
            raise ValueError("invalid StatsBomb shot period")
        if period == 5:
            continue
        event_id = _name(event["id"])
        if event_id in seen:
            raise ValueError("duplicate StatsBomb shot event")
        seen.add(event_id)
        team_id = _id(event["team"]["id"])
        if team_id not in shots:
            raise ValueError("shot team not in match")
        shots[team_id].append(event["shot"])
    metrics = {}
    for side, team_id in (("home", home_id), ("away", away_id)):
        team_shots = shots[team_id]
        # No observed shots or incomplete shot xG is not evidence for zero xG.
        values = [_metric(shot.get("statsbomb_xg")) for shot in team_shots]
        if not values or any(value is None for value in values):
            continue
        metrics[side] = {"xg": math.fsum(values)}
        types = [shot.get("type") or {} for shot in team_shots]
        if all(t.get("id") is not None or t.get("name") for t in types):
            metrics[side]["npxg"] = math.fsum(
                value for value, kind in zip(values, types)
                if kind.get("id") != 88 and kind.get("name") != "Penalty"
            )
    return metrics


def collect_statsbomb(
    fetch: Fetch, competition: int, season: int, limit: int,
    since: date | None = None, until: date | None = None,
) -> list[dict]:
    """Explicit historical open-data sample, never a current/live fallback.

    One competition/season match index and at most limit event JSON files.
    Unknown kickoff timezone stays unknown; match_date is preserved separately.
    """
    _bounds(limit, since, until)
    if type(competition) is not int or type(season) is not int:
        raise ValueError("competition and season must be explicit integer IDs")
    _id(competition)
    _id(season)
    if limit == 0:
        return []
    listing_url = f"{STATSBOMB}/matches/{competition}/{season}.json"
    matches = json.loads(fetch(listing_url))
    if not isinstance(matches, list):
        raise ValueError("StatsBomb match index must be a JSON list")
    selected = {}
    for match in matches:
        if match.get("match_status") != "available":
            continue
        day = date.fromisoformat(match["match_date"])
        if not _in_range(day, since, until):
            continue
        if (match["competition"]["competition_id"] != competition
                or match["season"]["season_id"] != season):
            raise ValueError("StatsBomb competition/season mismatch")
        selected.setdefault(_id(match["match_id"]), match)
    ordered = sorted(selected.values(), key=lambda m: (
        m["match_date"], m.get("kick_off") or "", int(m["match_id"])), reverse=True)
    records = []
    for match in ordered[:limit]:
        event_id = _id(match["match_id"])
        source_url = f"{STATSBOMB}/events/{event_id}.json"
        home, away = match["home_team"], match["away_team"]
        home_id, away_id = _id(home["home_team_id"]), _id(away["away_team_id"])
        if home_id == away_id:
            raise ValueError("home and away IDs must differ")
        home_score, away_score = _score(match["home_score"]), _score(match["away_score"])
        events = json.loads(fetch(source_url))
        if not isinstance(events, list):
            raise ValueError("StatsBomb events must be a JSON list")
        metrics = _statsbomb_metrics(events, home_id, away_id)
        records.append({
            "provider": "statsbomb", "event_id": event_id,
            "league": str(competition), "sport": "축구", "kickoff_at": None,
            "game_date": match["match_date"], "time_precision": "date",
            "source_kickoff": match.get("kick_off"), "status": "final",
            "home_id": home_id, "away_id": away_id,
            "home_name": _name(home["home_team_name"]),
            "away_name": _name(away["away_team_name"]),
            "home_score": home_score, "away_score": away_score, "score_unit": "goals",
            "metrics": metrics, "metric_status": _metric_status(metrics),
            "source_url": source_url, "listing_url": listing_url,
            "metric_scope": "shot_statsbomb_xg_periods_1_to_4",
            "competition": competition, "season": season,
            "competition_name": _name(match["competition"]["competition_name"]),
            "season_name": _name(match["season"]["season_name"]),
            "sample_scope": "historical_open_data",
        })
    return records
