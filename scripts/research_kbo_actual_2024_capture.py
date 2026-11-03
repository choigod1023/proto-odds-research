"""Resumable, rate-limited collection of official 2024 KBO identity evidence."""
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone

import requests

ROOT = 'https://www.koreabaseball.com'
BASE = Path('C:/Users/user/Documents/Codex/kbodata')
ROWS = BASE / 'experiments/kbo-player-data-20261103/rows.jsonl'
STATE = Path('C:/Users/user/Documents/Codex/kbo-actual-2024-20261103')
EXPECTED = '66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c'


@contextmanager
def capture_lock():
    """Exclusive OS lock shared by collection and finalization processes."""
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'capture.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('capture_already_running') from exc
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError('capture_already_running') from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True), encoding='utf8')
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def progress(**kwargs):
    save(STATE / 'progress.json', {'updated_at': datetime.now(timezone.utc).isoformat(), **kwargs})


def fetch(endpoint, params, method='POST', as_json=True):
    url = ROOT + endpoint
    key = sha(json.dumps([url, params] if method == 'POST' else [method, url, params], sort_keys=True).encode())
    metapath = STATE / 'requests' / (key + '.json')
    if metapath.exists():
        meta = json.loads(metapath.read_text(encoding='utf8'))
        raw = gzip.decompress((STATE / 'raw' / (meta['sha256'] + '.gz')).read_bytes())
        if sha(raw) != meta['sha256']:
            raise RuntimeError('cache_digest_mismatch')
        return (json.loads(raw) if as_json else raw), meta
    log = STATE / 'attempts.jsonl'
    log.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        count = sum(1 for _ in log.open(encoding='utf8')) if log.exists() else 0
        # Reserve six attempts for earlier interrupted processes, which might
        # have been stopped after sending a request but before appending its log.
        if count >= 2394:
            raise RuntimeError('request_budget_exhausted')
        time.sleep(max(0, 1.5 - (time.monotonic() - fetch.last)))
        progress(stage='request', request_count=count + 1, endpoint=endpoint, game=params.get('gameId'), attempt=attempt + 1)
        start = datetime.now(timezone.utc).isoformat()
        record = {'url': url, 'method': method, 'params': params, 'started_at': start}
        try:
            # Do not reuse cookies or follow redirects.
            response = requests.request(method, url, data=params if method == 'POST' else None,
                                     params=params if method == 'GET' else None, timeout=(5, 15), allow_redirects=False,
                                     headers={'User-Agent': 'Mozilla/5.0', 'Referer': ROOT + '/Schedule/GameCenter/Main.aspx'})
            raw = response.content
            record.update(http_status=response.status_code, sha256=sha(raw), bytes=len(raw),
                          received_at=datetime.now(timezone.utc).isoformat())
            p = STATE / 'raw' / (record['sha256'] + '.gz')
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_bytes(gzip.compress(raw, mtime=0))
        except requests.RequestException as exc:
            record['error'] = type(exc).__name__
        finally:
            fetch.last = time.monotonic()
        with log.open('a', encoding='utf8') as out:
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
            out.flush()
        progress(stage='response', request_count=count + 1, endpoint=endpoint, game=params.get('gameId'), status=record.get('http_status'), error=record.get('error'))
        if record.get('http_status') == 200:
            payload = json.loads(raw) if as_json else raw
            save(metapath, record)
            return payload, record
        if record.get('http_status') not in (None, 429, 500, 502, 503, 504):
            raise RuntimeError('http_status:' + str(record['http_status']))
        if attempt < 2:
            delay = (3, 8)[attempt]
            if record.get('http_status') == 429:
                retry = response.headers.get('Retry-After', '')
                if retry.isdigit() and int(retry) > 25:
                    raise RuntimeError('retry_after_exceeds_safe_wait')
                delay = max(delay, int(retry) if retry.isdigit() else 0)
            progress(stage='backoff', seconds=delay, request_count=count + 1)
            time.sleep(delay)
    raise RuntimeError('retries_exhausted')


fetch.last = time.monotonic()


def rows():
    raw = ROWS.read_bytes()
    if sha(raw) != EXPECTED:
        raise RuntimeError('input_hash_mismatch')
    result = [json.loads(line) for line in raw.splitlines() if line.strip()]
    result = [r for r in result if r['season'] == 2024]
    if len(result) != 728 or len({r['fixture_id'] for r in result}) != 728:
        raise RuntimeError('input_population_mismatch')
    return result


def game_day(row):
    if row.get('kickoff_at'):
        return row['kickoff_at'][:10].replace('-', '')
    match = re.fullmatch(r'score:(\d{4})-(\d{2})-(\d{2}):([^:]+):([^:]+)', row['fixture_id'])
    if not match or (match.group(4), match.group(5)) != (row['home_id'], row['away_id']):
        raise ValueError('fixture_date_missing_or_mismatch')
    return ''.join(match.group(i) for i in (1, 2, 3))


