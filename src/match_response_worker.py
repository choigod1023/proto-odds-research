"""Build compressed HTTP views in a disposable process, never in supervisor.

The private temporary output is IPC only, not a replacement prediction artifact.
No expanded picks/lineup tree crosses the process boundary.
"""
import argparse
from datetime import datetime
import gzip
import json
import pickle
import time
from pathlib import Path
import zlib

from match_api import KST, MatchViews, PreparedMatchResponses, card_game, game_key
from match_response_snapshot import load
from runtime_db import RuntimeDatabase


def refresh_lineups(database, previous, scopes):
    """Reuse serving bytes only when the canonical prediction artifact is unchanged.

    Existing compressed details cover the complete archive, including records
    outside the recent view. Only KBO display overlays are replaced. A missing
    scope or prediction/day change falls back to a complete rebuild.
    The cache is disposable serving state, never a source of new predictions.
    """
    import zipfile
    from kbo_pregame import matching_record, display_info

    now = datetime.now(KST)
    day = now.date().isoformat()
    try:
        ready, _revisions, revision, games = load(previous, day)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        return None
    if (not set(scopes).issubset(ready) or 'recent' not in ready
            or any(value[0] != day for value in ready.values())):
        return None
    meta = database.artifact_metadata('picks_v2', include_size=False)
    if meta is None or revision.split('|kbo:', 1)[0] != meta['stored_at']:
        return None
    kbo = database.get_document('kbo_pregame') or {}
    target = MatchViews._view_revision(meta['stored_at'], kbo)
    if target == revision:
        return None
    recent = json.loads(gzip.decompress(ready['recent'][1]))
    updates = {}
    for key, compressed in games.items():
        # VERSION 1 details use compact JSON. This is a conservative filter:
        # nested KBO strings may cause an extra decode, never a wrong update.
        raw = zlib.decompress(compressed)
        if b'"league":"KBO"' not in raw:
            continue
        game = json.loads(raw)
        if game.get('league') != 'KBO':
            continue
        game.pop('baseball_current', None)
        current = matching_record(game, kbo)
        if current:
            game['baseball_current'] = display_info(current)
        encoded = json.dumps(game, ensure_ascii=False, separators=(',', ':')).encode()
        if encoded != raw:
            updates[key] = card_game(game)
            games[key] = zlib.compress(encoded, 1)
    published = {}
    for scope in scopes:
        body = recent if scope == 'recent' else json.loads(gzip.decompress(ready[scope][1]))
        if scope != 'performance':
            for section in ('live', 'past'):
                body[section] = [updates.get(game_key(row), row) for row in body.get(section, [])]
        body['view']['revision'] = target
        published[scope] = (day, gzip.compress(json.dumps(
            body, ensure_ascii=False, separators=(',', ':')).encode(), compresslevel=3))
    # Never mix an old prediction cache with a new artifact arriving during work.
    if MatchViews(database).current_revision() != target:
        raise RuntimeError('prediction revision changed while updating lineups; retry')
    return published, {scope: target for scope in scopes}, target, games


def build(database, output, scopes, previous=None):
    started = time.monotonic()
    db = RuntimeDatabase(database)
    result = refresh_lineups(db, previous, scopes) if previous is not None else None
    mode = 'lineup-only' if result is not None else 'full'
    if result is None:
        prepared = PreparedMatchResponses(MatchViews(db))
        prepared.requested = set(scopes)
        prepared.refresh()
        if set(prepared.published_revisions.values()) != {prepared.views.revision}:
            raise RuntimeError('prediction revision changed while preparing scopes; retry')
        result = (prepared.ready, prepared.published_revisions,
                  prepared.views.revision, prepared.views.games)
    prepared_at = time.monotonic()
    with Path(output).open('wb') as stream:
        pickle.dump(result, stream, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"웹 응답 준비 build={prepared_at-started:.2f}s "
          f"ipc={time.monotonic()-prepared_at:.2f}s games={len(result[3])} mode={mode}", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('database')
    parser.add_argument('output')
    parser.add_argument('scopes', nargs='+', choices=['recent', 'all', 'performance'])
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    build(args.database, args.output, args.scopes, args.previous)
