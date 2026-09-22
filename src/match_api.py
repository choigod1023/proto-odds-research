"""Read-only, bounded views of the canonical picks artifact. Never rewrite picks."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import zlib

KST = timezone(timedelta(hours=9))


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
        self.lock = threading.Lock()
        self.revision = None
        self.payload = None
        self.games = {}
        self.cache = {}

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
