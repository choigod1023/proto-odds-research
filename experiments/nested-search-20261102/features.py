"""Causal team-form features and separate development/outer files; no model scoring."""
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from src.devig import shin

ALLOWED={'id','day','home_id','away_id','prices','market_p','dc_p'}


def read(path):
    return [json.loads(s) for s in Path(path).read_text(encoding='utf8').splitlines() if s.strip()]


def vector(p):
    p=np.asarray(p,dtype=float)
    if p.shape!=(3,) or not np.isfinite(p).all() or min(p)<=0 or abs(p.sum()-1)>1e-8:
        raise ValueError('invalid probability')
    return p


def team_form(history, team, day):
    cutoff=(date.fromisoformat(day)-timedelta(days=7)).isoformat()
    past=sorted((r for r in history if r['day']<=cutoff and team in (r['home_id'],r['away_id'])),
                key=lambda r:(r['day'],r['id']))[-5:]
    if not past:return [0.,0.,0.,0.,0.,1.]
    results=[];gf=[];ga=[]
    for r in past:
        h=team==r['home_id'];a,b=(r['hg'],r['ag']) if h else (r['ag'],r['hg'])
        results.append(0 if a>b else 1 if a==b else 2);gf.append(a);ga.append(b)
    rest=min(60,(date.fromisoformat(day)-date.fromisoformat(past[-1]['day'])).days)/60
    return [results.count(j)/len(past) for j in range(3)]+[sum(gf)/len(gf)/3,sum(ga)/len(ga)/3,rest]


def make_features(row, history):
    if set(row)!=ALLOWED:raise ValueError('unexpected target features, forbid score/outcome fields')
    if row['home_id']==row['away_id']:raise ValueError('team identity')
    market,dc=vector(row['market_p']),vector(row['dc_p'])
    if len(row['prices'])!=3 or any(not Decimal(x).is_finite() or Decimal(x)<=1 for x in row['prices']):
        raise ValueError('price identity')
    if max(abs(np.array(shin([float(x) for x in row['prices']]))-market))>1e-8:
        raise ValueError('market probability/price mismatch')
    base=np.log(np.r_[market,dc]).tolist()
    disagreement=base+(market-dc).tolist()+[sum(1/float(x) for x in row['prices'])-1,float(-(market*np.log(market)).sum())]
    form=team_form(history,row['home_id'],row['day'])+team_form(history,row['away_id'],row['day'])
    return {'base':base,'disagreement':disagreement,'form':disagreement+form,'form12':form}


def history_rows():
    earlier=read(HERE/'inputs/official-history.jsonl')
    h=[dict(id=r['fixture_id'],day=r['day'],home_id=r['home_team_id'],away_id=r['away_team_id'],
            hg=r['scores']['home'],ag=r['scores']['away']) for r in earlier]
    h.extend({k:r[k] for k in ('id','day','home_id','away_id','hg','ag')} for r in read(HERE/'inputs/official-2026.jsonl'))
    if len(h)!=888 or len({r['id'] for r in h})!=888:raise ValueError('official history changed')
    if any(type(r[k]) is not int or r[k]<0 for r in h for k in ('hg','ag')):raise ValueError('invalid goals')
    return h


def save(path, rows):
    with path.open('x',encoding='utf8',newline='\n') as f:
        for r in rows:f.write(json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')


def prepare():
    history=history_rows();prior=read(HERE/'inputs/old_oof.jsonl')
    if len(prior)!=455 or Counter(r['day'][:4] for r in prior)!={'2024':228,'2025':227}:
        raise ValueError('development cohort')
    dev=[]
    for r in prior:
        x={k:r[k] for k in ALLOWED}
        if date.fromisoformat(r['dc_training_cutoff'])>date.fromisoformat(r['day'])-timedelta(days=7):
            raise ValueError('noncausal DC')
        if r['y']!=(0 if r['hg']>r['ag'] else 1 if r['hg']==r['ag'] else 2):raise ValueError('outcome mismatch')
        dev.append({**x,'y':r['y'],'features':make_features(x,history)})
    raw=json.loads((HERE/'inputs/outer-opportunities.json').read_text(encoding='utf8'))
    outer=[];labels=[]
    for r in raw:
        if r['eligibility']!='eligible':continue
        if r['market']!='full_time_1x2' or r['selection_order']!=['home','draw','away'] or r['result_status']!='official_final':
            raise ValueError('invalid outer market/result')
        x={k:r[k] for k in ALLOWED if k!='market_p'}
        x['market_p']=shin([float(v) for v in r['prices']])
        outer.append({**x,'features':make_features(x,history)})
        labels.append({'id':r['id'],'y':0 if r['home_score']>r['away_score'] else 1 if r['home_score']==r['away_score'] else 2})
    if len(outer)!=77:raise ValueError('outer cohort changed')
    output=HERE/'prepared';output.mkdir(exist_ok=False)
    save(output/'development.jsonl',sorted(dev,key=lambda r:(r['day'],r['id'])))
    save(output/'outer-features.jsonl',sorted(outer,key=lambda r:(r['day'],r['id'])))
    save(output/'outer-labels.jsonl',sorted(labels,key=lambda r:r['id']))
    manifest={'development_rows':455,'outer_rows':77,'outer_classification':'previously_seen_2026_diagnostic_not_blind',
      'feature_names':{'base':6,'disagreement':11,'form':23,'form12':12},'result_availability':'T-7day research assumption',
      'price_receipt_verified':False,'tipster_or_operating_predictions_available':False,
      'input_lf_sha256':{p.name:hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in sorted((HERE/'inputs').glob('*'))}}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in manifest.items() if k!='input_lf_sha256'}))


if __name__=='__main__':prepare()
