"""Reconcile saved 2025 official schedule POST bytes against domestic data, no network."""
import csv, gzip, json
from collections import Counter, defaultdict
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.research_kleague_dates import parse_raw

BASE=Path('experiments/verified-cohort-20261101/kleague1')
RAW=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/internal-game-factors/data/processed/games.csv.tmp')
NORM=Path('experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz')


def index_unique(rows, key):
    index=defaultdict(list)
    for row in rows: index[key(row)].append(row)
    return index


def compare_rows(official, domestic, raw):
    """Only unique same-day oriented-team links; date discrepancies cannot be inferred."""
    key=lambda r:(r['day'],r['home'],r['away'])
    ix=index_unique(official,key); dx=index_unique(domestic,key); rx=index_unique(raw,key)
    out=[]
    for d in domestic:
        k=key(d); a=ix[k]; n=dx[k]; b=rx[k]
        if len(a)!=1 or len(n)!=1 or len(b)!=1:
            out.append({'domestic_id':d['id'],'link':'unmatched' if not a else 'ambiguous_or_missing_raw','official_candidates':len(a),'domestic_candidates':len(n),'raw_candidates':len(b)})
            continue
        o=a[0]; r=b[0]; s=o['scores']
        outcome=None if s is None else (0 if s['home']>s['away'] else 1 if s['home']==s['away'] else 2)
        out.append({'domestic_id':d['id'],'fixture_id':o['fixture_id'],'link':'unique',
          'date_match':True,'home_away_match':True,
          'clock_raw_match':None if o['kickoff'] is None else r['kickoff']==o['kickoff'],
          'clock_normalized_match':None if o['kickoff'] is None or d.get('kickoff_precision')!='minute_timezone_uncertified' else d.get('kickoff')==o['kickoff'],
          'official_final': o.get('source_status') == 'FE' and o.get('source_end_yn') == 'Y',
          'raw_not_void': r['is_void'] == 'False',
          'score_raw_match':None if s is None else (r['home_score'],r['away_score'])==(s['home'],s['away']),
          'outcome_normalized_match':None if outcome is None else d['y']==outcome})
    return out


def main():
    mapping=json.loads((BASE/'team-map.json').read_text(encoding='utf-8-sig'))
    fixtures=[]; official=[]; months=[]; round_counts=Counter(); team_counts=Counter()
    for month in range(2,12):
        name=f'schedule-2025-{month:02d}.json';doc=json.loads((BASE/name).read_bytes());assert str(doc['resultCode'])=='200'
        clubs={c['teamId']:c['teamNameFull'] for c in doc['data']['clubList']}
        for id_,item in mapping.items():
            if clubs.get(id_) != item['official']: raise ValueError(f'official identity mismatch: {id_}')
        for r in doc['data']['scheduleList']:
            if (r['year'],r['leagueId'],r['meetSeq'])!=(2025,1,1) or not r['gameDate'].startswith(f'2025.{month:02d}.'): raise ValueError('unexpected season, league or month')
            day=r['gameDate'].replace('.','-');time=r.get('gameTime');kickoff=day+'T'+time+':00' if time else None
            scores={'home':r['homeGoal'],'away':r['awayGoal']} if r.get('gameStatus')=='FE' and isinstance(r.get('homeGoal'),int) and isinstance(r.get('awayGoal'),int) else None
            fixture={'fixture_id':f"kleague:2025:1:{r['gameId']}",'league':'KLEAGUE1','home':r['homeTeamName'],'away':r['awayTeamName'],'home_team_id':r['homeTeam'],'away_team_id':r['awayTeam'],'day':day,'kickoff':kickoff,'kickoff_timezone':'unverified','scores':scores,'source_status':r.get('gameStatus'),'source_end_yn':r.get('endYn'),'source_url':'https://www.kleague.com/getScheduleList.do','source_evidence_id':name,'observation_method':'direct_read_only_post_response_bytes'}
            fixtures.append(fixture)
            round_counts[str(r['roundId'])] += 1
            team_counts.update((r['homeTeam'], r['awayTeam']))
            official.append({**fixture,'home':mapping[r['homeTeam']]['domestic'],'away':mapping[r['awayTeam']]['domestic']})
        months.append({'month':month,'rows':len(doc['data']['scheduleList'])})
    if len({r['fixture_id'] for r in fixtures}) != len(fixtures): raise ValueError('duplicate official fixture ID')
    with RAW.open(encoding='utf-8-sig',newline='') as f:
        candidates=[r for r in csv.DictReader(f) if r['year']=='2025' and r['sport']=='sc' and r['league']=='K리그1' and r['market_family']=='승무패' and not r['market_label'] and r['n_way']=='3']
        raw=[parse_raw(r) for r in candidates]
        if any(r is None for r in raw): raise ValueError('unparseable in-scope full-game 3-way row')
    with gzip.open(NORM,'rt',encoding='utf8') as f:
        domestic=[json.loads(l) for l in f];domestic=[r for r in domestic if r.get('sport')=='sc' and r.get('league')=='K리그1' and r.get('day','').startswith('2025-')]
    details=compare_rows(official,domestic,raw)
    unique=[r for r in details if r['link']=='unique'];matched={r['fixture_id'] for r in unique}
    def count(k):
        vals=[r.get(k) for r in details];return {'match':vals.count(True),'mismatch':vals.count(False),'unknown':len(domestic)-vals.count(True)-vals.count(False)}
    result={'official_rows':len(fixtures),'official_unique_ids':len({r['fixture_id'] for r in fixtures}),
      'official_final_scores':sum(r['scores'] is not None for r in fixtures),'official_status':dict(Counter(str(r['source_status']) for r in fixtures)),
      'official_by_month':months,'round_counts':dict(round_counts),'team_game_counts':dict(team_counts),'domestic_raw_full3':len(raw),'domestic_normalized':len(domestic),
      'link_counts':dict(Counter(r['link'] for r in details)),'official_unlinked':len(fixtures)-len(matched),
      'official_unlinked_ids':[r['fixture_id'] for r in fixtures if r['fixture_id'] not in matched],
      'fields':{k:count(k) for k in ('date_match','home_away_match','clock_raw_match','clock_normalized_match','score_raw_match','outcome_normalized_match','official_final','raw_not_void')},
      'utc_instant':{'match':0,'mismatch':0,'unknown':len(domestic)},'same_market_price_receipt':{'match':0,'mismatch':0,'unknown':len(domestic)},
      'limitations':['matching requires the same date; date discrepancies cannot be counted as zero across unlinked cases','2025 season-specific manual team ID mapping, not a general alias resolver','UTC and pregame receipts are not certified'], 'details':details}
    (BASE/'fixtures.jsonl').write_bytes((''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in fixtures)).encode('utf8'))
    (BASE/'comparison.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    print(json.dumps({k:v for k,v in result.items() if k!='details'},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
