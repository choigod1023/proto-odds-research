"""Detached, resumable KBO historical lineup research; no production writes."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests

ROOT = 'https://www.koreabaseball.com'
SCHEDULE = ROOT + '/ws/Main.asmx/GetKboGameList'
LINEUP = ROOT + '/ws/Schedule.asmx/GetLineUpAnalysis'
PROFILE = ROOT + '/Record/Player/HitterDetail/Basic.aspx'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class ProfileParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.names = []

    def handle_starttag(self, tag, attrs):
        if tag == 'span' and dict(attrs).get('id', '').endswith('playerProfile_lblName'):
            self.inside = True

    def handle_data(self, data):
        if self.inside:
            self.names.append(data.strip())

    def handle_endtag(self, tag):
        if tag == 'span' and self.inside:
            self.inside = False


def profile_name(raw):
    p = ProfileParser()
    p.feed(raw.decode('utf-8'))
    if len(p.names) != 1 or not p.names[0]:
        raise ValueError('profile_name_missing_or_ambiguous')
    return p.names[0]


def parse_lineup(raw, game):
    body = json.loads(raw)
    if not isinstance(body, list) or len(body) != 5 or not body[0] or type(body[0][0].get('LINEUP_CK')) is not bool:
        raise ValueError('invalid_lineup_envelope')
    if not body[0][0]['LINEUP_CK']:
        return None
    result = {}
    for side, m, t in [('home', 1, 3), ('away', 2, 4)]:
        meta = body[m][0]
        for k, v in [('G_ID', game['G_ID']), ('T_ID', game[side.upper()+'_ID']),
                     ('LE_ID', game['LE_ID']), ('SR_ID', game['SR_ID']), ('SEASON_ID', game['SEASON_ID'])]:
            if str(meta.get(k)) != str(v):
                raise ValueError('lineup_identity_mismatch:' + k)
        rows = json.loads(body[t][0])['rows']
        players = [{'order': int(row['row'][0]['Text']), 'position': row['row'][1]['Text'].strip(),
                    'name': row['row'][2]['Text'].strip()} for row in rows]
        if sorted(p['order'] for p in players) != list(range(1, 10)) or any(not p['position'] or not p['name'] for p in players):
            raise ValueError('invalid_nine')
        result[side] = sorted(players, key=lambda p: p['order'])
    return result


def publication_time_fields(raw):
    """Keep explicit source claims verbatim; they do not prove historical receipt."""
    found = []
    def visit(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                p = path + [str(key)]
                upper = str(key).upper()
                if ('ANNOUNC' in upper or 'PUBLISH' in upper) and ('TIME' in upper or 'DATE' in upper or upper.endswith(('_AT','_DT','_TM'))):
                    if isinstance(item,(str,int,float)) and not isinstance(item,bool) and item != '':
                        found.append({'path':p,'value':item})
                visit(item,p)
        elif isinstance(value, list):
            for index, item in enumerate(value): visit(item,path+[str(index)])
    visit(json.loads(raw), [])
    return found


def match_players(lineup, game_data):
    """IDs are an independently sourced *postgame* match until official profile checked."""
    seen = set()
    for side in ('home', 'away'):
        participants = game_data['data'][side]
        for p in lineup[side]:
            ids = {str(x['code']) for x in participants if str(x.get('name','')).strip() == p['name']
                   and x.get('order') == p['order'] and str(x.get('code','')).isdigit()}
            if len(ids) > 1:
                raise ValueError('ambiguous_or_missing_player_id')
            if not ids:
                # Abbreviated postgame names can differ from the official name.
                # All same-order candidates must be checked against KBO profiles.
                candidates = {str(x['code']) for x in participants if x.get('order') == p['order']
                              and str(x.get('code','')).isdigit()}
                if not candidates:
                    raise ValueError('ambiguous_or_missing_player_id')
                p['_candidate_ids'] = sorted(candidates)
                continue
            pid = ids.pop()
            if pid in seen:
                raise ValueError('duplicate_player_id')
            seen.add(pid)
            p['player_id'] = pid
            p['player_id_basis'] = 'same_game_side_name_order_postgame_crossmatch_pending_official_profile'
            p['_match_scope'] = 'same_game_side_order'
    return lineup


def build_team_index(source):
    index = {}
    for fid, game in source.items():
        if not isinstance(game, dict) or not re.search(r'\d{4}$', fid):
            continue
        season = fid[-4:]
        for side in ('home', 'away'):
            team = str(game.get(side) or '').strip()
            for player in (game.get('data') or {}).get(side, []):
                name = str(player.get('name') or '').strip()
                pid = str(player.get('code') or '')
                if team and name and pid.isdigit():
                    index.setdefault((season, team, name), set()).add(pid)
    return index


def match_from_team_season(lineup, game, team_index):
    for side in ('home', 'away'):
        team = game[side.upper()+'_NM'].strip()
        for p in lineup[side]:
            candidates = team_index.get((str(game['SEASON_ID']), team, p['name']), set())
            if len(candidates) != 1:
                raise ValueError('ambiguous_or_missing_team_season_player_id')
            p['_candidate_ids'] = sorted(candidates)
            p['_match_scope'] = 'other_game_same_team_season_name'
    return lineup


def select_schedule_game(games, gid, home, away):
    matches = [g for g in games if (g.get('G_ID') == gid if gid is not None else
               (g.get('HOME_NM','').strip(), g.get('AWAY_NM','').strip()) == (home, away))]
    if len(matches) != 1:
        raise ValueError('game_not_uniquely_in_official_schedule')
    return matches[0]


class StopCapture(Exception):
    pass


@contextmanager
def capture_lock(path):
    """An OS-held lock prevents two collectors sharing an output/cache directory."""
    path.mkdir(parents=True, exist_ok=True)
    lock = (path/'capture.lock').open('a+b')
    try:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            if __import__('os').name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise StopCapture('output_already_in_use') from exc
        yield
    finally:
        lock.close()


class Fetcher:
    def __init__(self, path, limit, delay):
        self.path = path
        self.limit = limit
        self.delay = delay
        self.requests = 0
        self.last = 0
        self.session = requests.Session()
        self.session.headers.update({'User-Agent':'Mozilla/5.0', 'Referer':ROOT+'/Schedule/GameCenter/Main.aspx'})
        (path/'requests').mkdir(parents=True, exist_ok=True)
        (path/'raw').mkdir(parents=True, exist_ok=True)

    def fetch(self, method, url, params):
        key = digest(json.dumps([method,url,params], sort_keys=True, ensure_ascii=False).encode())
        meta_path = self.path/'requests'/f'{key}.json'
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding='utf8'))
            raw = (self.path/'raw'/meta['sha256']).read_bytes()
            if digest(raw) != meta['sha256']:
                raise StopCapture('cached_sha256_mismatch')
            if meta.get('http_status') != 200:
                raise StopCapture('cached_http_status:'+str(meta.get('http_status')))
            return raw, meta
        if self.requests >= self.limit:
            raise StopCapture('request_limit')
        for attempt in range(4):
            if self.requests >= self.limit:
                raise StopCapture('request_limit')
            time.sleep(max(0, self.delay-(time.monotonic()-self.last)))
            self.requests += 1
            status={'phase':getattr(self,'phase','lineups'),'requests_started':self.requests,'url':url,'method':method,'attempt':attempt+1,'updated_at':datetime.now(timezone.utc).isoformat()}
            (self.path/'progress.json').write_text(json.dumps(status,ensure_ascii=False),encoding='utf8')
            try:
                r = self.session.request(method, url, params=params if method == 'GET' else None,
                                         data=params if method == 'POST' else None, timeout=(5, 15), allow_redirects=False)
                raw = r.content
            except requests.RequestException as e:
                self.last=time.monotonic()
                with (self.path/'request-failures.jsonl').open('a',encoding='utf8') as err:
                    err.write(json.dumps({**status,'error':type(e).__name__})+'\n')
                if attempt == 3: raise StopCapture('network_error:'+type(e).__name__) from e
                time.sleep(min(2**attempt,8));continue
            finally:
                self.last = time.monotonic()
            if r.status_code==429 or r.status_code>=500:
                with (self.path/'request-failures.jsonl').open('a',encoding='utf8') as err:
                    err.write(json.dumps({**status,'http_status':r.status_code,'sha256':digest(raw),'bytes':len(raw)})+'\n')
                (self.path/'raw'/digest(raw)).write_bytes(raw)
                if attempt == 3: raise StopCapture('http_status:'+str(r.status_code))
                time.sleep(min(2**attempt,8));continue
            break
        meta = {'url':url, 'method':method, 'params':params, 'http_status':r.status_code,
                'received_at':datetime.now(timezone.utc).isoformat(), 'sha256':digest(raw), 'bytes':len(raw)}
        (self.path/'raw'/meta['sha256']).write_bytes(raw)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, sort_keys=True), encoding='utf8')
        if r.status_code != 200:
            raise StopCapture('http_status:'+str(r.status_code))
        return raw, meta


def run(args):
    rows = [json.loads(x) for x in Path(args.rows).read_text(encoding='utf8').splitlines() if x.strip()]
    source = json.loads(Path(args.batters).read_text(encoding='utf8'))
    team_index = build_team_index(source)
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    output = target/'official-lineups.jsonl'
    previous = [json.loads(x) for x in output.read_text(encoding='utf8').splitlines()] if output.exists() else []
    done = {x['fixture_id'] for x in previous}
    if len(done) != len(previous):
        raise StopCapture('duplicate_fixture_in_output')
    f = Fetcher(target, args.max_requests, args.delay)
    reason = 'complete'
    count = 0
    for row in rows:
        fid = row['fixture_id']
        if fid in done or (args.fixture and fid not in args.fixture) or (args.only_season and row['season'] != args.only_season):
            continue
        schedule_meta = lineup_meta = None
        try:
            direct_id = bool(re.fullmatch(r'\d{8}[A-Z]{4}\d\d{4}', fid))
            score_match = re.fullmatch(r'score:(\d{4})-(\d{2})-(\d{2}):([^:]+):([^:]+)', fid)
            if direct_id:
                if fid[-4:] != str(row['season']):
                    raise ValueError('season_mismatch')
                gid, day = fid[:-4], fid[:8]
            elif score_match:
                day = ''.join(score_match.group(i) for i in (1,2,3))
                if day[:4] != str(row['season']) or (score_match.group(4),score_match.group(5)) != (row['home_id'],row['away_id']):
                    raise ValueError('score_fixture_identity_mismatch')
                gid = None
            else:
                raise ValueError('invalid_fixture_id')
            raw, schedule_meta = f.fetch('POST', SCHEDULE, {'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':day})
            payload = json.loads(raw)
            if str(payload.get('code')) != '100' or not isinstance(payload.get('game'),list):
                raise ValueError('invalid_schedule')
            game = select_schedule_game(payload['game'], gid, row['home_id'], row['away_id'])
            gid = game['G_ID']
            if str(game['G_DT']) != day or str(game['SEASON_ID']) != str(row['season']) or str(game['LE_ID']) != '1' or gid != day+game['AWAY_ID']+game['HOME_ID']+str(game['HEADER_NO']):
                raise ValueError('game_schedule_identity_mismatch')
            if row.get('kickoff_at'):
                local_start = datetime.fromisoformat(row['kickoff_at'])
                if local_start.utcoffset() is None or local_start.astimezone(timezone(timedelta(hours=9))).strftime('%Y%m%d %H:%M') != day+' '+game['G_TM']:
                    raise ValueError('kickoff_mismatch')
            params = {'leId':game['LE_ID'],'srId':game['SR_ID'],'seasonId':game['SEASON_ID'],'gameId':gid}
            raw, lineup_meta = f.fetch('POST', LINEUP, params)
            lineup = parse_lineup(raw, game)
            rec = {'fixture_id':fid, 'official_game_id':gid, 'game_date':day, 'home_team_id':game['HOME_ID'],
                   'away_team_id':game['AWAY_ID'], 'header_no':game['HEADER_NO'], 'league_id':game['LE_ID'],
                   'fixture_match_basis':'exact_official_game_id' if direct_id else 'unique_date_home_away_schedule_match',
                   'source_kickoff_available':bool(row.get('kickoff_at')),
                   'series_id':game['SR_ID'], 'season_id':game['SEASON_ID'], 'schedule_evidence':schedule_meta,
                   'lineup_evidence':lineup_meta, 'historical_publication_time':None,
                   'source_publication_time_fields':publication_time_fields(raw),
                   'historical_publication_time_proven':False, 'historical_receipt_proven':False,
                   'eligible_as_pregame_feature':False,
                   'lineup_status':'not_announced_or_recent_fallback' if lineup is None else 'official_flag_true',
                   'lineup_flag':lineup is not None, 'lineups':lineup,
                   'starting_pitcher_check':game.get('START_PIT_CK'),
                   'starting_pitchers': {side:{'player_id':str(game.get(prefix+'_PIT_P_ID') or ''),
                       'name':game.get(prefix+'_PIT_P_NM'), 'status':'schedule_field_historical_confirmation_unproven'}
                       for side,prefix in [('home','B'),('away','T')]}}
            if lineup is not None:
                source_id = gid + str(row['season'])
                try:
                    if source_id in source:
                        match_players(lineup, source[source_id])
                    else:
                        match_from_team_season(lineup, game, team_index)
                except (ValueError, KeyError, TypeError) as exc:
                        # Strip partial assignments; never leak a partial match as verified.
                        for players in lineup.values():
                            for p in players:
                                p.pop('player_id',None); p.pop('player_id_basis',None); p.pop('_candidate_ids',None); p.pop('_match_scope',None)
                        rec['lineup_status']='official_flag_true_player_ids_unavailable'
                        rec['player_id_error']=str(exc)
                else:
                        try:
                            all_ids = set()
                            for players in lineup.values():
                                for p in players:
                                    candidates = p.pop('_candidate_ids', None) or [p['player_id']]
                                    matched = []
                                    for pid in candidates:
                                        raw, profile = f.fetch('GET', PROFILE, {'playerId':pid})
                                        if profile_name(raw) == p['name']:
                                            matched.append((pid, profile))
                                    if len(matched) != 1 or matched[0][0] in all_ids:
                                        raise ValueError('official_profile_name_mismatch')
                                    p['player_id'], p['official_profile_evidence'] = matched[0]
                                    all_ids.add(p['player_id'])
                                    p['player_id_basis']='postgame_'+p.pop('_match_scope','same_game_side_order')+'_and_official_profile_name'
                            rec['lineup_status']='official_flag_true_profile_name_crossmatched'
                        except (ValueError, UnicodeError) as exc:
                            for players in lineup.values():
                                for p in players:
                                    p.pop('player_id',None); p.pop('player_id_basis',None)
                                    p.pop('official_profile_evidence',None); p.pop('_candidate_ids',None); p.pop('_match_scope',None)
                            rec['lineup_status']='official_flag_true_player_ids_unavailable'
                            rec['player_id_error']=str(exc)
            with output.open('a',encoding='utf8',newline='\n') as out:
                out.write(json.dumps(rec,ensure_ascii=False,sort_keys=True)+'\n')
            count += 1
        except StopCapture as exc:
            reason = str(exc)
            break
        except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
            rec={'fixture_id':fid,'lineup_status':'unresolved','reason':str(exc)[:200],
                 'schedule_evidence':schedule_meta,'lineup_evidence':lineup_meta,
                 'eligible_as_pregame_feature':False}
            with output.open('a',encoding='utf8',newline='\n') as out:
                out.write(json.dumps(rec,ensure_ascii=False,sort_keys=True)+'\n')
            count += 1
    captured = [json.loads(x) for x in output.read_text(encoding='utf8').splitlines()] if output.exists() else []
    counts = {}
    for item in captured:
        status = item['lineup_status']
        counts[status] = counts.get(status, 0) + 1
    selected = [r for r in rows if (not args.fixture or r['fixture_id'] in args.fixture)
                and (not args.only_season or r['season'] == args.only_season)]
    selected_done = sum(r['fixture_id'] in {x['fixture_id'] for x in captured} for r in selected)
    manifest = {'denominator':len(rows), 'processed':len(captured), 'unprocessed':len(rows)-len(captured),
                'selection_rule':{'only_season':args.only_season,'fixture_ids':args.fixture},
                'selected_denominator':len(selected), 'selected_processed':selected_done,
                'selected_unprocessed':len(selected)-selected_done,
                'status_counts':counts, 'completed_this_run':count, 'requests_this_run':f.requests,
                'stop_reason':reason, 'rows_sha256':digest(Path(args.rows).read_bytes()),
                'batters_sha256':digest(Path(args.batters).read_bytes()),
                'lineups_sha256':digest(output.read_bytes()) if output.exists() else None,
                'historical_publication_time_proven':0, 'historical_receipt_proven':0,
                'notes':'Player IDs use postgame same-game or same-team-season crossmatch and official current profile name; publication time and revision history are unproven.'}
    (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8')
    print(json.dumps(manifest,ensure_ascii=False))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--rows',required=True); p.add_argument('--batters',required=True); p.add_argument('--output',required=True)
    p.add_argument('--max-requests',type=int,default=100); p.add_argument('--delay',type=float,default=1.0)
    p.add_argument('--fixture',action='append')
    p.add_argument('--only-season',type=int,default=None)
    a=p.parse_args()
    if a.max_requests < 1 or a.delay < 1.5: p.error('max-requests must be positive and delay at least 1.5 seconds')
    with capture_lock(Path(a.output)):
        run(a)
