"""One bounded public official-result capture; no production access or model scores.

Adapted from the 2023/24 training/fetch.py in the prior K League diagnostic.
Only 2026 Jan-Nov, 11 sequential requests, no retries or existing-file replacement.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / 'official-2026'
URL = 'https://www.kleague.com/getScheduleList.do'
LIMIT = 3_000_000


def save(path, value):
    with path.open('x', encoding='utf8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def capture():
    OUT.mkdir(exist_ok=False)
    entries = []
    failures = 0
    for month in range(1, 12):
        payload = dict(leagueId=1, teamId='', year='2026', month=f'{month:02d}', ticketYn='')
        e = dict(url=URL, method='POST', payload=payload,
                 requested_at=datetime.now(timezone.utc).isoformat())
        try:
            with requests.post(URL, json=payload, timeout=15, stream=True,
                               allow_redirects=False) as response:
                e['http_status'] = response.status_code
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > LIMIT:
                        raise ValueError('response size limit')
                    chunks.append(chunk)
                raw = b''.join(chunks)
                e.update(received_at=datetime.now(timezone.utc).isoformat(),
                         sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                         raw_file=f'{month:02d}.json')
                (OUT/e['raw_file']).write_bytes(raw)
                doc = json.loads(raw)
                rows = doc.get('data', {}).get('scheduleList')
                e['status'] = ('ok' if response.status_code == 200 and
                               str(doc.get('resultCode')) == '200' and
                               isinstance(rows, list) else 'invalid_response')
                e['rows'] = len(rows) if isinstance(rows, list) else None
        except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
            e.update(status='failed', error_type=type(error).__name__)
        entries.append(e)
        save(OUT/f'{month:02d}.receipt.json', e)
        print(json.dumps({k:e.get(k) for k in ('payload','status','rows','http_status')}), flush=True)
        failures = failures + 1 if e['status'] != 'ok' else 0
        if e.get('http_status') in (401,403,429) or failures >= 2:
            break
        time.sleep(.25)
    save(OUT/'requests.json', dict(entries=entries, request_cap=11, max_in_flight=1,
         no_retry=True, raw_limit=LIMIT, scope='postgame result receipt, not historical pregame evidence'))


def parse_entries(entries, root=OUT):
    fixtures, rejected, clubs = [], [], {}
    for e in entries:
        if e['status'] != 'ok':
            continue
        raw = (root/e['raw_file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != e['sha256']:
            raise ValueError('raw byte mismatch')
        data = json.loads(raw)['data']
        for c in data.get('clubList', []):
            clubs.setdefault(c['teamId'], set()).add(c['teamNameFull'])
        for r in data['scheduleList']:
            if (r.get('year'),r.get('leagueId'),r.get('meetSeq')) != (2026,1,1):
                reason = 'wrong_competition_or_year'
            elif not str(r.get('gameDate','')).startswith('2026.'+e['payload']['month']):
                reason = 'wrong_month'
            elif r.get('gameStatus') != 'FE' or r.get('endYn') != 'Y':
                reason = 'not_final'
            elif any(type(r.get(k)) is not int or r[k] < 0 for k in ('homeGoal','awayGoal')):
                reason = 'invalid_final_score'
            elif not r.get('homeTeam') or not r.get('awayTeam') or r['homeTeam']==r['awayTeam']:
                reason = 'invalid_team_identity'
            else:
                reason = None
            if reason:
                rejected.append(dict(game_id=r.get('gameId'), month=e['payload']['month'], reason=reason))
                continue
            day=r['gameDate'].replace('.','-')
            fixtures.append(dict(id=f"kleague:2026:1:{r['gameId']}", day=day,
                kickoff=day+'T'+r['gameTime']+':00',home_id=r['homeTeam'],away_id=r['awayTeam'],
                home=r['homeTeamName'],away=r['awayTeamName'],hg=r['homeGoal'],ag=r['awayGoal'],
                source_status='FE',source_end_yn='Y',source_raw=e['raw_file'],
                source_sha256=e['sha256'],received_at=e['received_at']))
    if len({r['id'] for r in fixtures}) != len(fixtures):
        raise ValueError('duplicate official fixture IDs')
    return fixtures, rejected, clubs


def process():
    log = json.loads((OUT/'requests.json').read_text(encoding='utf8'))
    fixtures,rejected,clubs=parse_entries(log['entries'])
    with (OUT/'fixtures.jsonl').open('x',encoding='utf8',newline='\n') as f:
        for r in sorted(fixtures,key=lambda r:(r['day'],r['id'])):
            f.write(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n')
    save(OUT/'manifest.json',dict(fixtures=len(fixtures),counts_by_month=dict(Counter(r['day'][:7] for r in fixtures)),
         excluded=dict(Counter(r['reason'] for r in rejected)),rejected=rejected,
         clubs={k:sorted(v) for k,v in clubs.items()},actual_pregame_receipt=False))
    print(json.dumps(dict(fixtures=len(fixtures),excluded=dict(Counter(r['reason'] for r in rejected)))))


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--capture',action='store_true');a=p.parse_args()
    if a.capture:
        capture()
    process()
