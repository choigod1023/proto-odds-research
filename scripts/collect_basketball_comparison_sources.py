"""Bounded public basketball source capture; no historical receipt inference."""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
import urllib.request
import urllib.error
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = Path('C:/Users/user/Documents/Codex/pregame-basketball-evidence')
OUTPUT = ROOT / 'experiments/pregame-basketball-20261104'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; research contact via repository)', 'Channel': 'WEB', 'TeamCode': 'XX', 'lang': 'ko', 'X-Requested-With': 'XMLHttpRequest'}
SOURCES = {
    'kbl-2025-jan': 'https://api.kbl.or.kr/match/list?fromDate=20250101&toDate=20250107&tcodeList=all',
    'kbl-2026-nov': 'https://api.kbl.or.kr/match/list?fromDate=20261101&toDate=20261104&tcodeList=all',
    'nba-2025-injury-channel': 'https://official.nba.com/nba-injury-report-2024-25-season/',
    'kbl-2025-box': 'https://api.kbl.or.kr/match/S45G01N116/player-stat',
    'kbl-2026-box': 'https://api.kbl.or.kr/match/S49G01N52/player-stat',
    'nba-2026-injury-pdf': 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-04_12_15AM.pdf',
    'nba-2026-injury-channel': 'https://official.nba.com/nba-injury-report-2026-27-season/',
}

def sha(body):
    return hashlib.sha256(body).hexdigest()

class LimitedRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 2
    max_repeats = 1


def capture(label, url, evidence, opener=None):
    if opener is None:
        opener = urllib.request.build_opener(LimitedRedirect()).open
    evidence.mkdir(parents=True, exist_ok=True)
    meta_path = evidence / (label + '.json')
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        if meta['url'] != url:
            raise ValueError('cache URL mismatch')
        if meta.get('body_sha256'):
            body = gzip.decompress((evidence / (label + '.body.gz')).read_bytes())
            if sha(body) != meta['body_sha256']:
                raise ValueError('cache hash mismatch')
            return meta, body
        return meta, b''
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        try:
            response = opener(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read(8_000_001)
            if len(body) > 8_000_000:
                raise ValueError('response exceeds limit')
            status = response.status
            final_url = response.geturl()
        received = dt.datetime.now(dt.timezone.utc).isoformat()
        digest = sha(body)
        (evidence / (label + '.body.gz')).write_bytes(gzip.compress(body, mtime=0))
        meta = dict(label=label, url=url, final_url=final_url, status=status, received_at=received, body_sha256=digest, bytes=len(body), error=None, timestamp_basis='http_response_body_completed')
    except (OSError, ValueError) as exc:
        body = b''
        meta = dict(label=label, url=url, status=None, received_at=dt.datetime.now(dt.timezone.utc).isoformat(), body_sha256=None, bytes=0, error=str(exc))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return meta, body

def kbl_records(meta, body):
    if meta['status'] != 200:
        return []
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise ValueError('unexpected KBL response')
    output = []
    for row in rows:
        # Exclude D league, all-star, international and exhibition competitions.
        if row.get('seasonCategoryName') not in ('정규시즌', '플레이오프', '챔피언결정전'):
            continue
        # Only emit records with explicit, unambiguous official identity and kickoff.
        try:
            game_id = str(row['gmkey'])
            kickoff = dt.datetime.strptime(str(row['gameDate']) + str(row['gameStart']).zfill(4), '%Y%m%d%H%M').replace(tzinfo=dt.timezone(dt.timedelta(hours=9))).isoformat()
            home, away = row['tnameH'], row['tnameA']
        except (KeyError, ValueError, TypeError):
            continue
        common = dict(sport='bk', league='KBL', kickoff=kickoff, home=home, away=away, source_url=meta['url'], body_sha256=meta['body_sha256'], received_at=meta['received_at'], timing_basis='actual_receipt', timestamp_basis=meta.get('timestamp_basis', 'unknown'))
        output.append(dict(record_id='kbl:fixture:'+game_id+':'+sha((meta['body_sha256']+meta['received_at']).encode())[:12], kind='fixture', **common, payload={'source_fixture_id':game_id, 'home_team_id':row.get('tcodeH'), 'away_team_id':row.get('tcodeA')}))
        if row.get('isEnded') == 1 and row.get('scoreH') is not None and row.get('scoreA') is not None:
            output.append(dict(record_id='kbl:result:'+game_id+':'+sha((meta['body_sha256']+meta['received_at']).encode())[:12], kind='result', **common, payload={'source_fixture_id':game_id, 'home_score':row['scoreH'], 'away_score':row['scoreA']}))
    return output

@contextmanager
def singleflight(evidence):
    """One process owns the complete fixed request plan; lock file is retained."""
    evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / '.capture.lock').open('a+b') as lock:
        lock.seek(0)
        if lock.read(1) == b'':
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if __import__('os').name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            lock.seek(0)
            if __import__('os').name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

