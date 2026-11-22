"""Check fixed contract inputs and prior-date lineage without scoring or fitting."""
import gzip
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
import audit_math

REPO=Path('C:/Users/user/Documents/Codex/kbodata')
DATA='13291d23e855e0d500327cb39ac64a480e395837'
FRESH='0d980b40697997021256be9f42f6fd254b3c1049'
BASE=Path('experiments/kbo-player-data-20261103')
SOURCE={'rows.jsonl':'66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c','labels.jsonl':'ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca','prior-projected-features.jsonl.gz':'eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae'}
FRESH_HASH={'projected':'8b090ed9b204a50584c5e2a854bd0c6210a133a8518de78db51ca8f4217aa23f','official_batting_and_starter':'82a1e8630fafe2e790957900e981512fdfc8e70ba536f5ced45ac785f79dc706'}

def blob(commit,path,expected):
    raw=subprocess.check_output(['git','show',f'{commit}:{path.as_posix()}'],cwd=REPO)
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('pinned Git blob mismatch '+str(path))
    return raw
def decode(raw):return [json.loads(x) for x in gzip.decompress(raw).splitlines()]
def check_prior(obj,target,day,dates):
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k in ('fixture_id','id') and v is not None:
                if v==target or v not in dates or dates[v]>=day:raise ValueError('non-prior source fixture')
            elif k in ('date','latest_source_date') and v is not None:
                if v>=day:raise ValueError('non-prior source date')
            elif k=='source_fixture_ids':
                if any(x==target or x not in dates or dates[x]>=day for x in v):raise ValueError('non-prior source list')
            if k!='provenance':check_prior(v,target,day,dates)
    elif isinstance(obj,list):
        for v in obj:check_prior(v,target,day,dates)

def main():
    c=audit_math.contract()
    for filename,expected in SOURCE.items():blob(DATA,BASE/filename,expected)
    games=decode(blob(DATA,Path('data/model_candidates/kbo/archived-games.jsonl.gz'),'c1561a252f226edcfaceffefedc0295394b2905a7368078b96cf71ba76389fce'))
    dates={x['source_fixture_id']:x['date'] for x in games}
    if len(dates)!=len(games):raise ValueError('duplicate archive fixture')
    projections=decode(blob(DATA,BASE/'prior-projected-features.jsonl.gz',SOURCE['prior-projected-features.jsonl.gz']))
    if len(projections)!=len(dates) or len({x['fixture_id'] for x in projections})!=len(projections):raise ValueError('projection coverage mismatch')
    counts=Counter()
    for row in projections:
        fid=row['fixture_id'];day=dates[fid];counts[day[:4]]+=1
        if row['scenario']!='projected_from_prior_games' or row['availability_tier']!='retrospective_unproven':raise ValueError('invalid prior source')
        check_prior(row['projection'],fid,day,dates);check_prior(row['features'],fid,day,dates)
    if counts!={'2023':723,'2024':728,'2025':736,'2026':726}:raise ValueError('unexpected seasonal coverage')
    full={}
    for name,expected in FRESH_HASH.items():
        data=decode(blob(FRESH,BASE/('fresh-history-2026-'+name+'.jsonl.gz'),expected));index={x['fixture_id']:x for x in data}
        if len(index)!=709 or len(index)!=len(data):raise ValueError('fresh cohort identity')
        for fid,row in index.items():
            if fid not in dates or dates[fid][:4]!='2026' or row['received_at'] is not None or row['completed_at'] is not None:raise ValueError('invalid fresh date or availability')
            check_prior(row['projection'],fid,dates[fid],dates);check_prior(row['features'],fid,dates[fid],dates)
        full[name]=index
    if set(full['projected'])!=set(full['official_batting_and_starter']):raise ValueError('scenario cohorts differ')
    # Only player-lineup and starter selectors may differ; team, relief and park are common.
    allowed={s+'_'+v for s in ('home','away') for v in ('lineup_strength','starter_strength')}
    for fid,a in full['projected'].items():
        b=full['official_batting_and_starter'][fid]
        if {k:v for k,v in a['features'].items() if k not in allowed}!={k:v for k,v in b['features'].items() if k not in allowed}:raise ValueError('non-selector feature changed')
    print(json.dumps({'status':'PASS','contract_sha256':audit_math.CONTRACT_SHA,'season_counts':dict(counts),'fixed_2026_cohort':len(full['projected']),'temporal_boundary':'strict prior calendar date','pregame_receipt_proven':False},sort_keys=True))

if __name__=='__main__':main()
