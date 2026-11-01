"""Bounded audit of explicitly witnessed fixtures; does not certify prices or timezone."""
import csv
import gzip
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

LEAGUE = 'K리그1'

def parse_raw(row):
    if not (row['year'] == '2025' and row['sport'] == 'sc' and row['league'] == LEAGUE
            and row['market_family'] == '승무패' and not row['market_label'] and row['n_way'] == '3'):
        return None
    home = re.fullmatch(r'(.+?)\s+(\d+)', row['home'])
    away = re.fullmatch(r'(\d+)\s+(.+)', row['away'])
    date = re.fullmatch(r'(\d{2})\.(\d{2})\([^)]*\)\s+(\d{2}):(\d{2})', row['date_text'])
    if not home or not away or not date:
        return None
    mm, dd, hh, minute = date.groups()
    dt = datetime(2025, int(mm), int(dd), int(hh), int(minute))
    return {'round': row['round'], 'game_no': row['game_no'], 'home': home[1], 'away': away[2],
            'home_score': int(home[2]), 'away_score': int(away[1]), 'day': dt.date().isoformat(),
            'kickoff': dt.isoformat(), 'is_void': row['is_void'], 'odds': row['odds']}

def compare(fixture, raws, normalized):
    link = fixture['domestic_link']
    raw = [r for r in raws if r['round'] == link['round'] and r['game_no'] == link['game_no']]
    norm = [r for r in normalized if r['id'] == link['normalized_id']]
    if len(raw) != 1 or len(norm) != 1:
        return {'fixture_id': fixture['fixture_id'], 'raw_matches': len(raw), 'normalized_matches': len(norm), 'status': 'ambiguous_or_missing'}
    a, b = raw[0], norm[0]
    if (a['home'], a['away'], b['home'], b['away']) != (link['home'], link['away'], link['home'], link['away']):
        return {'fixture_id': fixture['fixture_id'], 'status': 'identity_conflict'}
    scores = fixture['scores']
    return {'fixture_id': fixture['fixture_id'], 'status': 'unique', 'raw_matches': 1, 'normalized_matches': 1,
            'date_raw': a['day'] == fixture['day'], 'date_normalized': b['day'] == fixture['day'],
            'clock_raw': a['kickoff'] == fixture['kickoff'], 'clock_normalized': b['kickoff'] == fixture['kickoff'],
            'utc_instant': None, 'home_away': True,
            'score_raw': None if scores is None else (a['home_score'], a['away_score']) == (scores['home'], scores['away']),
            'score_normalized': None, # normalized file has result category, not full score
            'outcome_normalized': None if scores is None else b['y'] == (0 if scores['home'] > scores['away'] else 1 if scores['home'] == scores['away'] else 2),
            'cancel_postpone': None, 'same_market_price': None}

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--raw',required=True);p.add_argument('--normalized',required=True);p.add_argument('--fixtures',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    with open(args.raw,encoding='utf-8-sig',newline='') as f:
        all_raw=list(csv.DictReader(f))
    scoped=[r for r in all_raw if r['year']=='2025' and r['league']==LEAGUE and r['sport']=='sc']
    market=[r for r in scoped if r['market_family']=='승무패' and not r['market_label'] and r['n_way']=='3']
    raws=[parse_raw(r) for r in market]
    if any(r is None for r in raws):
        raise ValueError('unparseable full-game 3-way row; do not silently exclude')
    with gzip.open(args.normalized,'rt',encoding='utf-8') as f:
        all_norm=[json.loads(line) for line in f]
    norm=[r for r in all_norm if r.get('sport')=='sc' and r.get('league')==LEAGUE and r.get('day','').startswith('2025-')]
    fixtures=[json.loads(line) for line in Path(args.fixtures).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    comparisons=[compare(x,raws,norm) for x in fixtures]
    def count(field):
        values=[x.get(field) for x in comparisons]
        return {'match':values.count(True),'mismatch':values.count(False),'unknown':len(norm)-values.count(True)-values.count(False)}
    output={'raw_league_rows':len(scoped),'raw_3way_rows':len(market),'normalized_league_rows':len(norm),
      'raw_duplicate_market_keys':sum(v-1 for v in Counter((x['round'],x['game_no']) for x in raws).values()),
      'normalized_duplicate_ids':sum(v-1 for v in Counter(x['id'] for x in norm).values()),
      'official_fixture_rows':len(fixtures),'official_score_rows':sum(x['scores'] is not None for x in fixtures),
      'unique_explicit_fixture_links':sum(x['status']=='unique' for x in comparisons),
      'fields':{k:count(k) for k in ('date_raw','date_normalized','clock_raw','clock_normalized','utc_instant','home_away','score_raw','score_normalized','outcome_normalized','cancel_postpone','same_market_price')},
      'comparisons':comparisons}
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':main()