def run(evidence):
    with singleflight(evidence):
        return run_locked(evidence)

def run_locked(evidence):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest, records, bodies = [], [], {}
    for label, url in SOURCES.items():
        meta, body = capture(label, url, evidence)
        manifest.append(meta)
        bodies[label] = body
        if label in ('kbl-2025-jan', 'kbl-2026-nov'):
            records.extend(kbl_records(meta, body))
    fixtures = {r['payload']['source_fixture_id']: r for r in records if r['kind'] == 'fixture'}
    for label, game_id in [('kbl-2025-box', 'S45G01N116'), ('kbl-2026-box', 'S49G01N52')]:
        meta = next(m for m in manifest if m['label'] == label)
        fixture = fixtures.get(game_id)
        if meta['status'] == 200 and fixture:
            players = json.loads(bodies[label])
            if not isinstance(players, list):
                raise ValueError('unexpected player response')
            record = {k: fixture[k] for k in ('sport','league','kickoff','home','away')}
            record.update(record_id='kbl:player_history:'+game_id, kind='player_history', source_url=meta['url'], body_sha256=meta['body_sha256'], received_at=meta['received_at'], timing_basis='actual_receipt', timestamp_basis=meta.get('timestamp_basis', 'unknown'), payload={'source_fixture_id':game_id, 'availability':'postgame_only', 'players':players})
            records.append(record)
    (OUTPUT / 'requests.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (OUTPUT / 'records.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records), encoding='utf-8')
    print(json.dumps({'requests':len(manifest), 'records':len(records), 'statuses':{m['label']:m['status'] for m in manifest}}))


# The date in NBA URLs is the league's Eastern calendar date; KBL dates are KST.
TEAM_NAMES = dict(ATL='Atlanta Hawks', BOS='Boston Celtics', BKN='Brooklyn Nets', CHA='Charlotte Hornets', CHI='Chicago Bulls', CLE='Cleveland Cavaliers', DAL='Dallas Mavericks', DEN='Denver Nuggets', DET='Detroit Pistons', GSW='Golden State Warriors', HOU='Houston Rockets', IND='Indiana Pacers', LAC='LA Clippers', LAL='Los Angeles Lakers', MEM='Memphis Grizzlies', MIA='Miami Heat', MIL='Milwaukee Bucks', MIN='Minnesota Timberwolves', NOP='New Orleans Pelicans', NYK='New York Knicks', OKC='Oklahoma City Thunder', ORL='Orlando Magic', PHI='Philadelphia 76ers', PHX='Phoenix Suns', POR='Portland Trail Blazers', SAC='Sacramento Kings', SAS='San Antonio Spurs', TOR='Toronto Raptors', UTA='Utah Jazz', WAS='Washington Wizards')


