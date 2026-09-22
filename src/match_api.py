"""Read-only, bounded views of the canonical picks artifact. Never rewrite picks."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import zlib
import gzip

KST = timezone(timedelta(hours=9))


class PreparedMatchResponses:
    """Publish immutable responses; HTTP readers never join a DB/cache rebuild.

    Only the warmer calls refresh(). Failed refreshes retain the original body,
    including its generated_at and revision. Never relabel old data as current.
    """
    def __init__(self, views):
        self.views = views
        self.lock = threading.Lock()
        self.requested = {'recent'}
        self.ready = {}

    def refresh(self):
        with self.lock:
            scopes = sorted(self.requested, key=lambda scope: scope != 'recent')
        for scope in scopes:
            day = datetime.now(KST).date().isoformat()
            body = self.views.get_bytes(scope)
            with self.lock:
                self.ready[scope] = (day, body)

    def get_bytes(self, scope='recent', key=None, revision=None, compressed=True):
        if scope == 'detail':
            # Details must match the published revision. Do not rebuild all games
            # or wait behind the warmer just to open one game's detail.
            if not self.views.lock.acquire(blocking=False):
                raise KeyError('details refreshing')
            try:
                if revision != self.views.revision:
                    raise ValueError('revision changed')
                if key not in self.views.games:
                    raise KeyError('game unavailable')
                raw = json.dumps({'game': json.loads(zlib.decompress(self.views.games[key])),
                                  'revision': revision}, ensure_ascii=False).encode()
                return gzip.compress(raw, compresslevel=3) if compressed else raw
            finally:
                self.views.lock.release()
        if scope not in ('recent', 'all'):
            raise ValueError('invalid scope')
        with self.lock:
            self.requested.add(scope)
            cached = self.ready.get(scope)
        if cached is None or cached[0] != datetime.now(KST).date().isoformat():
            raise KeyError('view preparing')
        return cached[1] if compressed else gzip.decompress(cached[1])


def game_key(game):
    identity = [str(game.get(k, '')) for k in ('year', 'round', 'sport', 'league', 'date', 'home', 'away')]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:32]


def card_game(game):
    result = dict(game, _detail_key=game_key(game))
    # Keep exactly the player names shown on compact cards; full player records are detail-only.
    info = game.get('선발')
    if isinstance(info, dict):
        compact = {}
        for side in ('home', 'away'):
            if side in info: compact[side] = info[side]
            if isinstance(info.get(side + '_detail'), dict):
                compact[side + '_detail'] = {'name': info[side + '_detail'].get('name')}
        for field in ('lineups', 'key_players'):
            compact[field] = {side: [{'name': p.get('name')} for p in info.get(field, {}).get(side, [])
                                    if isinstance(p, dict) and p.get('name')][:3]
                              for side in ('home', 'away')}
        compact['team_profiles'] = {side: {'key_players': [{'name': p.get('name')}
            for p in info['team_profiles'][side]['key_players']
            if isinstance(p, dict) and p.get('name')][:3]} for side in ('home', 'away')
            if isinstance(info.get('team_profiles', {}).get(side, {}).get('key_players'), list)}
        result['선발'] = compact
    return result


def result_game(game):
    keys = ('home', 'away', 'sport', 'league', 'date', 'round', 'prediction_record')
    result = {k: game[k] for k in keys if k in game}
    result['options'] = [{k: option[k] for k in ('selection_id', '적중') if k in option}
                         for option in game.get('options', []) if isinstance(option.get('적중'), bool)]
    return result


def summary(payload, revision, scope='recent', now=None):
    today = (now or datetime.now(KST)).astimezone(KST).date()
    days = {(today + timedelta(days=offset)).strftime('%Y-%m-%d') for offset in (-1, 0, 1)}
    def included(game):
        if scope == 'all': return True
        date = str(game.get('date', ''))[:5].replace('.', '-')
        return f"{game.get('year', today.year)}-{date}" in days
    result = {k: v for k, v in payload.items() if k not in ('live', 'past', 'prediction_performance')}
    for section in ('live', 'past'):
        result[section] = [card_game(g) for g in payload.get(section, []) if included(g)]
    result['result_games'] = [result_game(g) for section in ('live', 'past')
                              for g in payload.get(section, []) if g.get('prediction_record')]
    result['view'] = {'scope': scope, 'revision': revision, 'day': today.isoformat()}
    return result


class MatchViews:
    def __init__(self, database):
        self.database = database
        self.lock = threading.RLock()
        self.revision = None
        self.payload = None
        self.games = {}
        self.cache = {}
        self.wire_cache = {}

    def get(self, scope='recent', key=None, revision=None):
        if scope not in ('recent', 'all', 'detail'): raise ValueError('invalid scope')
        with self.lock:
            with self.database.connect() as connection:
                row = connection.execute("SELECT stored_at FROM artifacts WHERE name='picks_v2'").fetchone()
            if row is None: raise KeyError('picks unavailable')
            if self.revision != row['stored_at']:
                stored = self.database.get_artifact_json('picks_v2')
                if stored is None: raise KeyError('picks unavailable')
                body, stamp = stored
                payload = json.loads(body)
                del body, stored
                # Keep detail records compressed, not as thousands of nested Python
                # objects. Decode only the requested game, never the entire archive.
                # Build off to the side so a failed refresh preserves the old view.
                games = {}
                payload.pop('prediction_performance', None)
                for section in ('live', 'past'):
                    rows = payload.get(section, [])
                    for index, game in enumerate(rows):
                        games[game_key(game)] = zlib.compress(
                            json.dumps(game, ensure_ascii=False, separators=(',', ':')).encode(), 1)
                        rows[index] = card_game(game)
                self.games = games
                self.payload, self.revision, self.cache = payload, stamp, {}
                self.wire_cache = {}
            if scope == 'detail':
                if revision != self.revision: raise ValueError('revision changed')
                if key not in self.games: raise KeyError('game unavailable')
                return {'game': json.loads(zlib.decompress(self.games[key])), 'revision': self.revision}
            day = datetime.now(KST).date().isoformat()
            cache_key = (scope, day)
            if cache_key not in self.cache:
                # Bound cache even across many midnights with an unchanged artifact.
                self.cache = {k:v for k,v in self.cache.items() if k[1] == day}
                self.cache[cache_key] = summary(self.payload, self.revision, scope)
            return self.cache[cache_key]

    def get_bytes(self, scope='recent', key=None, revision=None, compressed=True):
        """Cache only compressed list responses; detail requests stay uncached.

        The lock coalesces concurrent rebuilds. get() still checks the DB revision
        on every request, so prewarming never extends the lifetime of stale data.
        """
        with self.lock:
            payload = self.get(scope, key, revision)
            if scope == 'detail':
                raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
                return gzip.compress(raw, compresslevel=3) if compressed else raw
            cache_key = (scope, payload['view']['day'])
            self.wire_cache = {k: v for k, v in self.wire_cache.items() if k[1] == cache_key[1]}
            if cache_key not in self.wire_cache:
                raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
                self.wire_cache[cache_key] = gzip.compress(raw, compresslevel=3)
            body = self.wire_cache[cache_key]
            return body if compressed else gzip.decompress(body)
