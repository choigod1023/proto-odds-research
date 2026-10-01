"""Storage-time match views. Caller owns the transaction and artifact revision."""
import json
import zlib

SCHEMA = """
CREATE TABLE IF NOT EXISTS match_projection (
    name TEXT PRIMARY KEY, revision TEXT NOT NULL, version INTEGER NOT NULL,
    cards_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_details (
    name TEXT NOT NULL, game_key TEXT NOT NULL, detail_zlib BLOB NOT NULL,
    PRIMARY KEY(name, game_key),
    FOREIGN KEY(name) REFERENCES match_projection(name) ON DELETE CASCADE
);
"""
VERSION = 1


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def write_projection(connection, payload, revision):
    from match_api import card_game, game_key

    cards = {k: v for k, v in payload.items()
             if k not in ('live', 'past', 'prediction_performance')}
    connection.execute("DELETE FROM match_projection WHERE name='picks_v2'")
    connection.execute("INSERT INTO match_projection VALUES ('picks_v2', ?, ?, '{}')",
                       (revision, VERSION))
    for section in ('live', 'past'):
        cards[section] = []
        for game in payload.get(section, []):
            cards[section].append(card_game(game))
            # One full game at a time; duplicate identities retain historical last-wins behavior.
            connection.execute("INSERT OR REPLACE INTO match_details VALUES ('picks_v2', ?, ?)",
                               (game_key(game), zlib.compress(encode(game).encode(), 1)))
    connection.execute("UPDATE match_projection SET cards_json=? WHERE name='picks_v2'",
                       (encode(cards),))


def read_projection(connection, revision):
    """Use the caller's read snapshot, including for legacy/old-writer fallback.

    Fallback never writes or backfills a DB. SQLite parses legacy JSON, but Python
    holds only a single full game plus compact cards and compressed details.
    """
    from match_api import card_game, game_key

    row = connection.execute(
        "SELECT cards_json FROM match_projection WHERE name='picks_v2' AND revision=? AND version=?",
        (revision, VERSION)).fetchone()
    if row is not None:
        cards = json.loads(row['cards_json'])
        games = {row['game_key']: bytes(row['detail_zlib']) for row in connection.execute(
            "SELECT game_key,detail_zlib FROM match_details WHERE name='picks_v2'")}
        return cards, games
    row = connection.execute("""SELECT json_remove(payload_json, '$.live', '$.past',
        '$.prediction_performance') FROM artifacts WHERE name='picks_v2'""").fetchone()
    cards, games = json.loads(row[0]), {}
    for section in ('live', 'past'):
        cards[section] = []
        for row in connection.execute("""SELECT g.value FROM artifacts a,
            json_each(a.payload_json, ?) g WHERE a.name='picks_v2'""", (f'$.{section}',)):
            game = json.loads(row[0])
            cards[section].append(card_game(game))
            games[game_key(game)] = zlib.compress(encode(game).encode(), 1)
    return cards, games