def nba_game_page(meta, body):
    if meta['status'] != 200:
        return []
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', body.decode('utf-8'))
    if not match:
        raise ValueError('NBA official page lacks game data')
    props = json.loads(match.group(1))['props']['pageProps']
    if props.get('selectedDate') != meta['url'][-10:]:
        raise ValueError('NBA selected date mismatch')
    feed = props.get('gameCardFeed')
    if not isinstance(feed, dict) or not isinstance(feed.get('modules'), list):
        raise ValueError('NBA official game feed unavailable; date is not complete')
    if feed.get('nextPageToken') is not None:
        raise ValueError('NBA game feed is paginated; date incomplete')
    rows = []
    for module in feed['modules']:
        for card in module.get('cards', []):
            g = card.get('cardData', {})
            if not all(k in g for k in ('gameId','homeTeam','awayTeam','gameTimeUtc','gameTimeEastern')):
                raise ValueError('NBA game card missing fixture fields')
            home, away = g['homeTeam'], g['awayTeam']
            h, a = home.get('teamTricode'), away.get('teamTricode')
            if h not in TEAM_NAMES or a not in TEAM_NAMES or h == a:
                raise ValueError('NBA game card has unresolved team')
            kickoff = dt.datetime.fromisoformat(g['gameTimeUtc'].replace('Z','+00:00')).isoformat()
            common = dict(sport='bk', league='NBA', kickoff=kickoff, home=TEAM_NAMES[h], away=TEAM_NAMES[a], source_url=meta['url'], body_sha256=meta['body_sha256'], received_at=meta['received_at'], timing_basis='actual_receipt', timestamp_basis=meta.get('timestamp_basis', 'unknown'))
            game_id = str(g['gameId'])
            payload = dict(source_fixture_id=game_id, home_team_id=home.get('teamId'), away_team_id=away.get('teamId'), home_tricode=h, away_tricode=a, game_time_eastern_claim=g['gameTimeEastern'], season=g.get('seasonYear'), season_type=g.get('seasonType'))
            rows.append(dict(record_id='nba:fixture:'+game_id+':'+sha((meta['body_sha256']+meta['received_at']).encode())[:12], kind='fixture', payload=payload, **common))
            if g.get('gameStatus') == 3 and home.get('score') is not None and away.get('score') is not None:
                rows.append(dict(record_id='nba:result:'+game_id+':'+sha((meta['body_sha256']+meta['received_at']).encode())[:12], kind='result', payload={'source_fixture_id':game_id,'home_score':home['score'],'away_score':away['score']}, **common))
    expected = props.get('allGamesInCurrentYear', {}).get(props['selectedDate'][:4], {}).get(props['selectedDate'])
    if isinstance(expected, int) and expected > 0 and not rows:
        raise ValueError('NBA calendar has games but game feed is empty')
    return rows


def injury_records(meta, text, fixtures):
    """Join only exact official tricode pair, Eastern date and 12-hour time, uniquely."""
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    date = clock = matchup = team = None
    groups = {}
    statuses = {'Out','Questionable','Doubtful','Probable','Available'}
    for i, line in enumerate(lines):
        if re.fullmatch(r'\d{2}/\d{2}/\d{4}', line):
            date = dt.datetime.strptime(line, '%m/%d/%Y').date().isoformat()
        elif re.fullmatch(r'\d{2}:\d{2} \(ET\)', line):
            clock = line[:5]
        elif re.fullmatch(r'[A-Z]{3}@[A-Z]{3}', line):
            matchup, team = line, None
        elif line in TEAM_NAMES.values():
            team = line
        elif line in statuses and i and ',' in lines[i-1] and date and clock and matchup and team:
            away, home = matchup.split('@')
            candidates = []
            for f in fixtures:
                p = f['payload']
                eastern = p.get('game_time_eastern_claim', '')
                if p.get('away_tricode') != away or p.get('home_tricode') != home or team not in (f['home'],f['away']):
                    continue
                if eastern[:10] != date or len(eastern) < 16:
                    continue
                hh, mm = map(int, eastern[11:16].split(':'))
                pdf_hh, pdf_mm = map(int, clock.split(':'))
                if hh % 12 == pdf_hh % 12 and mm == pdf_mm:
                    candidates.append(f)
            if len(candidates) != 1:
                continue
            f = candidates[0]
            key = f['payload']['source_fixture_id']
            groups.setdefault(key, (f, []))[1].append({'team':team,'player_name':lines[i-1], 'status':line, 'player_id':None})
    result = []
    for game_id, (f, players) in groups.items():
        record = {k:f[k] for k in ('sport','league','kickoff','home','away')}
        record.update(record_id='nba:injury:'+game_id+':'+sha((meta['body_sha256']+meta['received_at']).encode())[:12], kind='lineup', source_url=meta['url'], body_sha256=meta['body_sha256'], received_at=meta['received_at'], timing_basis='actual_receipt', timestamp_basis=meta.get('timestamp_basis', 'unknown'), payload={'source_fixture_id':game_id, 'fixture_record_id':f['record_id'], 'information_type':'injury_report', 'confirmed_lineup':False, 'player_ids_verified':False, 'report_header_claim':lines[0] if lines else None, 'players':players})
        result.append(record)
    return result