def schedule(row, gid=None, team_codes=None, header=None):
    day = game_day(row)
    body, meta = fetch('/ws/Main.asmx/GetKboGameList', {'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':day})
    if str(body.get('code')) != '100':
        raise ValueError('invalid_schedule')
    if not gid and header is None:
        same_teams = [g for g in body['game'] if str(g.get('G_DT')) == day and {g.get('HOME_NM'), g.get('AWAY_NM')} == {row['home_id'], row['away_id']}]
        if len(same_teams) != 1:
            raise ValueError('fallback_game_ambiguous_doubleheader')
    games = [g for g in body['game'] if (not gid or g.get('G_ID') == gid) and
             str(g.get('G_DT')) == day and
             g.get('HOME_NM') == row['home_id'] and g.get('AWAY_NM') == row['away_id'] and
             (team_codes is None or (g.get('AWAY_ID'),g.get('HOME_ID')) == team_codes) and
             (header is None or str(g.get('HEADER_NO')) == str(header))]
    if len(games) != 1:
        raise ValueError('schedule_match_not_unique')
    g = games[0]
    if (str(g['G_DT']) != day or str(g['SEASON_ID']) != '2024' or str(g['LE_ID']) != '1' or
        not re.fullmatch(r'\d{8}' + re.escape(g['AWAY_ID'] + g['HOME_ID'] + str(g['HEADER_NO'])), g['G_ID'])):
        raise ValueError('schedule_identity_mismatch')
    return g, meta


def collect(row):
    fid = row['fixture_id']
    match = re.fullmatch(r'(\d{8})([A-Z]{2})([A-Z]{2})(\d)2024', fid)
    smeta = None
    if match and match.group(1)[:4] != '2024':
        # Synthetic postseason year; retain month/day and identify the official
        # schedule game by actual date, both teams and doubleheader number.
        if match.group(1)[4:] != game_day(row)[4:]:
            raise ValueError('synthetic_fixture_month_day_mismatch')
        game, smeta = schedule(row, team_codes=(match.group(2),match.group(3)), header=match.group(4))
        if (game['AWAY_ID'], game['HOME_ID']) != (match.group(2), match.group(3)):
            raise ValueError('synthetic_fixture_team_code_mismatch')
    elif match:
        day, away, home, header = match.groups()
        if game_day(row) != day:
            raise ValueError('fixture_date_mismatch')
        game, smeta = schedule(row, fid[:-4])
        if game['AWAY_ID'] != away or game['HOME_ID'] != home:
            raise ValueError('fixture_team_code_mismatch')
    else:
        game, smeta = schedule(row)
    def params():
        return {'leId':game['LE_ID'], 'srId':game['SR_ID'], 'seasonId':game['SEASON_ID'], 'gameId':game['G_ID']}
    lineup, lm = fetch('/ws/Schedule.asmx/GetLineUpAnalysis', params())
    if isinstance(lineup, list) and lineup and lineup[0] and lineup[0][0].get('LINEUP_CK') is False and smeta is None:
        game, smeta = schedule(row, game['G_ID'])
        lineup, lm = fetch('/ws/Schedule.asmx/GetLineUpAnalysis', params())
    if not isinstance(lineup, list) or len(lineup) != 5 or not lineup[0] or lineup[0][0].get('LINEUP_CK') is not True:
        return {'fixture_id':fid, 'official_game_id':game['G_ID'], 'exclusion_reasons':['official_lineup_unconfirmed'], 'schedule_evidence':smeta, 'lineup_evidence':lm}
    for side, idx in [('HOME', 1), ('AWAY', 2)]:
        m = lineup[idx][0]
        for key, val in [('G_ID',game['G_ID']), ('T_ID',game[side + '_ID']), ('LE_ID',game['LE_ID']), ('SR_ID',game['SR_ID']), ('SEASON_ID',game['SEASON_ID'])]:
            if str(m.get(key)) != str(val):
                raise ValueError('lineup_identity_mismatch:' + side + ':' + key)
    box, bm = fetch('/ws/Schedule.asmx/GetBoxScoreScroll', params())
    if str(box.get('code')) != '100':
        raise ValueError('boxscore_response_invalid')
    return {'fixture_id':fid, 'official_game_id':game['G_ID'], 'official_game':game,
            'schedule_evidence':smeta, 'lineup_evidence':lm, 'boxscore_evidence':bm}


def _main():
    p = argparse.ArgumentParser()
    p.add_argument('--sample', type=int, default=0)
    p.add_argument('--fixture')
    p.add_argument('--retry-excluded', action='store_true')
    args = p.parse_args()
    population = rows()
    if args.sample:
        population = [population[i] for i in [0, len(population)//2, len(population)-1][:args.sample]]
    for i, row in enumerate(population):
        if args.fixture and row['fixture_id'] != args.fixture:
            continue
        target = STATE / 'games' / (sha(row['fixture_id'].encode()) + '.json')
        if target.exists():
            old = json.loads(target.read_text(encoding='utf8'))
            if old.get('capture_version') == 2 and not (args.retry_excluded and old.get('exclusion_reasons')):
                continue
        try:
            result = collect(row)
        except ValueError as exc:
            result = {'fixture_id':row['fixture_id'], 'exclusion_reasons':[str(exc)]}
        result['capture_version'] = 2
        save(target, result)
        progress(stage='capture', finished=i + 1, total=len(population), fixture_id=row['fixture_id'])
    progress(stage='capture_finished', total=len(population))


def main():
    with capture_lock():
        _main()


if __name__ == '__main__':
    main()
