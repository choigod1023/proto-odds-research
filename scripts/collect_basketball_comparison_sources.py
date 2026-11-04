"""Bounded public basketball source capture; no historical receipt inference."""
import argparse
import datetime as dt
import gzip
import hashlib
import json
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
        meta = dict(label=label, url=url, final_url=final_url, status=status, received_at=received, body_sha256=digest, bytes=len(body), error=None)
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
        # Only emit records with explicit, unambiguous official identity and kickoff.
        try:
            game_id = str(row['gmkey'])
            kickoff = dt.datetime.strptime(str(row['gameDate']) + str(row['gameStart']).zfill(4), '%Y%m%d%H%M').replace(tzinfo=dt.timezone(dt.timedelta(hours=9))).isoformat()
            home, away = row['tnameH'], row['tnameA']
        except (KeyError, ValueError, TypeError):
            continue
        common = dict(sport='bk', league='KBL', kickoff=kickoff, home=home, away=away, source_url=meta['url'], body_sha256=meta['body_sha256'], received_at=meta['received_at'], timing_basis='actual_receipt')
        output.append(dict(record_id='kbl:fixture:'+game_id, kind='fixture', **common, payload={'source_fixture_id':game_id, 'home_team_id':row.get('tcodeH'), 'away_team_id':row.get('tcodeA')}))
        if row.get('isEnded') == 1 and row.get('scoreH') is not None and row.get('scoreA') is not None:
            output.append(dict(record_id='kbl:result:'+game_id, kind='result', **common, payload={'source_fixture_id':game_id, 'home_score':row['scoreH'], 'away_score':row['scoreA']}))
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
            record.update(record_id='kbl:player_history:'+game_id, kind='player_history', source_url=meta['url'], body_sha256=meta['body_sha256'], received_at=meta['received_at'], timing_basis='actual_receipt', payload={'source_fixture_id':game_id, 'availability':'postgame_only', 'players':players})
            records.append(record)
    (OUTPUT / 'requests.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (OUTPUT / 'records.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records), encoding='utf-8')
    print(json.dumps({'requests':len(manifest), 'records':len(records), 'statuses':{m['label']:m['status'] for m in manifest}}))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, default=DEFAULT_EVIDENCE)
    run(parser.parse_args().evidence)