def annual_ranges(start, end):
    current = start
    while current <= end:
        last = min(end, dt.date(current.year,12,31))
        yield current, last
        current = last + dt.timedelta(days=1)


def season_run(args):
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    cursor = dt.date.fromisoformat(args.nba_cursor or args.start)
    if start > end or cursor < start or cursor > end or args.nba_max_days < 0 or args.nba_max_days > 16:
        raise ValueError('invalid date range or NBA cursor/day cap')
    if args.refresh and not args.capture_id:
        raise ValueError('--refresh requires a new --capture-id')
    if args.capture_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', args.capture_id):
        raise ValueError('invalid capture id')
    # The evidence directory has a cumulative conservative 60-HTTP reservation.
    if args.total_http_budget < 0:
        raise ValueError('negative total HTTP budget')
    if not 0 <= args.max_new_requests <= 12:
        raise ValueError('--max-new-requests must be between 0 and 12')
    today = dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=9))).date()
    version = args.capture_id or 'initial'
    requests, records = [], []
    missing, nba_completed = [], []
    new = 0
    def get(label, url, mutable=False):
        nonlocal new
        path = args.evidence / (label+'.json')
        if args.refresh and mutable and path.exists():
            raise ValueError('capture ID already exists; choose a fresh ID')
        if not path.exists():
            # Reserve three HTTP calls per URL for the request and at most two redirects.
            # Existing metadata counts too, so resume cannot evade the shared budget.
            existing = 0
            for candidate in args.evidence.glob('*.json'):
                try:
                    if 'url' in json.loads(candidate.read_text(encoding='utf-8')):
                        existing += 1
                except (ValueError, OSError):
                    raise ValueError('unreadable evidence metadata; cannot enforce budget')
            if new >= args.max_new_requests or (existing + 1) * 3 > args.total_http_budget:
                return None, b''
            new += 1
        m,b = capture(label,url,args.evidence)
        requests.append(m)
        return m,b
    with singleflight(args.evidence):
        if not args.nba_only:
            for a,b in annual_ranges(start,end):
                mutable = b >= today - dt.timedelta(days=1)
                label = 'kbl-'+a.strftime('%Y%m%d')+'-'+b.strftime('%Y%m%d') + (('-'+version) if mutable else '')
                if a == dt.date(2025,1,1) and b == dt.date(2025,12,31): label = 'kbl-2025-full'
                if a == dt.date(2026,1,1) and b == dt.date(2026,11,4) and version == 'initial' and mutable: label = 'kbl-2026-full'
                url = f'https://api.kbl.or.kr/match/list?fromDate={a:%Y%m%d}&toDate={b:%Y%m%d}&tcodeList=all'
                m,body = get(label,url,mutable=mutable)
                if m and m['status'] == 200:
                    records.extend(kbl_records(m,body))
                else:
                    missing.append({'source':'kbl','start':str(a),'end':str(b),'reason':'budget' if not m else 'HTTP_or_network_failure'})
        for offset in range(args.nba_max_days):
            date = cursor + dt.timedelta(days=offset)
            if date > end: break
            # Keep recent Eastern dates mutable through the next UTC day.
            mutable = date >= dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
            label = 'nba-games-'+date.strftime('%Y%m%d') + ('-'+version if mutable else '')
            if date == dt.date(2026,11,4) and version == 'initial' and mutable: label = 'nba-2026-games-page'
            url = 'https://www.nba.com/games?date='+str(date)
            m,body = get(label,url,mutable=mutable)
            if not m:
                missing.append({'source':'nba','date':str(date),'reason':'budget'})
                break
            if m['status'] != 200:
                missing.append({'source':'nba','date':str(date),'reason':'HTTP_or_network_failure'})
                break
            try:
                rows = nba_game_page(m,body)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                missing.append({'source':'nba','date':str(date),'reason':str(exc)})
                break
            records.extend(rows)
            nba_completed.append(str(date))
        if args.injury_pdf:
            # Explicit URL and capture version, not a fixed permanently reused current document.
            url = args.injury_pdf
            if not url.startswith('https://ak-static.cms.nba.com/referee/injury/') or not url.endswith('.pdf'):
                raise ValueError('NBA official injury PDF URL required')
            label = 'nba-injury-'+sha(url.encode())[:12]+'-'+version
            if url.endswith('Injury-Report_2026-11-04_12_15AM.pdf') and version == 'initial': label = 'nba-2026-injury-pdf'
            m,body = get(label,url,mutable=True)
            if m and m['status'] == 200:
                try:
                    import fitz
                    doc = fitz.open(stream=body,filetype='pdf')
                    text = '\n'.join(page.get_text() for page in doc)
                    records.extend(injury_records(m,text,[r for r in records if r['league']=='NBA' and r['kind']=='fixture']))
                except ImportError:
                    missing.append({'source':'nba_injury','reason':'PyMuPDF required for PDF text'})
            else:
                missing.append({'source':'nba_injury','reason':'budget' if not m else 'HTTP_or_network_failure'})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
    next_date = cursor + dt.timedelta(days=len(nba_completed))
    report = {'start':str(start),'end':str(end),'nba_cursor':str(cursor),'nba_next_cursor':str(next_date) if next_date <= end else None,'nba_dates_completed':nba_completed,'new_url_attempts':new,'max_new_url_attempts':args.max_new_requests,'total_http_budget':args.total_http_budget,'max_redirects_per_url':2,'capture_id':version,'requests':requests,'missing':missing,'kbl_pregame_lineup':'missing_unverified_source','nba_cdn':'not used in date-batch mode; official nba.com game feed used','output':str(args.output)}
    manifest_path = args.output.with_name(args.output.stem+'.manifest.json')
    manifest_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'records':len(records),'new_url_attempts':new,'nba_dates_completed':nba_completed,'nba_next_cursor':report['nba_next_cursor'],'missing':missing},ensure_ascii=False))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--start', default='2025-01-01')
    p.add_argument('--end', default='2026-11-04')
    p.add_argument('--evidence', type=Path, default=DEFAULT_EVIDENCE)
    p.add_argument('--output', type=Path, default=OUTPUT/'season-records.jsonl')
    p.add_argument('--nba-cursor', help='Eastern date to resume NBA daily pages')
    p.add_argument('--nba-max-days',type=int,default=1)
    p.add_argument('--max-new-requests',type=int,default=4,help='URL attempts; each permits up to two redirects')
    p.add_argument('--total-http-budget',type=int,default=60,help='conservative cumulative request budget for this evidence directory')
    p.add_argument('--capture-id',help='stable version for mutable schedule or injury snapshots')
    p.add_argument('--refresh',action='store_true',help='require new capture ID; refuse overwrite')
    p.add_argument('--nba-only',action='store_true')
    p.add_argument('--injury-pdf',help='explicit official NBA injury PDF URL')
    args=p.parse_args()
    season_run(args)

if __name__ == '__main__':
    main()
