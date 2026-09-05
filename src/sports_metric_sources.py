"""Fetch season metric snapshots, never match records or historical event inputs.

Each nonempty call makes exactly one ``fetch(url)`` request (text response), with
no retry, season fallback, or per-subject request. Transport errors propagate.
``limit`` caps output rows, including separate hitting/pitching rows for a player.
Metrics contain finite numbers or None for unavailable values; raw source values
and field names remain available for auditing. Even an explicitly requested past
season is a snapshot observed *now*, not evidence available before a past game.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from urllib.parse import quote, urlencode


Fetch = Callable[[str], str]
MLB_API = "https://statsapi.mlb.com/api/v1/people"
NAVER_API = "https://api-gw.sports.naver.com"
# Canonical categories and fields validated against court_info.py. A runner can
# pass **NAVER_SEASON_SOURCES[league] with fetch, explicit season, and limit.
NAVER_SEASON_SOURCES = {
    "NBA": {"category": "nba", "sport": "bk", "league": "NBA"},
    "KBL": {"category": "kbl", "sport": "bk", "league": "KBL"},
    "WKBL": {"category": "wkbl", "sport": "bk", "league": "WKBL"},
    "KOVO남": {"category": "kovo", "sport": "vl", "league": "KOVO남"},
    "KOVO여": {"category": "wkovo", "sport": "vl", "league": "KOVO여"},
}
NAVER_CATEGORIES = {spec["category"]: spec["sport"] for spec in NAVER_SEASON_SOURCES.values()}
NAVER_COMMON_FIELDS = ("rank", "wins", "losses", "matchesPlayed")
NAVER_FIELDS = {
    "bk": NAVER_COMMON_FIELDS + (
        "winRate", "pointsPerGame", "pointsConcededPerGame", "reboundPerGame",
        "assistPerGame", "fieldGoalThrowSuccessRate", "threePointSuccessRate",
        "turnoverPerGame",
    ),
    "vl": NAVER_COMMON_FIELDS + (
        "points", "setRatio", "pointRatio", "hittingSuccessRate",
        "blockingPerSet", "servesPerSet", "digPerSet", "receiveEfficiency",
    ),
}


class MetricSourceSchemaError(ValueError):
    """A source response no longer has the expected structural schema."""


def _object(value, path: str) -> dict:
    if not isinstance(value, dict):
        raise MetricSourceSchemaError(f"{path}: expected object")
    return value


def _array(value, path: str) -> list:
    if not isinstance(value, list):
        raise MetricSourceSchemaError(f"{path}: expected array")
    return value


def _json_float(token: str):
    value = float(token)
    # Keep invalid numeric tokens inspectable, and raw_metrics JSON-serializable.
    return value if math.isfinite(value) else token


def _payload(text: str) -> dict:
    try:
        data = json.loads(text, parse_constant=str, parse_float=_json_float)
    except (TypeError, ValueError) as exc:
        raise MetricSourceSchemaError("response: expected JSON text") from exc
    return _object(data, "response")


def _number(value) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return value if isinstance(value, int) else number


def _limit(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("limit must be a nonnegative integer")


def _identifier(value, path: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        raise MetricSourceSchemaError(f"{path}: expected nonempty identifier")
    return str(value)


def _name(value, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MetricSourceSchemaError(f"{path}: expected nonempty name")
    return value


def _snapshot(*, provider: str, league: str, sport: str, subject_id: str,
              name: str, subject_type: str, season: str, group: str,
              metrics: dict, raw_metrics: dict, source_url: str,
              source_type: str, source_metadata: dict) -> dict:
    return {
        "kind": "metric_snapshot", "provider": provider, "league": league,
        "sport": sport, "subject_id": subject_id, "name": name,
        "subject_type": subject_type, "scope": "season_snapshot",
        "season": season, "group": group, "metrics": metrics,
        "raw_metrics": dict(raw_metrics), "raw_field_names": list(raw_metrics),
        "source_url": source_url, "source_type": source_type,
        "source_metadata": source_metadata,
        "availability": "available" if any(v is not None for v in metrics.values()) else "not_available",
    }


def collect_mlb_expected(fetch: Fetch, player_ids: list[int], season: int,
                         limit: int) -> list[dict]:
    """Collect MLB regular-season expectedStatistics for one batch of players.

    avg/slg/woba are interpreted only within the expectedStatistics namespace.
    This observed mapping is not a documented stable API contract. wobaCon stays
    raw and xERA stays unavailable; neither is inferred from another statistic.
    Missing stats produce no rows; matching empty stat objects produce explicitly
    unavailable snapshots. Splits without an explicit matching season are omitted.
    """
    _limit(limit)
    if type(season) is not int or season <= 0:
        raise ValueError("season must be an explicit positive integer")
    if not isinstance(player_ids, list) or any(type(p) is not int or p <= 0 for p in player_ids):
        raise ValueError("player_ids must be a list of positive integers")
    ids = list(dict.fromkeys(player_ids))
    if not limit or not ids:
        return []
    season_text = str(season)
    url = MLB_API + "?" + urlencode({
        "personIds": ",".join(map(str, ids)),
        "hydrate": f"stats(group=[hitting,pitching],type=[expectedStatistics],season={season})",
    }, safe=",=[]()")
    people = _array(_payload(fetch(url)).get("people"), "people")
    requested = {str(p) for p in ids}
    records = []
    for person_value in people:
        person = _object(person_value, "people[]")
        pid = _identifier(person.get("id"), "people[].id")
        if pid not in requested:
            continue
        for block_value in _array(person.get("stats", []), "people[].stats"):
            block = _object(block_value, "people[].stats[]")
            stat_type = _object(block.get("type"), "stats[].type").get("displayName")
            if not isinstance(stat_type, str):
                raise MetricSourceSchemaError("stats[].type.displayName: expected string")
            if stat_type != "expectedStatistics":
                continue
            group = _object(block.get("group"), "stats[].group").get("displayName")
            if not isinstance(group, str):
                raise MetricSourceSchemaError("stats[].group.displayName: expected string")
            if group not in ("hitting", "pitching"):
                continue
            for split_value in _array(block.get("splits"), "stats[].splits"):
                split = _object(split_value, "splits[]")
                if str(split.get("season") or "") != season_text:
                    continue
                sport = _object(split.get("sport", {}), "splits[].sport")
                if type(sport.get("id")) is not int or sport["id"] != 1 or split.get("gameType") != "R":
                    continue
                if "player" in split:
                    player = _object(split["player"], "splits[].player")
                    if _identifier(player.get("id"), "splits[].player.id") != pid:
                        continue
                raw = _object(split.get("stat"), "splits[].stat")
                suffix = "_against" if group == "pitching" else ""
                mapping = {"xba" + suffix: "avg", "xslg" + suffix: "slg", "xwoba" + suffix: "woba"}
                metrics = {key: _number(raw.get(field)) for key, field in mapping.items()}
                if group == "pitching":
                    metrics["xera"] = None
                records.append(_snapshot(
                    provider="mlb_statsapi", league="MLB", sport="bs", subject_id=pid,
                    name=_name(person.get("fullName"), "people[].fullName"),
                    subject_type="player", season=season_text, group=group,
                    metrics=metrics, raw_metrics=raw, source_url=url,
                    source_type=stat_type, source_metadata={
                        "type_namespace": f"{stat_type}.{group}",
                        "metric_field_map": mapping,
                        "mapping_interpretation": "avg/slg/woba interpreted as expected metrics only within expectedStatistics",
                        "api_stability": "undocumented; observed response, not a stable API contract",
                        "sport_id": 1, "game_type": "R",
                        "unavailable_metrics": ["xera"] if group == "pitching" else [],
                    },
                ))
                if len(records) == limit:
                    return records
    return records


def collect_naver_season_metrics(fetch: Fetch, category: str, season: str,
                                 sport: str, league: str, limit: int) -> list[dict]:
    """Collect team snapshots from one explicit Naver source season ID.

    Preserve leading zeroes (e.g. KOVO '022') and field names/units exactly.
    Only court_info.py's validated fields enter metrics. The complete team row
    remains in raw_metrics, including unknown fields and source update time.
    Rows without a matching seasonId/categoryId cannot establish scope and are
    omitted. No schedule lookup or fallback to a guessed season occurs here.
    """
    _limit(limit)
    if category not in NAVER_CATEGORIES:
        raise ValueError("unsupported Naver category")
    source_category, expected_sport = category, NAVER_CATEGORIES[category]
    if sport != expected_sport:
        raise ValueError(f"{category} requires sport={expected_sport!r}")
    if not isinstance(season, str) or not season.strip() or season != season.strip():
        raise ValueError("season must be an explicit nonempty source season ID")
    if not isinstance(league, str) or not league.strip():
        raise ValueError("league must be a nonempty string")
    if not limit:
        return []
    url = f"{NAVER_API}/statistics/categories/{source_category}/seasons/{quote(season, safe='')}/teams"
    payload = _payload(fetch(url))
    if ("success" in payload and payload["success"] is not True) or (
        "code" in payload and payload["code"] != 200
    ):
        raise MetricSourceSchemaError("Naver returned an unsuccessful response")
    result = _object(payload.get("result"), "result")
    rows = _array(result.get("seasonTeamStats"), "result.seasonTeamStats")
    records = []
    for row_value in rows:
        row = _object(row_value, "seasonTeamStats[]")
        if str(row.get("seasonId") or "") != season or row.get("categoryId") != source_category:
            continue
        records.append(_snapshot(
            provider="naver", league=league, sport=sport,
            subject_id=_identifier(row.get("teamId"), "seasonTeamStats[].teamId"),
            name=_name(row.get("teamName"), "seasonTeamStats[].teamName"),
            subject_type="team", season=season, group="team",
            metrics={field: _number(row.get(field)) for field in NAVER_FIELDS[sport]},
            raw_metrics=row, source_url=url, source_type="seasonTeamStats",
            source_metadata={"category": category, "source_category": source_category,
                             "metric_field_map": {field: field for field in NAVER_FIELDS[sport]}},
        ))
        if len(records) == limit:
            return records
    return records
