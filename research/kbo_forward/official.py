"""Bounded, evidence-preserving public KBO feed for research only.

No inferred batting identity, same-day performance, or postgame backfill is used.
"""
import base64
import hashlib
import importlib.util
import json
import re
import time
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = 'https://www.koreabaseball.com'
SCHEDULE = BASE + '/ws/Main.asmx/GetKboGameList'
LINEUP = BASE + '/ws/Schedule.asmx/GetLineUpAnalysis'
BOX = BASE + '/ws/Schedule.asmx/GetBoxScore'
PROFILE = BASE + '/Record/Player/HitterDetail/Basic.aspx'
SEARCH = BASE + '/Player/Search.aspx'
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
        if url not in (SCHEDULE, LINEUP, BOX, PROFILE, SEARCH):
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
            rec = {'url': url, 'method': 'GET' if url in (PROFILE, SEARCH) else 'POST', 'params': params, 'requested_at': now_utc().isoformat(), 'attempt': attempt + 1}
            try:
                response = (self.session.get(url, params=params, timeout=(5, 15), allow_redirects=False) if url in (PROFILE, SEARCH) else self.session.post(url, data=params, timeout=(5, 15), allow_redirects=False))
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

    def verified_body(self, rec, expected_url):
        if rec.get('status') != 200 or rec.get('url') != expected_url:
            raise SourceError('receipt_endpoint_or_status_mismatch')
        try:
            stamp = datetime.fromisoformat(rec['finished_at'])
            if stamp.utcoffset() is None or stamp > now_utc():
                raise ValueError()
            filename = rec['body_file']
            if filename != 'bodies/' + rec['sha256'] + '.bin':
                raise ValueError()
            raw = (self.root / filename).read_bytes()
            if sha(raw) != rec['sha256']:
                raise ValueError()
            return raw
        except (KeyError, ValueError, TypeError, OSError):
            raise SourceError('receipt_or_body_invalid')

    def resolve_lineups(self, lineup, game):
        """Search exact names, requiring a unique active ID on the official team.

        Incomplete search pages or ambiguous same-name players are never guessed.
        """
        result, receipts, seen = {}, [], set()
        for side in ('home', 'away'):
            result[side] = []
            for player in lineup[side]:
                raw, rec = self.fetch(SEARCH, {'searchWord': player['name']}, refresh_seconds=300)
                pid = parse_player_search(raw, player['name'], str(game[side.upper() + '_NM']).strip())
                if pid in seen:
                    raise SourceError('duplicate_batter_id')
                seen.add(pid)
                result[side].append(pid)
                receipts.append(rec)
        return result, receipts

    def history_delta(self, dates, team_ids, target_day):
        """Collect each previous-day schedule and completed team box with receipts.

        The caller must choose dates covering the gap from its seed. A missing or
        in-progress box raises rather than certifying a partial day as complete.
        """
        target_day = date.fromisoformat(target_day) if isinstance(target_day, str) else target_day
        parsed = [date.fromisoformat(d) if isinstance(d, str) else d for d in dates]
        if len(set(parsed)) != len(parsed) or any(d >= target_day for d in parsed):
            raise SourceError('history_dates_not_strictly_before_target')
        if parsed and (max(parsed) != target_day - timedelta(days=1) or len(parsed) != (max(parsed) - min(parsed)).days + 1):
            raise SourceError('history_dates_have_gap')
        day_records = []
        for day in sorted(parsed):
            schedule = self.schedule([day])
            games = []
            for item in schedule['games']:
                g = item['source']
                if not ({g.get('HOME_ID'), g.get('AWAY_ID')} & set(team_ids)):
                    continue
                cancel, state, result = str(g.get('CANCEL_SC_ID')), str(g.get('GAME_STATE_SC')), str(g.get('GAME_RESULT_CK'))
                if cancel != '0':
                    raise SourceError('history_cancel_or_suspension_unresolved')
                if state != '3' or result != '1':
                    raise SourceError('history_game_not_final')
                params = {'leId': g['LE_ID'], 'srId': g['SR_ID'], 'seasonId': g['SEASON_ID'], 'gameId': g['G_ID']}
                raw, rec = self.fetch(BOX, params, refresh_seconds=86400)
                obj = json.loads(raw)
                if not isinstance(obj, dict) or not isinstance(obj.get('tables'), list) or not obj['tables']:
                    raise SourceError('history_box_invalid')
                games.append({'game': item, 'box_receipt': rec})
            day_records.append({'date': day.isoformat(), 'schedule_receipt': schedule['receipts'][0], 'games': games})
        return {'target_date': target_day.isoformat(), 'days': day_records}

    def snapshot(self, game, history=None, build_row=None, verify_history=None):
        """Return source announcement and explicit missing fields; never invent IDs."""
        g, schedule_rec = game['source'], game['receipt']
        schedule_body = json.loads(self.verified_body(schedule_rec, SCHEDULE))
        if (schedule_rec.get('params') or {}).get('date') != g.get('G_DT'):
            raise SourceError('schedule_request_date_mismatch')
        matches = [x for x in schedule_body.get('game', []) if x.get('G_ID') == g.get('G_ID')]
        if len(matches) != 1 or matches[0] != g:
            raise SourceError('schedule_body_identity_mismatch')
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
        ids, identity_receipts = {}, []
        if ordered is not None:
            try:
                ids, identity_receipts = self.resolve_lineups(ordered, g)
            except SourceError as exc:
                missing.append('batter_identity:' + str(exc))
        if now_utc() > kickoff.astimezone(timezone.utc) - timedelta(minutes=30):
            missing.append('identity_after_t_minus_30')
        announcement_verified = not missing
        answer = {'source_verified': False, 'announcement_verified': announcement_verified, 'missing': list(missing), 'game': game, 'kickoff_at': kickoff.isoformat(), 'starters': starters, 'lineups': ordered, 'batter_ids': ids, 'lineup_receipt': rec, 'identity_receipts': identity_receipts}
        if not announcement_verified:
            return answer
        if history is None or build_row is None or verify_history is None:
            answer['missing'].append('verified_history_features_not_attached')
            return answer
        event = {'official_game_id': g['G_ID'], 'season': int(g['SEASON_ID']), 'kickoff_at': kickoff.isoformat(), 'home_id': str(g['HOME_NM']).strip(), 'away_id': str(g['AWAY_NM']).strip()}
        roster = {side: {'starter': starters[side]['player_id'], 'hitters': ids[side]} for side in ('home', 'away')}
        if not self.verify_history_coverage(event, history):
            answer['missing'].append('history_coverage_not_verified')
            return answer
        if verify_history(event, roster, history, self) is not True:
            answer['missing'].append('history_evidence_not_verified')
            return answer
        history_receipts = history.get('source_receipts') or []
        if not history_receipts:
            answer['missing'].append('history_source_receipts_missing')
            return answer
        stamp = now_utc()
        row = build_row(event, roster, history, stamp.isoformat())
        if row.get('roster') != roster or row.get('fixture_id') != g['G_ID'] + str(g['SEASON_ID']) or row.get('home_id') != event['home_id'] or row.get('away_id') != event['away_id']:
            raise SourceError('feature_row_identity_mismatch')
        validate_feature_row(row, event, history)
        if now_utc() > kickoff.astimezone(timezone.utc) - timedelta(minutes=30):
            answer['missing'].append('feature_build_after_t_minus_30')
            return answer
        announcement = {'lineup_confirmed':True, 'pitcher_announcement_confirmed':True, 'basis':'official_confirmed_pregame_announcement', 'roster':roster}
        evidence = {'announcement': self.bundle([schedule_rec, rec] + identity_receipts), 'history': self.bundle(history_receipts, history)}
        if now_utc() > kickoff.astimezone(timezone.utc) - timedelta(minutes=30):
            answer['missing'].append('evidence_bundle_after_t_minus_30')
            return answer
        payload = {'event': event, 'row': row, 'announcement': announcement, 'evidence': evidence}
        answer.update(source_verified=True, event=event, row=row, announcement=announcement, evidence=evidence, payload=payload, captured_at=stamp.isoformat())
        return answer


    def verify_history_coverage(self, event, history):
        """Reconcile every date after the seed against official source bytes.

        The caller-supplied verifier must additionally authenticate seed bytes
        and every normalized statistic against its box response.
        """
        seed = history.get('seed')
        if not isinstance(seed, dict):
            return False
        day = datetime.fromisoformat(event['kickoff_at']).astimezone(KST).date()
        try:
            seed_received = datetime.fromisoformat(seed['received_at'])
            if seed_received.utcoffset() is None or seed_received > now_utc():
                return False
            bounds = {team: date.fromisoformat(seed.get('team_through', {}).get(team, seed['through_date'])) for team in (event['home_id'], event['away_id'])}
            if any(d >= day for d in bounds.values()):
                return False
            schedules, boxes = {}, {}
            for rec in history.get('source_receipts') or []:
                url = rec.get('url')
                if url not in (SCHEDULE, BOX):
                    continue
                raw = self.verified_body(rec, url)
                params = rec.get('params') or {}
                if url == SCHEDULE:
                    d = datetime.strptime(params['date'], '%Y%m%d').date()
                    if d in schedules:
                        return False
                    obj = json.loads(raw)
                    if str(obj.get('code')) != '100' or not isinstance(obj.get('game'), list):
                        return False
                    schedules[d] = obj['game']
                else:
                    ident = params['gameId']
                    if ident in boxes:
                        return False
                    obj = json.loads(raw)
                    if not isinstance(obj, dict) or not isinstance(obj.get('tables'), list) or not obj['tables']:
                        return False
                    boxes[ident] = rec
            normalized = {g['fixture_id']:g for g in history.get('games', [])}
            if len(normalized) != len(history.get('games', [])):
                return False
            current = min(bounds.values()) + timedelta(days=1)
            while current < day:
                if current not in schedules:
                    return False
                for g in schedules[current]:
                    if g.get('G_DT') != current.strftime('%Y%m%d') or str(g.get('LE_ID')) != '1':
                        return False
                    home, away = str(g.get('HOME_NM') or '').strip(), str(g.get('AWAY_NM') or '').strip()
                    needed = [team for team in bounds if team in (home, away) and current > bounds[team]]
                    if not needed:
                        continue
                    if str(g.get('CANCEL_SC_ID')) != '0':
                        if '취소' in str(g.get('CANCEL_SC_NM') or ''):
                            continue
                        return False
                    if str(g.get('GAME_STATE_SC')) != '3' or str(g.get('GAME_RESULT_CK')) != '1':
                        return False
                    gid = g['G_ID']; fid = gid + str(g['SEASON_ID'])
                    if gid not in boxes or fid not in normalized:
                        return False
                    params = boxes[gid]['params']
                    if any(str(params.get(k)) != str(g.get(v)) for k,v in (('leId','LE_ID'),('srId','SR_ID'),('seasonId','SEASON_ID'))):
                        return False
                    row = normalized[fid]
                    if row.get('date') != current.isoformat() or any(team not in row.get('teams', {}) for team in needed):
                        return False
                    alias = 'score:' + current.isoformat() + ':' + home + ':' + away
                    if any(z.get('fixture_id') == alias for z in seed.get('games', [])):
                        return False
                current += timedelta(days=1)
            return True
        except (SourceError, KeyError, ValueError, TypeError, OSError, json.JSONDecodeError):
            return False

    def bundle(self, receipts, history=None):
        records = []
        for rec in receipts:
            if rec.get('url') not in (SCHEDULE, LINEUP, BOX, PROFILE, SEARCH):
                raise SourceError('unapproved_evidence_endpoint')
            raw = self.verified_body(rec, rec['url'])
            records.append({'receipt': rec, 'body_base64': base64.b64encode(raw).decode('ascii')})
        if not records:
            raise SourceError('empty_evidence_bundle')
        body = {'responses': records}
        if history is not None:
            body['normalized_history'] = history
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')
        received_at = now_utc().isoformat() if history is not None else max((x['receipt']['finished_at'] for x in records), key=datetime.fromisoformat)
        return {'uri': records[0]['receipt']['url'], 'received_at': received_at, 'body': body, 'sha256': sha(encoded)}

    def result(self, game):
        g = game['source']
        found = [x for x in self.schedule([datetime.strptime(g['G_DT'], '%Y%m%d').date()])['games'] if x['source'].get('G_ID') == g['G_ID']]
        if len(found) != 1:
            return {'verified': False, 'missing': ['unique_official_result']}
        source = found[0]['source']
        if any(str(source.get(k)) != str(g.get(k)) for k in ('G_DT', 'HOME_ID', 'AWAY_ID', 'LE_ID', 'SR_ID', 'SEASON_ID')):
            return {'verified': False, 'missing': ['result_identity_mismatch']}
        cancel_name = str(source.get('CANCEL_SC_NM') or '').strip()
        if str(source.get('CANCEL_SC_ID')) != '0' and '취소' in cancel_name:
            return {'verified': True, 'status': 'cancelled', 'official_reason': cancel_name, 'game': found[0]}
        if str(source.get('GAME_STATE_SC')) != '3' or str(source.get('GAME_RESULT_CK')) != '1' or str(source.get('CANCEL_SC_ID')) != '0':
            return {'verified': False, 'missing': ['completed_official_result']}
        # Schedule schema varies; scores are never inferred from team names or text.
        try:
            home, away = int(source['B_SCORE_CN']), int(source['T_SCORE_CN'])
            if home < 0 or away < 0:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            return {'verified': False, 'missing': ['official_score_fields'], 'game': found[0]}
        return {'verified': True, 'status': 'final', 'home_score': home, 'away_score': away, 'game': found[0]}


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


