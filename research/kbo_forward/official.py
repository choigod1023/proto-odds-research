"""Bounded, evidence-preserving public KBO feed for research only.

No inferred batting identity, same-day performance, or postgame backfill is used.
"""
import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = 'https://www.koreabaseball.com'
SCHEDULE = BASE + '/ws/Main.asmx/GetKboGameList'
LINEUP = BASE + '/ws/Schedule.asmx/GetLineUpAnalysis'
KST = timezone(timedelta(hours=9))


def now_utc():
    return datetime.now(timezone.utc)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SourceError(Exception):
    pass


class OfficialClient:
    """One run has at most 80 requests; persisted evidence and five-minute cache.

    state_dir must be private to this collector. Never share between processes.
    """
    def __init__(self, state_dir, max_requests=80, max_lineup_requests=5, session=None):
        if not 1 <= max_requests <= 80 or not 0 <= max_lineup_requests <= 5:
            raise ValueError('request limits out of range')
        self.root = Path(state_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'bodies').mkdir(exist_ok=True)
        self.index = self.root / 'receipts.jsonl'
        self.max_requests, self.max_lineups = max_requests, max_lineup_requests
        self.count = self.lineups = 0
        self.last = 0.0
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': BASE + '/Schedule/GameCenter/Main.aspx'})
        self.records = [json.loads(x) for x in self.index.read_text(encoding='utf-8').splitlines()] if self.index.exists() else []
        if self.records:
            last = datetime.fromisoformat(self.records[-1]['finished_at'])
            self.last = time.monotonic() - max(0, (now_utc() - last).total_seconds())

    def fetch(self, url, params, *, refresh_seconds=300):
        if url not in (SCHEDULE, LINEUP):
            raise ValueError('unapproved endpoint')
        current = now_utc()
        for rec in reversed(self.records):
            age = (current - datetime.fromisoformat(rec['finished_at'])).total_seconds()
            if rec['url'] == url and rec['params'] == params and rec['status'] == 200 and 0 <= age < refresh_seconds:
                raw = (self.root / rec['body_file']).read_bytes()
                if sha(raw) != rec['sha256']:
                    raise SourceError('cached_body_sha_mismatch')
                return raw, rec
        if self.records and self.records[-1]['status'] == 403:
            raise SourceError('previous_403')
        for attempt in range(2):
            if self.count >= self.max_requests:
                raise SourceError('request_cap')
            if url == LINEUP and self.lineups >= self.max_lineups:
                raise SourceError('lineup_request_cap')
            time.sleep(max(0, 1.5 - (time.monotonic() - self.last)))
            self.count += 1
            self.lineups += int(url == LINEUP)
            rec = {'url': url, 'method': 'POST', 'params': params, 'requested_at': now_utc().isoformat(), 'attempt': attempt + 1}
            try:
                response = self.session.post(url, data=params, timeout=(5, 15), allow_redirects=False)
                raw = response.content
                rec.update(status=response.status_code, sha256=sha(raw), body_file='bodies/' + sha(raw) + '.bin')
                (self.root / rec['body_file']).write_bytes(raw)
            except requests.RequestException as exc:
                response, raw = None, b''
                rec.update(status=None, error_type=type(exc).__name__)
            rec['finished_at'] = now_utc().isoformat()
            self.last = time.monotonic()
            with self.index.open('a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + '\n')
            self.records.append(rec)
            if response is not None and response.status_code == 200:
                return raw, rec
            if response is not None and response.status_code == 403:
                raise SourceError('http_403')
            if response is not None and response.status_code != 429:
                raise SourceError('http_' + str(response.status_code))
            if attempt == 0:
                time.sleep(15)
        raise SourceError('timeout_or_429_after_backoff')

    def schedule(self, dates):
        games, receipts = [], []
        for day in dates:
            day = date.fromisoformat(day) if isinstance(day, str) else day
            requested = day.strftime('%Y%m%d')
            raw, rec = self.fetch(SCHEDULE, {'leId': '1', 'srId': '0,1,3,4,5,6,7,8,9', 'date': requested})
            obj = json.loads(raw)
            if not isinstance(obj, dict) or str(obj.get('code')) != '100' or not isinstance(obj.get('game'), list):
                raise SourceError('invalid_schedule')
            for game in obj['game']:
                if game.get('G_DT') != requested or str(game.get('LE_ID')) != '1':
                    raise SourceError('schedule_identity_mismatch')
                games.append({'source': game, 'receipt': rec})
            receipts.append(rec)
        return {'games': games, 'receipts': receipts}

    def snapshot(self, game):
        """Return source announcement and explicit missing fields; never invent IDs."""
        g, schedule_rec = game['source'], game['receipt']
        missing = []
        try:
            kickoff = datetime.strptime(g['G_DT'] + ' ' + g['G_TM'], '%Y%m%d %H:%M').replace(tzinfo=KST)
        except (KeyError, TypeError, ValueError):
            return {'source_verified': False, 'missing': ['invalid_kickoff']}
        if str(g.get('CANCEL_SC_ID')) != '0' or str(g.get('GAME_STATE_SC')) != '1':
            return {'source_verified': False, 'missing': ['game_not_scheduled']}
        if now_utc() > kickoff.astimezone(timezone.utc) - timedelta(minutes=30):
            return {'source_verified': False, 'missing': ['capture_after_t_minus_30']}
        if datetime.fromisoformat(schedule_rec['finished_at']) > kickoff.astimezone(timezone.utc) - timedelta(minutes=30):
            return {'source_verified': False, 'missing': ['schedule_after_t_minus_30']}
        starters = {}
        for side, prefix in [('home', 'B'), ('away', 'T')]:
            pid, name = g.get(prefix + '_PIT_P_ID'), str(g.get(prefix + '_PIT_P_NM') or '').strip()
            if g.get('START_PIT_CK') in (1, True) and str(pid or '').isdigit() and int(pid) > 0 and name:
                starters[side] = {'player_id': str(pid), 'name': name}
        if len(starters) != 2:
            missing.append('announced_starters')
        raw, rec = self.fetch(LINEUP, {'leId': g['LE_ID'], 'srId': g['SR_ID'], 'seasonId': g['SEASON_ID'], 'gameId': g['G_ID']})
        if datetime.fromisoformat(rec['finished_at']) > kickoff.astimezone(timezone.utc) - timedelta(minutes=30):
            missing.append('lineup_after_t_minus_30')
        ordered = parse_lineup(raw, g)
        if ordered is None:
            missing.append('confirmed_lineups')
        else:
            missing.append('verified_batter_ids_and_prior_history')
        return {'source_verified': False, 'missing': missing, 'game': game, 'kickoff_at': kickoff.isoformat(), 'starters': starters, 'lineups': ordered, 'lineup_receipt': rec}

    def result(self, game):
        g = game['source']
        found = [x for x in self.schedule([datetime.strptime(g['G_DT'], '%Y%m%d').date()])['games'] if x['source'].get('G_ID') == g['G_ID']]
        if len(found) != 1:
            return {'verified': False, 'missing': ['unique_official_result']}
        source = found[0]['source']
        if any(str(source.get(k)) != str(g.get(k)) for k in ('G_DT', 'HOME_ID', 'AWAY_ID', 'LE_ID', 'SR_ID', 'SEASON_ID')):
            return {'verified': False, 'missing': ['result_identity_mismatch']}
        if str(source.get('GAME_STATE_SC')) != '3' or str(source.get('GAME_RESULT_CK')) != '1' or str(source.get('CANCEL_SC_ID')) != '0':
            return {'verified': False, 'missing': ['completed_official_result']}
        # Schedule schema varies; scores are never inferred from team names or text.
        try:
            home, away = int(source['B_SCORE_CN']), int(source['T_SCORE_CN'])
            if home < 0 or away < 0:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            return {'verified': False, 'missing': ['official_score_fields'], 'game': found[0]}
        return {'verified': True, 'home_score': home, 'away_score': away, 'game': found[0]}


def parse_lineup(raw, game):
    obj = json.loads(raw)
    if not isinstance(obj, list) or len(obj) != 5 or not obj[0] or type(obj[0][0].get('LINEUP_CK')) is not bool:
        raise SourceError('invalid_lineup_envelope')
    if obj[0][0]['LINEUP_CK'] is False:
        return None
    out = {}
    for side, mi, ti in [('home', 1, 3), ('away', 2, 4)]:
        meta = obj[mi][0]
        for key, wanted in [('G_ID', game['G_ID']), ('T_ID', game[side.upper() + '_ID']), ('LE_ID', game['LE_ID']), ('SR_ID', game['SR_ID']), ('SEASON_ID', game['SEASON_ID'])]:
            if str(meta.get(key)) != str(wanted):
                raise SourceError('lineup_identity_mismatch_' + key)
        rows = json.loads(obj[ti][0])['rows']
        players = [{'slot': int(x['row'][0]['Text']), 'position': str(x['row'][1]['Text']).strip(), 'name': str(x['row'][2]['Text']).strip()} for x in rows]
        if sorted(x['slot'] for x in players) != list(range(1, 10)) or any(not x['name'] or not x['position'] for x in players):
            raise SourceError('incomplete_ordered_nine')
        out[side] = sorted(players, key=lambda x: x['slot'])
    return out
