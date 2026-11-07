"""Small wire views of existing score and odds artifacts, using one DB snapshot."""
import gzip
import hashlib
import io
import json
import re


# Consumers merge these detail fields only after matching the provider event.
SCORE_DETAIL = ("timeline", "timeline_scope", "period_scores")
LIVE_VIEWS = {
    "/api/live-scores-summary": ("live_scores", "summary"),
    "/api/live-scores-detail": ("live_scores", "detail"),
    "/api/live-odds-summary": ("live_odds", "summary"),
    "/api/live-odds-history": ("live_odds", "detail"),
}


def query_key(name, view, query):
    if view == "summary":
        return None

    def one(field):
        values = query.get(field, [])
        if len(values) != 1:
            raise ValueError("expected one " + field)
        return values[0]

    if name == "live_scores":
        key = tuple(one(field) for field in ("game_id", "source", "start"))
        if any(not re.fullmatch(r"[A-Za-z0-9_:+.\-]{1,90}", part) for part in key):
            raise ValueError("invalid score identity")
        return key
    number = one("round")
    ids = one("game_nos").split(",")
    if (not re.fullmatch(r"[0-9]{1,4}", number) or not 1 <= len(ids) <= 16
            or any(not re.fullmatch(r"[0-9]{1,6}", value) for value in ids)):
        raise ValueError("invalid odds history identity")
    return (number, *sorted(set(ids)))


def _chunks(connection, name, view, key):
    if view == "summary" and name == "live_scores":
        metadata, = connection.execute(
            "SELECT json_remove(payload_json, '$.games') FROM artifacts WHERE name=?", (name,)).fetchone()
        yield metadata[:-1] + (',' if metadata != '{}' else '') + '"summary":true,"games":['
        fields = ", ".join("'$." + field + "'" for field in SCORE_DETAIL)
        rows = connection.execute(f"""
            SELECT json_remove(game.value, {fields})
            FROM artifacts, json_each(payload_json, '$.games') AS game
            WHERE name=? AND game.type='object'
        """, (name,))
        for index, (row,) in enumerate(rows):
            yield (',' if index else '') + row
        yield ']}'
    elif view == "summary":
        row, = connection.execute(
            "SELECT json_remove(payload_json, '$.history') FROM artifacts WHERE name=?", (name,)).fetchone()
        yield row
    elif name == "live_scores":
        fields = ", ".join(f"'{field}', json_extract(game.value, '$.{field}')" for field in SCORE_DETAIL)
        rows = connection.execute(f"""
            SELECT json_object({fields})
            FROM artifacts, json_each(payload_json, '$.games') AS game
            WHERE name=? AND game.type='object'
              AND json_extract(game.value, '$.game_id')=?
              AND json_extract(game.value, '$.source')=?
              AND json_extract(game.value, '$.start')=?
            LIMIT 2
        """, (name, *key)).fetchall()
        detail = json.loads(rows[0][0]) if len(rows) == 1 else None
        yield json.dumps({"identity": key, "detail": detail}, ensure_ascii=False, separators=(",", ":"))
    else:
        number, *ids = key
        paths = [f'$."history"."{number}"."{value}"' for value in ids]
        placeholders = ", ".join("json_extract(payload_json, ?)" for _ in ids)
        values = connection.execute(f"SELECT {placeholders} FROM artifacts WHERE name=?",
                                    (*paths, name)).fetchone()
        yield json.dumps({"round": number, "history": {
            value: json.loads(history) for value, history in zip(ids, values) if history
        }}, ensure_ascii=False, separators=(",", ":"))


def get_view_gzip(database, name, view, key):
    with database.connect() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT stored_at FROM artifacts WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        output, digest = io.BytesIO(), hashlib.sha256()
        with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=3, mtime=0) as stream:
            for chunk in _chunks(connection, name, view, key):
                raw = chunk.encode()
                digest.update(raw)
                stream.write(raw)
        return output.getvalue(), row["stored_at"], 'W/"' + digest.hexdigest() + '"'