def parse_player_search(raw, name, team_name):
    """Only accept a complete official result page and one current team match."""
    soup = BeautifulSoup(raw, 'html.parser')
    root = soup.select_one('div.inquiry')
    if root is None:
        raise SourceError('player_search_page_invalid')
    count = root.select_one('p.title span.point')
    rows = root.select('table.tEx tbody tr')
    if count is None or not count.get_text(strip=True).isdigit() or int(count.get_text(strip=True)) != len(rows):
        raise SourceError('player_search_page_incomplete')
    matches = []
    for row in rows:
        cells = row.find_all('td', recursive=False)
        if len(cells) < 3:
            raise SourceError('player_search_row_invalid')
        a = cells[1].find('a', href=True)
        if not a or a.get_text(strip=True) != name or cells[2].get_text(strip=True) != team_name:
            continue
        url = urlparse(a['href'])
        ids = parse_qs(url.query).get('playerId', [])
        if url.scheme or url.netloc or url.path != '/Record/Player/HitterDetail/Basic.aspx' or len(ids) != 1 or not ids[0].isdigit() or int(ids[0]) <= 0:
            continue
        matches.append(ids[0])
    if len(matches) != 1:
        raise SourceError('player_search_missing_or_ambiguous')
    return matches[0]


def validate_feature_row(row, event, history):
    path = Path(__file__).resolve().parents[2] / 'experiments/kbo-m0-followup-20261103/vendor/evolution/common/vendor/original_adapter.py'
    spec = importlib.util.spec_from_file_location('official_frozen_scalar', path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    raw, scalar = row.get('raw_features', {}), row.get('features', {})
    if set(raw) != set(adapter.SOURCE) or set(scalar) != set(adapter.SOURCE):
        raise SourceError('feature_set_not_frozen')
    if row.get('season') != event['season'] or row.get('event_date') != datetime.fromisoformat(event['kickoff_at']).astimezone(KST).date().isoformat():
        raise SourceError('feature_date_mismatch')
    known = {}
    for game in ((history.get('seed') or {}).get('games') or []) + (history.get('games') or []):
        ident, day = game['fixture_id'], date.fromisoformat(game['date'])
        if ident in known and known[ident] != day:
            raise SourceError('history_identity_date_conflict')
        known[ident] = day
    adapter.check_nested(raw, row['fixture_id'], date.fromisoformat(row['event_date']), known)
    for key in adapter.SOURCE:
        if adapter.scalar(key, raw[key]['value']) != scalar[key]['value']:
            raise SourceError('feature_scalar_mismatch')
    for side in ('home', 'away'):
        lineup = raw[side + '_lineup_strength']['value']
        starter = raw[side + '_starter_strength']['value']
        if lineup is not None and ([p['player_id'] for p in lineup] != row['roster'][side]['hitters'] or any(len(p.get('history') or []) > 10 for p in lineup)):
            raise SourceError('feature_lineup_owner_or_window_mismatch')
        if starter is not None and (starter['projected_player_id'] != row['roster'][side]['starter'] or len(starter['appearances']) > 10):
            raise SourceError('feature_starter_owner_or_window_mismatch')
