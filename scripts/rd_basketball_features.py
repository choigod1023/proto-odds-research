"""Isolated basketball workload extraction; no outcomes, model fitting or production IO.

Historical ESPN NBA is a third-party proxy; KBL is the official public API.
Captured today never establishes historical pregame observability.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/pregame-rd-basketball-20261030'
BASE = Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees')
ROSTERS = BASE / 'two-leg-model-literature-20261030/experiments/two-leg-literature-20261030'
ARCHIVE = BASE / 'single-vs-low-odds-parlays-20261030/experiments/single-parlay-20261030/inputs/ARCHIVE/events.jsonl.gz'
KBL_HEADERS = {'Channel': 'WEB', 'TeamCode': 'XX', 'lang': 'ko', 'X-Requested-With': 'XMLHttpRequest'}
FEATURES = ['player_top5_minutes_72h', 'player_peak_minutes_72h', 'player_squared_load_72h', 'ot_team_minutes_72h']
PROTOCOL = {'version': 1, 'window_hours': 72, 'features_per_side': FEATURES,
    'squared_load': 'sum over players of (cumulative 72h minutes / league regulation minutes)^2',
    'ot_team_minutes': '5 * 5 * number of overtime periods; not individual OT minutes',
    'clock': {'NBA': 48, 'KBL': 40, 'overtime': 5},
    'gate': 'game start in [decision-72h,decision); actual final end strictly before decision; exclude target',
    'missing': 'omit feature keys, never coerce missing minutes or absent boxes to zero',
    'injury': 'missing: no authenticated historical pregame announcement archive',
    'archive': 'reuse fetched sources only; retrospective settled-only secondary training/diagnostic',
    'selection': 'collect all prior games for actual roster windows, bounded HTTP budget; no ROI inspected'}

def dt(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('aware timestamp required')
    return value

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')

def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf8') as f:
        return [json.loads(line) for line in f]

def write_rows(path, rows):
    with gzip.open(path, 'wt', encoding='utf8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')

def frame(row):
    return {k: row[k] for k in ('cohort', 'event_id', 'league', 'kickoff', 'home', 'away', 'available_at') if k in row}

def decision(row):
    k = dt(row['kickoff'])
    d = dt(row['available_at']) if row.get('available_at') else k - timedelta(minutes=30)
    if d >= k:
        raise ValueError('decision must precede kickoff')
    return d

class Intake:
    """Bounded public GET, append-only provenance, no credentials, no redirects."""
    def __init__(self, out, budget=220):
        self.out, self.budget, self.count = out, budget, 0
        self.entries = []
        self.cache = {}
        ledger = out / 'sources.jsonl'
        if ledger.exists():
            self.entries = [json.loads(x) for x in ledger.read_text(encoding='utf8').splitlines()]
            self.cache = {r['url']: r for r in self.entries if r.get('status') == 200}

    def get(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in {'api.kbl.or.kr', 'cdn.nba.com', 'site.api.espn.com'} or parsed.username or parsed.password or parsed.port:
            raise ValueError('public source allowlist only')
        if url in self.cache:
            meta = self.cache[url]
            raw = gzip.decompress((self.out / meta['raw_path']).read_bytes())
            if sha(raw) != meta['sha256']:
                raise ValueError('source digest mismatch')
            return json.loads(raw), meta
        if self.count >= self.budget:
            return None, {'error': 'request_budget', 'url': url}
        self.count += 1
        meta = {'url': url, 'observed_at': datetime.now(timezone.utc).isoformat(),
                'source_kind': 'official_kbl' if parsed.hostname == 'api.kbl.or.kr' else 'espn_nba_proxy' if parsed.hostname == 'site.api.espn.com' else 'official_nba'}
        try:
            # trust_env=False avoids local authentication/proxy environment settings.
            with requests.Session() as session:
                session.trust_env = False
                with session.get(url, headers=KBL_HEADERS if parsed.hostname == 'api.kbl.or.kr' else {},
                                 timeout=(5, 15), allow_redirects=False, stream=True) as response:
                    raw = b''
                    for chunk in response.iter_content(65536):
                        raw += chunk
                        if len(raw) > 8_000_000:
                            raise ValueError('payload exceeds 8MB')
                    meta.update(status=response.status_code, sha256=sha(raw), bytes=len(raw))
            meta['observed_at'] = datetime.now(timezone.utc).isoformat()
            meta['source_id'] = sha(url.encode() + raw)[:24]
            meta['raw_path'] = 'raw/' + meta['source_id'] + '.gz'
            (self.out / 'raw').mkdir(parents=True, exist_ok=True)
            (self.out / meta['raw_path']).write_bytes(gzip.compress(raw, mtime=0))
            result = json.loads(raw) if meta['status'] == 200 else None
        except (requests.RequestException, ValueError) as e:
            meta['error'] = type(e).__name__
            result = None
        self.entries.append(meta)
        with (self.out / 'sources.jsonl').open('a', encoding='utf8') as f:
            f.write(json.dumps(meta) + '\n')
        if result is not None:
            self.cache[url] = meta
        return result, meta

def minutes(value):
    if value is None or value in ('', '--', '-', 'DNP'):
        return None
    if isinstance(value, str) and ':' in value:
        a, b = value.split(':')
        return float(a) + float(b) / 60
    v = float(value)
    return v if v >= 0 else None

def normalize_espn(data, meta):
    comp = data['header']['competitions'][0]
    if not comp['status']['type'].get('completed'):
        raise ValueError('not final')
    end_plays = [p for p in data.get('plays', []) if p.get('type', {}).get('text') == 'End Game' and p.get('wallclock')]
    if not end_plays:
        raise ValueError('no actual final wallclock')
    last = end_plays[-1]
    periods = int(last['period']['number'])
    teams = {}
    for team in data.get('boxscore', {}).get('players', []):
        players = {}
        for stats in team.get('statistics', []):
            if 'MIN' not in stats.get('names', []):
                continue
            idx = stats['names'].index('MIN')
            for athlete in stats.get('athletes', []):
                vals = athlete.get('stats', [])
                if athlete.get('didNotPlay') and not vals:
                    continue  # Explicit DNP is excluded; unknown active minutes remain missing.
                # A DNP with no explicit minutes is missing, not a synthetic zero.
                players[str(athlete['athlete']['id'])] = minutes(vals[idx]) if len(vals) > idx else None
        teams[team['team']['displayName']] = players
    return {'league': 'NBA', 'game_id': str(comp['id']), 'start': comp['date'],
            'end': last['wallclock'], 'periods': periods, 'teams': teams,
            'source_meta': [meta]}

def normalize_kbl(info, players, metas):
    g = info['game']
    if not g.get('isEnded') or not g.get('gameEnd'):
        raise ValueError('not final/no end')
    def stamp(clock):
        return datetime.strptime(g['gameDate'] + str(clock).zfill(4), '%Y%m%d%H%M').replace(tzinfo=timezone(timedelta(hours=9)))
    start, end = stamp(g['gameStart']), stamp(g['gameEnd'])
    if end < start:
        end += timedelta(days=1)
    teams = {str(g['tcodeH']): {}, str(g['tcodeA']): {}}
    for p in players:
        rec, identity = p['records'], p['player']
        t = str(identity['tcode'])
        if t not in teams:
            raise ValueError('player team differs')
        m, s = rec.get('playMin'), rec.get('playSec')
        teams[t][str(identity['pcode'])] = None if m is None or s is None else float(m) + float(s)/60
    ot_home = info['teamrecords']['home'].get('scoreeq')
    ot_away = info['teamrecords']['away'].get('scoreeq')
    if not isinstance(ot_home, list) or ot_home is None or ot_away is None or len(ot_home) != len(ot_away):
        raise ValueError('missing overtime periods')
    return {'league': 'KBL', 'game_id': g['gmkey'], 'start': start.isoformat(),
            'end': end.isoformat(), 'periods': 4 + len(ot_home), 'teams': teams, 'source_meta': metas}

def aggregate(games, league, team, cutoff, target_id):
    """Strictly past complete player boxes; no target lineup or season totals."""
    reg = {'NBA': 48, 'KBL': 40}[league]
    selected = [g for g in games if g['league'] == league and team in g['teams']
                and dt(g['start']) >= cutoff - timedelta(hours=72) and dt(g['start']) < cutoff
                and g['game_id'] != target_id.removeprefix('nba:')]
    if not selected:
        return {}, {}, 'no_prior_box_in_72h'
    if any(dt(g['end']) >= cutoff for g in selected):
        return {}, {}, 'prior_game_not_finished_at_decision'
    totals = defaultdict(float)
    for g in selected:
        players = g['teams'][team]
        if not players or any(v is None for v in players.values()):
            return {}, {}, 'missing_player_minutes'
        # ESPN rounded minutes tolerate <= number of players / 2 minutes.
        expected = 5 * (reg + max(0, g['periods']-4)*5)
        if any(v < 0 or v > reg + max(0,g['periods']-4)*5 for v in players.values()) or abs(sum(players.values()) - expected) > max(1, len(players)*.51):
            return {}, {}, 'incomplete_or_invalid_minutes_total'
        for player, value in players.items():
            totals[player] += value
    ordered = sorted(totals.values(), reverse=True)
    vals = dict(zip(FEATURES, [sum(ordered[:5]), ordered[0], sum((v/reg)**2 for v in ordered),
                             sum(25*max(0,g['periods']-4) for g in selected)]))
    metas = [m for g in selected for m in g['source_meta']]
    latest_fact = max(dt(g['end']) for g in selected)
    observed = max(dt(m['observed_at']) for m in metas)
    evidence = {'max_fact_time': latest_fact.isoformat(), 'observed_at': observed.isoformat(),
                'source_sha256': sorted({m['sha256'] for m in metas}),
                'availability_tier': 'captured_pregame' if observed <= cutoff else 'retrospective_lagged',
                'note': 'Past game actual end; current capture is not historical availability. OT is team exposure, not individual OT minutes.'}
    return vals, {'evidence': evidence, 'source_ids': sorted({m['source_id'] for m in metas})}, None

NBA_FULL = ['Charlotte Hornets','Washington Wizards','Cleveland Cavaliers','Minnesota Timberwolves','Boston Celtics','Chicago Bulls','Miami Heat','Dallas Mavericks','Milwaukee Bucks','Indiana Pacers','Oklahoma City Thunder','Phoenix Suns','Houston Rockets','Atlanta Hawks','Utah Jazz','Memphis Grizzlies','Denver Nuggets','Golden State Warriors','Los Angeles Lakers','LA Clippers','Brooklyn Nets','New York Knicks','Orlando Magic','Toronto Raptors','Philadelphia 76ers','Detroit Pistons','Sacramento Kings','New Orleans Pelicans','Portland Trail Blazers','San Antonio Spurs']
NBA_SHORT = '샬럿호네 워싱위저 클리캐벌 미네울브 보스셀틱 시카불스 마이히트 댈러매버 밀워벅스 인디페이 오클썬더 피닉선즈 휴스로케 애틀호크 유타재즈 멤피그리 덴버너게 골든워리 LA레이커 LA클리퍼 브루네츠 뉴욕닉스 올랜매직 토론랩터 필라76s 디트피스 새크킹스 뉴올펠리 포틀트레 샌안스퍼'.split()
NBA_MAP = dict(zip(NBA_SHORT, NBA_FULL))
NBA_MAP['Los Angeles Clippers'] = 'LA Clippers'
KBL_MAP = {'서울삼성':'35','서울SK':'55','고양소노':'66','안양정관':'70','원주DB':'16','부산KCC':'60','수원KT':'06','KT소닉붐':'06','울산모비':'10','대구한국':'64','한국가스':'64','창원LG':'50'}

def team_key(row, side, identities):
    name = row[side]
    if row['league'] == 'NBA':
        return NBA_MAP.get(name, name)
    return identities.get(name, KBL_MAP.get(name, name))

def extract(rows, games, identities, schedule, out, broad=False, coverage=None):
    outputs, audit = [], []
    for row in rows:
        if row.get('league') not in ('NBA', 'KBL'):
            continue
        cutoff = decision(row)
        result = {k: row[k] for k in ('cohort','event_id','league','kickoff')}
        result.update(decision_at=cutoff.isoformat(), features={}, feature_evidence={}, source_ids=[])
        for side in ('home','away'):
            team = team_key(row, side, identities)
            calendar_cutoff=cutoff.astimezone(timezone.utc if row['league']=='NBA' else timezone(timedelta(hours=9)))
            covered = coverage is None or all(
                (calendar_cutoff-timedelta(hours=72)+timedelta(hours=i)).date().isoformat() in coverage.get(row['league'], set())
                for i in range(73))
            # Require all scheduled prior games to have valid normalized boxes.
            expected = [s for s in schedule if s['league'] == row['league'] and team in s['teams']
                        and cutoff-timedelta(hours=72) <= dt(s['start']) < cutoff
                        and s['game_id'] != row['event_id'].removeprefix('nba:')]
            ids = {(g['league'],g['game_id']) for g in games}
            missing = [s['game_id'] for s in expected if (s['league'],s['game_id']) not in ids]
            vals, ev, reason = ({},{},'schedule_window_not_covered') if not covered else (({},{},'missing_scheduled_box') if missing else aggregate(games,row['league'],team,cutoff,row['event_id']))
            if vals:
                for key, value in vals.items():
                    name = side + '_' + key
                    result['features'][name] = value
                    result['feature_evidence'][name] = ev['evidence']
                result['source_ids'].extend(ev['source_ids'])
            audit.append({'cohort':row['cohort'],'event_id':row['event_id'],'league':row['league'],
                          'side':side,'reason':reason,'missing_game_ids':missing,
                          'announced_absence_minutes_lost':'missing_no_pregame_announcement_history'})
        result['source_ids'] = sorted(set(result['source_ids']))
        if result['features'] or not broad:
            outputs.append(result)
    write_rows(out, outputs)
    return outputs, audit

def collect(intake, rows):
    games, schedule, identities, errors = [], [], {}, []
    coverage={'NBA':set(),'KBL':set()}
    nba = [r for r in rows if r['league']=='NBA']
    # This public NBA endpoint rejects ranges. Daily source dates are US Eastern;
    # one extra day conservatively covers the Korean/UTC calendar boundary.
    dates = sorted({(decision(r).astimezone(timezone.utc)-timedelta(days=i)).date() for r in nba for i in range(5)})
    chunks=[[d] for d in dates]
    fetched_dates=set()
    candidates={}
    for days in chunks:
        span=days[0].strftime('%Y%m%d')
        data, meta=intake.get('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?limit=1000&dates='+span)
        if data is None:
            errors.append({'stage':'nba_schedule','range':span,'reason':meta.get('error',meta.get('status'))}); continue
        fetched_dates.update(days)
        for event in data.get('events',[]):
            comp=event['competitions'][0]
            s={'league':'NBA','game_id':event['id'],'start':event['date'],
               'teams':[t['team']['displayName'] for t in comp['competitors']], 'source_meta':meta}
            schedule.append(s)
            if any(team_key(r,'home',{}) in s['teams'] or team_key(r,'away',{}) in s['teams'] for r in nba if decision(r)-timedelta(hours=72)<=dt(s['start'])<decision(r)):
                candidates[s['game_id']]=s
    # A full UTC calendar day needs both corresponding US schedule dates.
    coverage['NBA'].update(d.isoformat() for d in fetched_dates if d-timedelta(days=1) in fetched_dates)
    # KBL first: prioritize its smaller official sample before NBA proxy requests.
    kbl=[r for r in rows if r['league']=='KBL']
    if kbl:
        lo=(min(decision(r) for r in kbl)-timedelta(days=4)).strftime('%Y%m%d')
        hi=max(decision(r) for r in kbl).strftime('%Y%m%d')
        data,meta=intake.get(f'https://api.kbl.or.kr/match/list?fromDate={lo}&toDate={hi}&tcodeList=all')
        if data is not None:
            day=datetime.strptime(lo,'%Y%m%d').date()
            while day<=datetime.strptime(hi,'%Y%m%d').date():
                coverage['KBL'].add(day.isoformat()); day+=timedelta(days=1)
            entries=data if isinstance(data,list) else data.get('list',data.get('games',[]))
            for entry in entries:
                for suffix in ('H','A'):
                    for key in ('tname','tnameF'):
                        if entry.get(key+suffix): identities[entry[key+suffix]]=str(entry['tcode'+suffix])
            for entry in entries:
                try:
                    start=datetime.strptime(entry['gameDate']+str(entry['gameStart']).zfill(4),'%Y%m%d%H%M').replace(tzinfo=timezone(timedelta(hours=9)))
                    teams=[str(entry['tcodeH']),str(entry['tcodeA'])]
                    s={'league':'KBL','game_id':entry['gmkey'],'start':start.isoformat(),'teams':teams}
                    schedule.append(s)
                    if not any(decision(r)-timedelta(hours=72)<=start<decision(r) and (team_key(r,'home',identities) in teams or team_key(r,'away',identities) in teams) for r in kbl): continue
                    info,im=intake.get('https://api.kbl.or.kr/match/'+entry['gmkey'])
                    players,pm=intake.get('https://api.kbl.or.kr/match/'+entry['gmkey']+'/player-stat')
                    if info is not None and players is not None:
                        games.append(normalize_kbl(info,players,[meta,im,pm]))
                except (ValueError,KeyError,TypeError) as e:
                    errors.append({'stage':'kbl_box','reason':str(e)})
        else: errors.append({'stage':'kbl_schedule','reason':meta.get('error',meta.get('status'))})
    for gid,s in sorted(candidates.items(),key=lambda item:item[1]['start']):
        data,meta=intake.get('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event='+gid)
        if data is None:
            errors.append({'stage':'nba_box','game_id':gid,'reason':meta.get('error',meta.get('status'))}); continue
        try:
            game=normalize_espn(data,meta)
            game['source_meta'].append(s['source_meta'])
            if set(game['teams'])!=set(s['teams']) or dt(game['start'])!=dt(s['start']): raise ValueError('schedule identity mismatch')
            games.append(game)
        except (ValueError,KeyError,TypeError) as e:
            errors.append({'stage':'nba_box','game_id':gid,'reason':str(e)})
    return games,schedule,identities,errors,coverage

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget',type=int,default=220)
    parser.add_argument('--capture-url',action='append',default=[],help='one-shot future intake; max ten public URLs; never backdate')
    args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    protocol=OUT/'frozen-protocol.json'
    if protocol.exists() and json.loads(protocol.read_text(encoding='utf8'))!=PROTOCOL:
        raise ValueError('frozen protocol changed')
    write_json(protocol,PROTOCOL)
    intake=Intake(OUT,max(0,min(args.budget,300)))
    if args.capture_url:
        if len(args.capture_url)>10: raise ValueError('capture at most ten URLs')
        captures=[]
        for url in args.capture_url:
            _,meta=intake.get(url)
            captures.append(meta)
        write_json(OUT/'capture-manifest.json',captures)
        return
    inputs=[ROSTERS/'results/forecast-rosters.jsonl.gz',ROSTERS/'claimed-time-results/forecast-rosters.jsonl.gz']
    manifest=[]; rows=[]
    for path in inputs:
        manifest.append({'path':str(path),'sha256':sha(path.read_bytes())})
        selected=[frame(r) for r in read_rows(path) if r.get('league') in ('NBA','KBL')]
        rows.extend(selected)
    write_rows(OUT/'selected-frames.jsonl.gz',rows)
    games,schedule,identities,errors,coverage=collect(intake,rows)
    write_json(OUT/'schedule-coverage.json',{k:sorted(v) for k,v in coverage.items()})
    write_rows(OUT/'normalized-games.jsonl.gz',games)
    write_rows(OUT/'schedule.jsonl.gz',schedule)
    write_json(OUT/'team-identities.json',identities)
    actual,audit=extract(rows,games,identities,schedule,OUT/'features.jsonl.gz',coverage=coverage)
    manifest.append({'path':str(ARCHIVE),'sha256':sha(ARCHIVE.read_bytes())})
    broad=[frame(r) for r in read_rows(ARCHIVE) if r.get('league') in ('NBA','KBL')]
    archive,ba=extract(broad,games,identities,schedule,OUT/'archive-features.jsonl.gz',broad=True,coverage=coverage)
    # Save compact selected frames only, never the full archive or its outcome fields.
    used={(r['cohort'],r['event_id']) for r in archive}
    write_rows(OUT/'selected-archive-frames.jsonl.gz',[r for r in broad if (r['cohort'],r['event_id']) in used])
    write_rows(OUT/'audit.jsonl.gz',audit+ba)
    write_json(OUT/'fetch-errors.json',errors)
    def summary(rr):
        return {'rows':len(rr),'rows_with_features':sum(bool(r['features']) for r in rr),
                'both_sides':sum(len(r['features'])==8 for r in rr),
                'by_league':dict(Counter(r['league'] for r in rr if r['features'])),
                'tiers':dict(Counter(e['availability_tier'] for r in rr for e in r['feature_evidence'].values()))}
    write_json(OUT/'summary.json',{'actual_and_claimed':summary(actual),'archive_secondary':summary(archive),
        'normalized_games':dict(Counter(g['league'] for g in games)), 'requests_this_run':intake.count,
        'missing_reasons_actual':dict(Counter(a['reason'] for a in audit if a['reason'])),
        'missing_reasons_archive':dict(Counter(a['reason'] for a in ba if a['reason'])),
        'roi':'not evaluated here; parent matched evaluator only; no model improvement claim'})
    manifest.extend({'path':str(p.relative_to(OUT)),'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size}
                    for p in OUT.rglob('*') if p.is_file() and p.name!='hash-manifest.json')
    write_json(OUT/'hash-manifest.json',manifest)
    print((OUT/'summary.json').read_text(encoding='utf8'))

if __name__=='__main__':
    main()
