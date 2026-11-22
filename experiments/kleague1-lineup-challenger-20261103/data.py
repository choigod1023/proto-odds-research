"""Integrity-checked K1 retrospective input; labels stripped for test predictions."""
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parent
INPUT=ROOT/'inputs'
REF=dt.date(2026,7,1)
CUTOFF=REF-dt.timedelta(days=7)
TRAIN_CUTOFF=dt.date(2026,1,1)-dt.timedelta(days=7)

def load_inputs(scoring=False):
    protocol=json.loads((ROOT/'protocol.json').read_bytes())
    for name,sha in protocol['inputs_sha256'].items():
        if hashlib.sha256((INPUT/name).read_bytes()).hexdigest()!=sha:raise ValueError('input hash mismatch '+name)
    manifest=json.loads((INPUT/'price-manifest.json').read_bytes())
    if manifest['status']!='ready_for_join' or manifest['joined_gzip_sha256']!=protocol['inputs_sha256']['prices.jsonl.gz']:raise ValueError('price manifest invalid')
    rows=[]
    for year in (2025,2026):
        records=[json.loads(x) for x in (INPUT/f'lineups{year}.jsonl').read_bytes().splitlines()]
        records=[x for x in records if x['fixture_id'].startswith(f'kleague:{year}:1:')]
        if len(records)!=(228 if year==2025 else 204):raise ValueError('incomplete official season')
        for r in records:
            key=r['fixture_id'];day=dt.date.fromisoformat(r['day']);sides=r['lineups']
            if r['league']!='KLEAGUE1' or r['match_status']!='FE' or len(sides)!=2 or [x['side'] for x in sides]!=['home','away']:raise ValueError('fixture scope invalid')
            teams=[s['team_id'] for s in sides]
            if any(not t for t in teams) or teams[0]==teams[1]:raise ValueError('invalid teams')
            xis=[]
            for side in sides:
                xi=[x['player_id'] for x in side['starters']]
                if len(xi)!=11 or len(set(xi))!=11 or any(not isinstance(x,str) or len(x)!=8 or not x.isdigit() for x in xi):raise ValueError('invalid starting XI')
                xis.append(xi)
            rec={'id':key,'date':day,'kickoff_local':r['day']+'T'+r['kickoff_local_display']+':00','teams':teams,'xi':xis}
            if scoring or day<REF:
                score=r['scores'];vals=[score['home'],score['away']]
                if any(type(x) is not int or x<0 for x in vals):raise ValueError('invalid score')
                rec['score']=vals
            rows.append(rec)
    if len(rows)!=432 or len({r['id'] for r in rows})!=432:raise ValueError('fixture duplicate')
    schedule=json.loads((INPUT/'schedule2026.json').read_bytes());official={r['fixture_id']:r for r in schedule['targets']}
    if len(official)!=228:raise ValueError('invalid schedule')
    for g in rows:
        if g['date'].year==2026:
            o=official.get(g['id'])
            if not o or (o['day'],o['home_team_id'],o['away_team_id'],o['game_status'],o['end_yn'])!=(g['date'].isoformat(),*g['teams'],'FE','Y'):raise ValueError('2026 schedule identity/status mismatch')
            if 'score' in g and g['score']!=[o['home_goal'],o['away_goal']]:raise ValueError('2026 schedule score mismatch')
    with gzip.open(INPUT/'prices.jsonl.gz','rt',encoding='utf8') as f:source=[json.loads(s) for s in f]
    index={}
    for r in source:
        if r.get('league_id')!=1 or r.get('season') not in (2025,2026):continue
        key=r['fixture_id']
        if key in index:raise ValueError('duplicate price fixture')
        index[key]=r
    products=[(r.get('gmId'),r.get('gmTs'),r.get('matchSeq')) for r in index.values()]
    if any(any(x is None for x in key) for key in products) or len(set(products))!=len(products):raise ValueError('duplicate or missing product identity')
    if sum(r['season']==2025 for r in index.values())!=219 or sum(r['season']==2026 for r in index.values())!=193:raise ValueError('unexpected K1 official price coverage')
    prices={}
    for g in rows:
        r=index.get(g['id'])
        q=eligible_price(g,r) if r else None
        if q is not None:prices[g['id']]=q
    return sorted(rows,key=lambda g:(g['date'],g['id'])),prices

def eligible_price(g,r):
    market=r.get('market_type') or {};odds=r.get('prices') or {}
    modern=(market.get('betId')=='1' and market.get('betTypId')=='1' and market.get('betNm')=='축구 승무패')
    legacy=(market.get('betId') is None and market.get('betTypId') is None)
    if (r.get('fixture_id')!=g['id'] or r.get('league_id')!=1 or r.get('season')!=g['date'].year or r.get('gmId')!='G101'
        or not(modern or legacy) or market.get('handi')!=0 or market.get('gameSubject') not in (None,'')
        or str(r.get('gameReject'))!='0' or str(r.get('buyReject'))!='0'):return None
    if (r.get('home_team_id'),r.get('away_team_id'))!=tuple(g['teams']) or str(r.get('kickoff_kst',''))[:19]!=g['kickoff_local']:return None
    try:v=[float(odds[k]) for k in ('home','draw','away')]
    except (KeyError,TypeError,ValueError):return None
    if not all(math.isfinite(x) and x>1 for x in v):return None
    return {'odds':v,'product_id':[r.get('gmId'),r.get('gmTs'),r.get('matchSeq')],'availability_tier':r.get('availability_tier')}

def partition(games):
    train=[g for g in games if g['date'].year==2025 and g['date']<=TRAIN_CUTOFF]
    validation=[g for g in games if dt.date(2026,1,1)<=g['date']<=CUTOFF]
    embargo=[g for g in games if CUTOFF<g['date']<REF]
    test=[g for g in games if g['date']>=REF]
    if len(train)+len(validation)+len(embargo)+len(test)!=len(games):raise ValueError('unassigned fixture')
    return train,validation,embargo,test
