"""Build compact local facts for a retrospective K-league experiment, not runtime."""
from collections import Counter, defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

ALIASES = dict(zip(
    'FC서울 FC안양 강원FC 경남FC 광주FC 김천상무 김포FC 대구FC 대전하나 부산아이 부천FC 서울이랜 성남FC 수원삼성 안산그리 울산HDFC 인천유나 전남드래 전북현대 제주SKFC 천안시티 충북청주 포항스틸'.split(),
    '서울 안양 강원 경남 광주 김천 김포 대구 대전 부산 부천 서울E 성남 수원 안산 울산 인천 전남 전북 제주 천안 충북청주 포항'.split()))


def team(value):
    return ALIASES.get(value, value)


def build(csv, detail, official, cohort):
    grouped = defaultdict(list)
    frame = pd.read_csv(csv)
    for r in frame.to_dict('records'):
        if r['league'] not in {'K리그1', 'K리그2'} or r['market_family'] != '승무패':
            continue
        if r['is_void'] or r['result'] not in {'홈승', '홈패', '무승부'}:
            continue
        h = re.fullmatch(r'(.+?)\s+(\d+)', str(r['home']).strip())
        a = re.fullmatch(r'(\d+)\s+(.+)', str(r['away']).strip())
        dt = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', r['date_text'])
        if not h or not a or not dt:
            continue
        year = int(r['year']) - int(int(r['round']) == 1 and dt[1] == '12')
        day = f'{year}-{dt[1]}-{dt[2]}'
        record = dict(league=r['league'], date=day, home=team(h[1]), away=team(a[2]),
                      hg=int(h[2]), ag=int(a[1]), origin='archived_proto_result')
        grouped[(record['league'], day, record['home'], record['away'])].append(record)
    official_rows = json.loads(official.read_text(encoding='utf8'))
    for r in official_rows:
        if r['league'] not in {'K리그1', 'K리그2'}:
            continue
        record = dict(league=r['league'], date=r['match_date'], home=team(r['home']),
            away=team(r['away']), hg=r['home_score'], ag=r['away_score'],
            origin='official_kleague_schedule', official_url=r['source_url'])
        grouped[(record['league'], record['date'], record['home'], record['away'])].append(record)
    members = defaultdict(set)
    for (league, day, home, away) in grouped:
        members[(league, day[:4])].update([home, away])
    details = json.loads(detail.read_text(encoding='utf8'))
    skipped = Counter()
    for r in details.values():
        home, away, day = team(r['home']), team(r['away']), r['date']
        # Known league membership excludes the promotion play-offs in this archive.
        if not {home, away} <= members[('K리그1', day[:4])]:
            skipped['detail_outside_k1_members'] += 1
            continue
        sides = {}
        for side in ['home', 'away']:
            ps = (r.get('data', {}).get(side) or {}).get('players') or []
            sides[side] = [dict(pid=str(p['playerId']), ga=float(p.get('goal') or 0)+float(p.get('assists') or 0))
                           for p in ps if p.get('playerId')]
            if len(sides[side]) != 11 or len({p['pid'] for p in sides[side]}) != 11:
                sides[side] = []
        grouped[('K리그1', day, home, away)].append(dict(league='K리그1', date=day,
            home=home, away=away, hg=r['home_score'], ag=r['away_score'],
            home_players=sides['home'], away_players=sides['away'], origin='archived_kleague_detail'))
    history = []
    for key, batch in sorted(grouped.items()):
        if len({(r['hg'], r['ag']) for r in batch}) != 1:
            skipped['score_conflict'] += 1
            continue
        row = dict(batch[0])
        for r in batch:
            if r.get('home_players') and r.get('away_players'):
                row.update(home_players=r['home_players'], away_players=r['away_players'])
            if r.get('official_url'):
                row['official_url'] = r['official_url']
        row['origins'] = sorted({r['origin'] for r in batch})
        row.pop('origin', None)
        history.append(row)
    lookup = {(r['league'],r['date'],r['home'],r['away']):r for r in history}
    targets = []
    for e in json.loads(cohort.read_text(encoding='utf8'))['event_ledger']:
        if e['league'] not in {'K리그1','K리그2'} or any(p is None for p in e['picks'].values()):
            continue
        key = e['league'],e['match_date'],team(e['home']),team(e['away'])
        r = lookup.get(key)
        if not r:
            skipped['target_missing_result'] += 1
            continue
        winner = 'home' if r['hg'] > r['ag'] else 'away' if r['hg'] < r['ag'] else 'draw'
        if winner != e['winner']:
            raise ValueError('cohort and official results disagree')
        targets.append(dict(e, home=key[2], away=key[3]))
    return history, targets, dict(skipped)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['csv','detail','official','cohort','output']:
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    history, targets, excluded = build(args.csv,args.detail,args.official,args.cohort)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in [('history.jsonl',history),('targets.jsonl',targets)]:
        (args.output/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf8',newline='\n')
    manifest = dict(sources=[dict(name=name, sha256_lf=hashlib.sha256(getattr(args,name).read_bytes().replace(b'\r\n',b'\n')).hexdigest())
        for name in ['csv','detail','official','cohort']], history=len(history),targets=len(targets),exclusions=excluded,
        by_league_year=dict(Counter(r['league']+':'+r['date'][:4] for r in history)),
        live_pregame_lineup_versions_proven=False)
    (args.output/'inputs.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(manifest,ensure_ascii=False))
