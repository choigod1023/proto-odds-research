"""Collect official 2025 Korean results; no odds or production writes.

Output is a JSON array. Volleyball scores are SET wins, basketball scores points.
Raw HTTP bodies and request metadata are kept in an output-local cache. Existing
cache is replayed by default; --refresh makes new requests. KBL scope is senior
domestic regular season/playoffs/finals; other competitions are counted as excluded.
KOVO includes V-League and the domestic cup, excluding aggregate/all-star categories.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
KOVO = dict(zip(
    ['1001', '1002', '1004', '1005', '1006', '1008', '1009',
     '2001', '2002', '2003', '2004', '2005', '2006', '2007'],
    ['대한항공', '삼성화재', 'KB손보', '현대캐피', '한국전력', 'OK저축', '우리카드',
     '현대건설', '도로공사', '대전정관', '흥국생명', 'GS칼텍스', 'IBK기업', '페퍼저축']))
KBL = dict(zip(
    ['06', '10', '16', '35', '50', '55', '60', '64', '66', '70'],
    ['KT소닉붐', '울산모비', '원주DB', '서울삼성', '창원LG',
     '서울SK', '부산KCC', '한국가스', '고양소노', '안양정관']))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class Fetcher:
    def __init__(self, cache, refresh=False):
        self.cache, self.refresh = Path(cache), refresh
        self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, url, params, headers):
        url = requests.Request('GET', url, params=params).prepare().url
        key = hashlib.sha256(url.encode()).hexdigest()
        body = self.cache / (key + '.body.json')
        meta = self.cache / (key + '.meta.json')
        if self.refresh or not (body.exists() and meta.exists()):
            response = requests.get(url, headers=headers, timeout=40)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get('result', {}).get('status', 200) != 200:
                raise ValueError(f'Official API error: {url}: {data}')
            body.write_bytes(response.content)
            dump(meta, {'url': url, 'headers': headers, 'status': response.status_code,
                        'fetched_at': datetime.now(timezone.utc).isoformat(),
                        'sha256': hashlib.sha256(response.content).hexdigest()})
        raw = body.read_bytes()
        if hashlib.sha256(raw).hexdigest() != json.loads(meta.read_text(encoding='utf-8'))['sha256']:
            raise ValueError(f'Corrupt cache: {body}')
        return json.loads(raw), url


def make_row(sport, league, date, clock, home, away, hs, aws, ident, url, names):
    kickoff = datetime.strptime(date + ' ' + clock, '%Y-%m-%d %H:%M').replace(tzinfo=KST)
    if type(hs) is not int or type(aws) is not int or min(hs, aws) < 0 or hs == aws:
        raise ValueError(f'Invalid final score: {ident}: {hs}:{aws}')
    return dict(sport=sport, league=league, date=date, kickoff=kickoff.isoformat(),
                home=home, away=away, home_score=hs, away_score=aws,
                official_id=ident, source_url=url, raw_team_names=names)


def normalize_kovo(x, url, year, gcode):
    if not x['gdate'].startswith(f'{year}-'):
        return None
    hs, aws = x['hspoint'], x['aspoint']
    if max(hs or 0, aws or 0) != 3 or min(hs or 0, aws or 0) not in (0, 1, 2):
        raise ValueError(f'Non-final KOVO historical match: {x}')
    # Check final set wins against the individual set scores, not just the label.
    sets = [(x[f'hs{i}point'], x[f'as{i}point']) for i in range(1, 6)]
    if (sum(a > b for a, b in sets), sum(b > a for a, b in sets)) != (hs, aws):
        raise ValueError(f'Inconsistent KOVO set score: {x}')
    gender = str(x['gender'])
    if gender not in ('1', '2'):
        raise ValueError(f'Unknown gender: {gender}')
    league = ('KOVO' if gcode == '001' else 'KOVO컵') + ('남' if gender == '1' else '여')
    return make_row('vl', league, x['gdate'], x['gstime'],
                    KOVO[x['hcode']], KOVO[x['acode']], hs, aws,
                    f"KOVO:{gcode}:{x['seasonCode']}:{x['leagueCode']}:{gender}:{x['gnum']}",
                    url, {'home': x['hname'], 'away': x['aname']})


def normalize_kbl(x, url, year):
    if x['seasonGrade'] != 1 or x['seasonCategory'] not in ('R', 'PO', 'CP'):
        return None
    if not x['gameDate'].startswith(str(year)):
        raise ValueError('KBL returned a match outside the requested year')
    if x['isEnded'] != 1:
        raise ValueError(f'Non-final KBL historical match: {x["gmkey"]}')
    date = datetime.strptime(x['gameDate'], '%Y%m%d').date().isoformat()
    clock = x['gameStart']
    return make_row('bk', 'KBL', date, clock[:2] + ':' + clock[2:],
                    KBL[x['tcodeH']], KBL[x['tcodeA']], x['scoreH'], x['scoreA'],
                    'KBL:' + x['gmkey'], url,
                    {'home': x['tnameFH'], 'away': x['tnameFA']})


def collect(fetch, year):
    rows, excluded, rejected = [], Counter(), []
    headers = {'X-Service-Name': 'user', 'Accept-Language': 'ko'}
    base = 'https://user-api.kovo.co.kr/stat/'
    for gcode in ('001', '002'):
        seasons, _ = fetch.get(base + 'season-list', {'gcode': gcode}, headers)
        selected = [s for s in seasons['payload'] if str(year) in s['ryear'].split('-')]
        if not selected:
            raise ValueError(f'No seasons found for {year}/{gcode}')
        for season in selected:
            leagues = [x['leagueCode'] for x in season['leagues']
                       if x['leagueCode'] in ('201', '202', '203', '204', '212', '213', '214')]
            for gender in ('1', '2'):
                page, seen = 0, 0
                while True:
                    data, url = fetch.get(base + 'game-schedule', {
                        'gcode': gcode, 'seasonCode': season['seasonCode'],
                        'leagueCodes': leagues, 'gender': gender, 'size': 500, 'page': page}, headers)
                    payload = data['payload']
                    for x in payload['content']:
                        try:
                            row = normalize_kovo(x, url, year, gcode)
                        except (ValueError, KeyError) as exc:
                            # Historical regular-season rows can also lack a set score.
                            # Quarantine the row with raw evidence; never repair it by guessing.
                            excluded['KOVO invalid score/time or unmapped team'] += 1
                            rejected.append({'source_url': url, 'reason': str(exc), 'raw': x})
                            continue
                        if row:
                            rows.append(row)
                    seen += len(payload['content'])
                    page += 1
                    if page >= payload['page']['totalPages']:
                        if seen != payload['page']['totalElements']:
                            raise ValueError('Incomplete KOVO pagination')
                        break
    data, url = fetch.get('https://api.kbl.or.kr/match/list', {
        'fromDate': f'{year}0101', 'toDate': f'{year}1231', 'tcodeList': 'all'},
        {'Channel': 'WEB', 'TeamCode': 'XX', 'lang': 'ko'})
    if not isinstance(data, list) or not data:
        raise ValueError('Empty or invalid KBL response')
    for x in data:
        row = normalize_kbl(x, url, year)
        if row:
            rows.append(row)
        else:
            excluded[f"KBL grade={x['seasonGrade']} category={x['seasonCategory']}"] += 1
    ids, fixtures = set(), set()
    for row in rows:
        fixture = (row['league'], row['date'], row['home'], row['away'])
        if row['official_id'] in ids or fixture in fixtures:
            raise ValueError(f'Duplicate fixture: {row}')
        ids.add(row['official_id'])
        fixtures.add(fixture)
    return sorted(rows, key=lambda x: (x['kickoff'], x['league'], x['official_id'])), excluded, rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', type=int, default=2025)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows, excluded, rejected = collect(Fetcher(args.output.parent / 'official-korean-cache', args.refresh), args.year)
    dump(args.output, rows)
    dump(args.output.with_suffix('.excluded.json'), rejected)
    summary = {'year': args.year, 'rows': len(rows), 'by_league': dict(Counter(x['league'] for x in rows)),
               'excluded': dict(excluded), 'date_min': min(x['date'] for x in rows),
               'date_max': max(x['date'] for x in rows),
               'output_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}
    dump(args.output.with_suffix('.summary.json'), summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
