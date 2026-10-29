"""Preserve independent sidecar sources; only official pre-decision lineups qualify."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/baseball-information-stepchange-20261029/mlb'


def stamp(value):
    if len(value) == 15 and value[8] == '_':
        return datetime.strptime(value, '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def read_rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf8') as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8', newline='\n')


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'wt', encoding='utf8', newline='\n') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def extract_snapshot(j, cutoff, scheduled, game_pk):
    if j.get('gamePk') != game_pk:
        raise ValueError('different_fixture_returned')
    observed = stamp(j.get('metaData', {}).get('timeStamp', ''))
    if not observed <= cutoff < scheduled or observed >= scheduled:
        raise ValueError('snapshot_timestamp_after_cutoff_or_cutoff_after_start')
    gd = j['gameData']
    # Also compare against the start stored in the historical feed, not just the quote source.
    feed_start = stamp(gd['datetime']['dateTime'])
    if cutoff >= feed_start:
        raise ValueError('cutoff_after_feed_start')
    if gd['status'].get('codedGameState') not in {'S', 'P'}:
        raise ValueError('not_scheduled_or_warmup')
    plays = j.get('liveData', {}).get('plays', {})
    all_plays = plays.get('allPlays', []) + ([plays['currentPlay']] if plays.get('currentPlay') else [])
    if any(e.get('isPitch') for p in all_plays for e in p.get('playEvents', [])):
        raise ValueError('pitch_already_thrown')
    sides = {}
    for side in ['home', 'away']:
        starter = gd.get('probablePitchers', {}).get(side, {}).get('id')
        box = j['liveData']['boxscore']['teams'][side]
        order = box.get('battingOrder', [])
        if not starter or len(order) != 9 or len(set(order)) != 9:
            raise ValueError('starter_or_complete_lineup_missing')
        def player(pid):
            p = box.get('players', {}).get('ID' + str(pid), {})
            return dict(id=int(pid), name=p.get('person', {}).get('fullName', ''),
                        batting=p.get('seasonStats', {}).get('batting', {}),
                        pitching=p.get('seasonStats', {}).get('pitching', {}))
        sides[side] = dict(starter=player(starter), lineup=[player(pid) for pid in order])
    return dict(snapshot_at=observed.isoformat(), cutoff_at=cutoff.isoformat(),
                start_at=scheduled.isoformat(), feed_start_at=feed_start.isoformat(),
                source_kind='official_historical_timecode_replay', status=gd['status']['detailedState'], sides=sides)


def fetch_cached(url, path):
    if path.exists():
        with gzip.open(path, 'rb') as f:
            data = f.read()
        return data, [dict(cache=True, url=url, bytes=len(data))]
    logs = []
    for attempt in range(2):
        now = datetime.now(timezone.utc).isoformat()
        start = time.monotonic()
        try:
            response = requests.get(url, timeout=25)
            log = dict(url=url, attempt=attempt + 1, at=now, status=response.status_code,
                       seconds=round(time.monotonic() - start, 3), bytes=len(response.content))
            logs.append(log)
            response.raise_for_status()
            response.json()  # Do not cache invalid/non-JSON successful responses.
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, 'wb') as f:
                f.write(response.content)
            return response.content, logs
        except Exception as e:
            if not logs or logs[-1]['at'] != now:
                logs.append(dict(url=url, attempt=attempt + 1, at=now,
                                 seconds=round(time.monotonic() - start, 3)))
            logs[-1]['error'] = type(e).__name__ + ': ' + str(e)[:180]
    return None, logs


def prepare(prior_root, external_root):
    sources = OUT / 'sources'
    sources.mkdir(parents=True, exist_ok=True)
    old = prior_root / 'experiments/baseball-pregame-evidence-20261029'
    ext = external_root / 'experiments/baseball-bullpen-external-periods-20261029'
    names = ['mlb-snapshots.jsonl.gz', 'mlb-evaluation.jsonl'] + [
        f'savant-{y}-{kind}.json' for y in [2023, 2024, 2025] for kind in ['batter', 'pitcher']]
    origins = []
    for source in [old / n for n in names] + [ext / 'mlb-external-features.jsonl',
                    prior_root / 'scripts/collect_baseball_pregame_evidence.py',
                    prior_root / 'docs/research/2026-10-29-announced-baseball-comparison.md',
                    old / 'PROTOCOL.md']:
        dest = sources / ('prior-PROTOCOL.md' if source.name == 'PROTOCOL.md' else source.name)
        if not dest.exists():
            shutil.copyfile(source, dest)
        assert dest.read_bytes() == source.read_bytes(), 'source changed: ' + str(source)
        origins.append(dict(path=dest.relative_to(OUT).as_posix(), original_path=str(source.resolve()),
                            sha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
    write_json(sources / 'copied-origins.json', origins)


def collect():
    sources = OUT / 'sources'
    stats_audit = []
    for year in [2023, 2024, 2025]:
        for group in ['hitting', 'pitching']:
            url = (f'https://statsapi.mlb.com/api/v1/stats?stats=season&group={group}&season={year}'
                   '&sportIds=1&gameType=R&playerPool=ALL&limit=10000')
            data, logs = fetch_cached(url, sources / f'mlb-stats-{year}-{group}.json.gz')
            stats_audit.append(dict(year=year, group=group, url=url, attempts=logs,
                                    success=data is not None, response_sha256=hashlib.sha256(data).hexdigest() if data else None))
    write_json(OUT / 'stats-collection-audit.json', stats_audit)
    targets = read_rows(sources / 'mlb-external-features.jsonl')
    done_file = OUT / 'external-snapshots.jsonl.gz'
    done = {r['gamePk']: r for r in read_rows(done_file)} if done_file.exists() else {}
    def one(row):
        pk = row['gamePk']
        cutoff = stamp(row['decision_at']).replace(microsecond=0)
        code = cutoff.strftime('%Y%m%d_%H%M%S')
        url = f'https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live?timecode={code}'
        cache = sources / 'feed' / f'{pk}-{code}.json.gz'
        data, logs = fetch_cached(url, cache)
        result = dict(gamePk=pk, role='external', decision_at=row['decision_at'], url=url,
                      attempts=logs, raw_path=cache.relative_to(OUT).as_posix(), accepted=False,
                      collected_at=datetime.now(timezone.utc).isoformat())
        if data:
            result['response_sha256'] = hashlib.sha256(data).hexdigest()
            try:
                result.update(extract_snapshot(json.loads(data), cutoff, stamp(row['scheduled_start']), pk))
                result['accepted'] = True
            except Exception as e:
                result.update(reason=str(e)[:180], error_type=type(e).__name__)
        else:
            result.update(reason='http_failed', error_type='RequestFailure')
        return result
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(one, r) for r in targets if r['gamePk'] not in done]
        for i, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            done[result['gamePk']] = result
            if i % 20 == 0:
                write_rows(done_file, sorted(done.values(), key=lambda r: r['gamePk']))
                print('external snapshots', len(done), '/', len(targets), 'accepted', sum(r['accepted'] for r in done.values()), flush=True)
    write_rows(done_file, sorted(done.values(), key=lambda r: r['gamePk']))
    audit = dict(requested_games=len(targets), accepted=sum(r['accepted'] for r in done.values()),
                 actual_http_requests=sum(not a.get('cache', False) for r in done.values() for a in r['attempts']),
                 stats_http_requests=sum(not a.get('cache', False) for r in stats_audit for a in r['attempts']),
                 request_failures=sum('error' in a for r in done.values() for a in r['attempts']),
                 rejection_reasons={s: sum(r.get('reason') == s for r in done.values())
                                    for s in sorted({r['reason'] for r in done.values() if not r['accepted']})})
    write_json(OUT / 'collection-audit.json', audit)
    print(json.dumps(audit), flush=True)
    manifest = []
    for file in sorted(sources.rglob('*')):
        if file.is_file() and file.name != 'manifest.json':
            # Normalize text for git CRLF; compressed sources retain original bytes.
            data = file.read_bytes()
            if file.suffix != '.gz':
                data = data.replace(b'\r\n', b'\n')
            manifest.append(dict(path=file.relative_to(OUT).as_posix(), bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
    write_json(sources / 'manifest.json', manifest)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--prior-root', type=Path)
    p.add_argument('--external-root', type=Path)
    args = p.parse_args()
    if args.prepare:
        prepare(args.prior_root, args.external_root)
    collect()
