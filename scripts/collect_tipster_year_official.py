"""Read public K League final schedules; no inference/DB/production imports."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import requests


def collect(out):
    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / 'requests.json'
    ledger = json.loads(ledger_path.read_text(encoding='utf8')) if ledger_path.exists() else []
    rows_path = out / 'kleague-results.json'
    rows = json.loads(rows_path.read_text(encoding='utf8')) if rows_path.exists() else []
    seen = {r['official_id'] for r in rows}
    url = 'https://www.kleague.com/getScheduleList.do'
    session = requests.Session()
    session.headers.update({'User-Agent': 'sportstoto-research/1.0', 'Referer': 'https://www.kleague.com/'})
    for year, months in ((2025, range(10, 13)), (2026, range(1, 11))):
        for league_id in (1, 2):
            for month in months:
                body = dict(leagueId=str(league_id), teamId='', ticketStatus='', year=str(year), month=f'{month:02}', ticketYn='N')
                if any(x.get('body') == body and x.get('encoding') == 'json' and not x.get('error') for x in ledger):
                    continue
                record = dict(url=url, method='POST', body=body, encoding='json', observed_at=datetime.now(timezone.utc).isoformat())
                try:
                    response = session.post(url, json=body, timeout=(5, 15), allow_redirects=False)
                    record.update(status=response.status_code, sha256=hashlib.sha256(response.content).hexdigest())
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError('unexpected redirect')
                    payload = response.json()
                    if payload.get('resultCode') != '200':
                        raise ValueError('official resultCode not 200')
                    count = 0
                    for game in payload['data']['scheduleList']:
                        if game['year'] != year:
                            raise ValueError('wrong year')
                        if game['leagueId'] != league_id:
                            record['other_competition_rows'] = record.get('other_competition_rows', 0)+1
                            continue
                        if game['meetSeq'] != league_id or game['endYn'] != 'Y' or game['gameStatus'] != 'FE':
                            continue
                        kickoff = datetime.strptime(game['gameDate']+' '+game['gameTime'], '%Y.%m.%d %H:%M')
                        if kickoff.year != year or kickoff.month != month:
                            raise ValueError('wrong month')
                        day = kickoff.date().isoformat()
                        if not '2025-10-28' <= day <= '2026-10-27':
                            continue
                        ident = f"kleague:{year}:{game['meetSeq']}:{game['gameId']}"
                        if ident in seen:
                            continue
                        h, a = int(game['homeGoal']), int(game['awayGoal'])
                        if min(h, a) < 0:
                            raise ValueError('negative score')
                        rows.append(dict(league=f'K리그{league_id}', match_date=day, home=game['homeTeamName'],
                            away=game['awayTeamName'], home_score=h, away_score=a, official_id=ident,
                            kickoff=kickoff.isoformat()+'+09:00', source_url=url, source_params=body,
                            observed_at=record['observed_at']))
                        seen.add(ident)
                        count += 1
                    record['accepted_finals'] = count
                except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                    record['error'] = f'{type(exc).__name__}: {exc}'[:300]
                ledger.append(record)
                ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
                rows_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
                if record.get('error'):
                    print(json.dumps(record, ensure_ascii=False), flush=True)
                    return rows
                print(f'{year}-{month:02} K{league_id} total={len(rows)}', flush=True)
                time.sleep(1)
    return rows


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    collect(a.out)
