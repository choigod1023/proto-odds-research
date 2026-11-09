"""Index the detail rows from the same committed feed used by the lists."""
from collections.abc import Mapping
import json

TIMELINE_FIELDS = ("source", "game_id", "start", "md", "home", "away")
ODDS_FIELDS = ("home", "away", "date")
ROUTES = {"/api/fixture-timeline": "live_scores", "/api/fixture-odds-history": "live_odds"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_key(kind, key):
    if not key or len(key) > 1024:
        raise ValueError("invalid fixture key")
    parts = json.loads(key)
    size = len(TIMELINE_FIELDS) if kind == "live_scores" else 1 + len(ODDS_FIELDS)
    if not isinstance(parts, list) or len(parts) != size or not all(
        part is None or type(part) in (str, int) for part in parts
    ):
        raise ValueError("invalid fixture key")
    return encode(parts)


def _timelines(payload):
    games = payload.get("games")
    for row in games if isinstance(games, (list, tuple)) else ():
        if not isinstance(row, Mapping) or not row.get("timeline"):
            continue
        identity = {key: row.get(key) for key in TIMELINE_FIELDS}
        yield encode(list(identity.values())), encode({**identity, "timeline": row["timeline"]})


def _market_number(row):
    number = str(row.get("game_no", ""))
    return int(number) if number.isdecimal() else 0


def _odds_history(payload):
    history, rounds = payload.get("history"), payload.get("markets")
    if not isinstance(history, Mapping) or not isinstance(rounds, Mapping):
        return
    for round_no, markets in rounds.items():
        round_history = history.get(round_no)
        if not isinstance(markets, Mapping) or not isinstance(round_history, Mapping):
            continue
        by_game = {}
        for market in sorted((row for row in markets.values() if isinstance(row, Mapping)), key=_market_number):
            number = str(market.get("game_no"))
            entries = round_history.get(number) or ()
            if not isinstance(entries, (list, tuple)):
                continue
            key = encode([str(round_no), *(market.get(field) for field in ODDS_FIELDS)])
            by_game.setdefault(key, []).extend(
                {**entry, "gameNo": number} for entry in entries if isinstance(entry, Mapping)
            )
        for key, entries in by_game.items():
            if entries:
                yield key, encode(entries)


BUILDERS = {"live_scores": _timelines, "live_odds": _odds_history}
