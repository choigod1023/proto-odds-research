"""KBO's public GameCenter feed. Previous-game lineups are never today's evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import requests

ROOT = 'https://www.koreabaseball.com'
KST = timezone(timedelta(hours=9))


def _post(session, path, data):
    response = session.post(ROOT + path, data=data, timeout=(3, 10))
    response.raise_for_status()
    return response.json()


def _lineups(body, game):
    if not isinstance(body, list) or len(body) < 5 or not body[0]:
        raise ValueError('Missing official lineup envelope')
    if body[0][0].get('LINEUP_CK') is not True:
        return {}  # KBO returns yesterday's nine players before announcement.
    result = {}
    for side, meta_index, table_index in [('home', 1, 3), ('away', 2, 4)]:
        meta = body[meta_index][0]
        if meta.get('G_ID') != game['G_ID'] or meta.get('T_ID') != game[side.upper() + '_ID']:
            raise ValueError('Official lineup belongs to another game/team')
        rows = json.loads(body[table_index][0])['rows']
        players = []
        for row in rows:
            cells = row['row']
            players.append({'batting_order': int(cells[0]['Text']),
                            'position': cells[1]['Text'].strip(),
                            'name': cells[2]['Text'].strip()})
        if sorted(p['batting_order'] for p in players) != list(range(1, 10)) or any(not p['name'] for p in players):
            raise ValueError('Incomplete official batting order')
        result[side] = sorted(players, key=lambda p: p['batting_order'])
    return result


def collect_official(session=None, now=None, existing=None):
    if session is None:
        with requests.Session() as owned:
            owned.headers.update({'User-Agent': 'Mozilla/5.0',
                                  'Referer': ROOT + '/Schedule/GameCenter/Main.aspx'})
            return collect_official(owned, now, existing)
    clock = now or datetime.now(timezone.utc)
    day = clock.astimezone(KST).date()
    games = []
    for offset in (0, 1):
        requested = (day + timedelta(days=offset)).strftime('%Y%m%d')
        payload = _post(session, '/ws/Main.asmx/GetKboGameList', {
            'leId': '1', 'srId': '0,1,3,4,5,6,7,8,9', 'date': requested})
        if not isinstance(payload, dict) or not isinstance(payload.get('game'), list) or str(payload.get('code')) != '100':
            raise ValueError('Invalid official schedule')
        for game in payload['game']:
            if game.get('G_DT') != requested or str(game.get('LE_ID')) != '1':
                raise ValueError('Official schedule date/league mismatch')
            games.append(game)
    previous = (existing or {}).get('games', [])
    if not games and any(datetime.fromisoformat(r['game_datetime']) > clock for r in previous):
        raise ValueError('Empty official schedule contradicts saved upcoming games')
    records, failures = [], 0
    for game in games:
        if str(game.get('GAME_STATE_SC')) != '1' or str(game.get('CANCEL_SC_ID')) != '0':
            continue
        kickoff = datetime.strptime(game['G_DT'] + ' ' + game['G_TM'], '%Y%m%d %H:%M').replace(tzinfo=KST)
        if kickoff <= clock:
            continue
        home, away = game['HOME_NM'].strip(), game['AWAY_NM'].strip()
        matches = [r for r in previous if r.get('home_team') == home and r.get('away_team') == away
                   and datetime.fromisoformat(r['game_datetime']) == kickoff]
        old = matches[0] if len(matches) == 1 else {}
        errors, lineups = [], {}
        try:
            body = _post(session, '/ws/Schedule.asmx/GetLineUpAnalysis', {
                'leId': game['LE_ID'], 'srId': game['SR_ID'],
                'seasonId': game['SEASON_ID'], 'gameId': game['G_ID']})
            lineups = _lineups(body, game)
        except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
            errors.append('lineup')
        stamp = (now or datetime.now(timezone.utc)).isoformat(timespec='seconds')
        starters, states = {}, {}
        for side, prefix in [('home', 'B'), ('away', 'T')]:
            name = (game.get(prefix + '_PIT_P_NM') or '').strip()
            if name and game.get('START_PIT_CK') in (1, True):
                starters[side] = {'name': name, 'announced': True, 'observed_at': stamp}
            elif old.get('starters', {}).get(side):
                starters[side] = dict(deepcopy(old['starters'][side]), stale=True)
            states[side] = 'official_today' if side in lineups else ('fetch_failed' if errors else 'not_announced')
            if side not in lineups and old.get('lineups', {}).get(side):
                lineups[side] = deepcopy(old['lineups'][side])
                states[side] = 'stale'
        official = all(state == 'official_today' for state in states.values())
        records.append({'game_id': game['G_ID'], 'league': 'KBO', 'sport': 'bs',
            'home_team': home, 'away_team': away, 'game_datetime': kickoff.isoformat(),
            'updated_at': stamp, 'source': 'KBO 공식 경기정보',
            'source_url': ROOT + '/Schedule/GameCenter/Main.aspx?gameDate=' + game['G_DT'] + '&gameId=' + game['G_ID'],
            'starters': starters, 'lineups': lineups,
            'starter_status': {'state': 'announced' if len(starters) == 2 and not any(p.get('stale') for p in starters.values()) else 'pending'},
            'lineup_status': {'state': 'official_today' if official else 'pending', 'official_today': official,
                'side_states': states, 'label': '오늘 발표 선발 타순' if official else '선발 타순 확인 중',
                'observed_at': stamp if official else (old.get('lineup_status') or {}).get('observed_at'),
                'caveat': '수집 실패 — 자동 재시도 중' if errors else ('' if official else '원천에 타순 미발표 — 자동 재조회 중')},
            'collection': {'checked_at': stamp, 'failed_endpoints': errors}})
        failures += len(errors)
    return {'generated_at': (now or datetime.now(timezone.utc)).isoformat(timespec='seconds'),
            'games': records, 'errors': failures, 'route': 'kbo_official'}
