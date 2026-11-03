"""Independent, source-agnostic arithmetic for the pinned roster comparison."""
import hashlib
import json
import math
import subprocess
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np

CONTRACT_REPO=Path('C:/Users/user/Documents/Codex/kbtrain')
CONTRACT_COMMIT='c5407e9bf50bc80a26794240d4ada218b28f2f7d'
CONTRACT_PATH=Path('experiments/kbo-actual-training-20261103/contract.json')
CONTRACT_SHA='512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480'
WEIGHTS=tuple(x/4.3 for x in (4.7,4.6,4.5,4.4,4.3,4.2,4.1,4.0,3.9))

def contract():
    raw=subprocess.check_output(['git','show',f'{CONTRACT_COMMIT}:{CONTRACT_PATH.as_posix()}'],cwd=CONTRACT_REPO)
    if hashlib.sha256(raw).hexdigest()!=CONTRACT_SHA:raise ValueError('contract bytes differ')
    c=json.loads(raw)
    if set(c['model_families'])!={'M0','M2'} or c['primary_design']['fixed_hyperparameters_no_new_search'] is not True:raise ValueError('unexpected family contract')
    if c['source_pins']['fixed_M0_M2_commit']!='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa':raise ValueError('wrong corrected baseline')
    return c

def roster_eligibility(item):
    """Check explicit evidence semantics; callers must first authenticate source bytes."""
    allowed_batting={'official_confirmed_lineup','official_actual_starting_lineup'}
    allowed_pitching={'official_start_flag','official_verified_first_pitcher'}
    if item.get('batting_basis') not in allowed_batting:return False,'batting_not_official_starting_nine'
    if item.get('starter_basis') not in allowed_pitching:return False,'pitcher_not_official_actual_start'
    batting=item.get('batting') or [];starter=item.get('starter') or {};fid=item.get('fixture_id');team=item.get('team_id')
    if len(batting)!=9 or [x.get('order') for x in batting]!=list(range(1,10)):return False,'bad_starting_order'
    ids=[x.get('player_id') for x in batting]
    if any(not isinstance(x,str) or not x.isdigit() or int(x)<=0 for x in ids) or len(set(ids))!=9:return False,'missing_or_duplicate_batter_id'
    if any(x.get('fixture_id')!=fid or x.get('team_id')!=team or not x.get('identity_verified') for x in batting):return False,'batter_identity_mismatch'
    if not isinstance(starter.get('player_id'),str) or not starter['player_id'].isdigit() or int(starter['player_id'])<=0 or starter.get('fixture_id')!=fid or starter.get('team_id')!=team or not starter.get('identity_verified'):return False,'starter_identity_mismatch'
    return True,None

def assert_prior(records,target_id,target_date):
    for r in records:
        if r.get('fixture_id')==target_id or date.fromisoformat(r['date'])>=date.fromisoformat(target_date):raise ValueError('target, same-day, or future source')

def scalars(name,value):
    def natural(x):return type(x) is int and x>=0
    if value is None or name=='park_factor':return None
    if name.endswith('_rest_days'):return float(value) if natural(value) else None
    if name.endswith('_team_strength'):
        data=[x['runs_for']-x['runs_against'] for x in value if natural(x.get('runs_for')) and natural(x.get('runs_against'))];return sum(data)/len(data) if data else None
    if name.endswith('_lineup_strength'):
        data=[x for p in value for x in p.get('history') or [] if all(natural(x.get(k)) for k in ('ab','hit','bb')) and x['hit']<=x['ab']];den=sum(x['ab']+x['bb'] for x in data)
        return sum(x['hit']+x['bb'] for x in data)/den if den else None
    if name.endswith('_starter_strength'):
        data=[x for x in value['appearances'] if natural(x.get('er')) and natural(x.get('outs'))];den=sum(x['outs'] for x in data)
        return 27*sum(x['er'] for x in data)/den if den else None
    if name.endswith('_bullpen_load'):
        data=value['games'];return float(sum(x['relief']['outs'] for x in data)) if data and all(natural((x.get('relief') or {}).get('outs')) for x in data) else None
    raise ValueError('unknown scalar')

def numeric(value,mean,scale):
    if not math.isfinite(mean) or not math.isfinite(scale) or scale<=0:raise ValueError('invalid training scaler')
    if value is None:return 0.,1.
    if not math.isfinite(value):raise ValueError('invalid input')
    return (value-mean)/scale,0.
def oriented(row,side):
    other='away' if side=='home' else 'home';keys=[side+'_team_strength',other+'_team_strength',side+'_rest_days',other+'_rest_days','park_factor',side+'_lineup_strength',other+'_starter_strength',other+'_bullpen_load']
    return [row['values'].get(k) for k in keys]
def train_spec(rows):
    if not rows:raise ValueError('empty training')
    a=np.array([oriented(r,s) for r in rows for s in ('home','away')],dtype=float);m=np.isfinite(a);n=m.sum(0);mean=np.divide(np.where(m,a,0).sum(0),n,out=np.zeros(8),where=n>0);variance=np.divide(np.where(m,(a-mean)**2,0).sum(0),n,out=np.zeros(8),where=n>0);scale=np.sqrt(variance);scale[scale<1e-12]=1.
    return {'teams':sorted({r[s+'_id'] for r in rows for s in ('home','away')}),'hitters':sorted({p for r in rows for s in ('home','away') for p in (r['roster'][s]['hitters'] or [])}),'starters':sorted({r['roster'][s]['starter'] for r in rows for s in ('home','away') if r['roster'][s]['starter'] is not None}),'mean':mean.tolist(),'scale':scale.tolist()}
def flags(row,side,spec):
    other='away' if side=='home' else 'home';h=row['roster'][side]['hitters'];p=row['roster'][other]['starter'];return [float(h is None),sum(x not in spec['hitters'] for x in h)/9 if h else 0.,float(p is None),float(p is not None and p not in spec['starters'])]
def design(row,spec,family):
    teams=spec['teams'];h=spec['hitters'];p=spec['starters'];hm={x:i for i,x in enumerate(h)};pm={x:i for i,x in enumerate(p)};nf=[];miss=[];quality=[]
    for side in ('home','away'):
        z=[numeric(v,m,s) for v,m,s in zip(oriented(row,side),spec['mean'],spec['scale'])];nf.append(np.array([q[0] for q in z]));miss.append(np.array([q[1] for q in z]));quality.append(np.array(flags(row,side,spec)))
    if family=='M0':
        return np.array([[1.,float(s=='home')]+[float(row[s+'_id']==t) for t in teams]+[float(row[('away' if s=='home' else 'home')+'_id']==t) for t in teams]+nf[i].tolist()+miss[i].tolist() for i,s in enumerate(('home','away'))])
    if family!='M2':raise ValueError('unregistered family')
    base=[1.]+[float(row['home_id']==t)-float(row['away_id']==t) for t in teams]+(nf[0]-nf[1]).tolist()+(miss[0]-miss[1]).tolist()+(quality[0]-quality[1]).tolist()+(miss[0]+miss[1]).tolist()+(quality[0]+quality[1]).tolist();players=[0.]*(len(h)+len(p))
    for side,sign in [('home',1.),('away',-1.)]:
        hitters=row['roster'][side]['hitters']
        if hitters is not None and (len(hitters)!=9 or len(set(hitters))!=9):raise ValueError('invalid lineup')
        for pid,w in zip(hitters or [],WEIGHTS):
            if pid in hm:players[hm[pid]]+=sign*w
        pid=row['roster'][side]['starter']
        if pid in pm:players[len(h)+pm[pid]]+=sign
    return np.array([base+players])
def poisson(h,a):
    if not all(math.isfinite(x) and 0<x<=100 for x in (h,a)):raise ValueError('invalid rate')
    def mass(rate):
        p=[math.exp(-rate)];total=p[0]
        for i in range(1,10000):
            p.append(p[-1]*rate/i);total+=p[-1]
            if i>rate and max(0.,1-total)<5e-14:return p
        raise ValueError('non-convergent tail')
    hp=mass(h);ap=mass(a);q=[0.,0.,0.]
    for i,x in enumerate(hp):
        for j,y in enumerate(ap):q[0 if i>j else 1 if i==j else 2]+=x*y
    if abs(sum(q)-1)>1e-12:raise ValueError('insufficient probability mass')
    return [x/sum(q) for x in q]
def softmax(values):
    if len(values)!=3 or any(not math.isfinite(x) for x in values):raise ValueError('invalid logits')
    x=[math.exp(v-max(values)) for v in values];return [v/sum(x) for v in x]
def prediction(model,row):
    x=design(row,model['spec'],model['family']);b=np.array(model['beta']);d=x.shape[1]
    if model['family']=='M0':
        rates=np.exp(x@b);p=poisson(*rates)
    else:
        z=np.asarray(x@b.reshape(2,d).T)[0];p=softmax([z[0],z[1],0.]);rates=None
    return p,rates
def settle(p,prices,winner):
    if len(p)!=2 or len(prices)!=2 or winner not in ('home','away','draw') or any(not math.isfinite(x) or x<0 for x in p) or abs(sum(p)-1)>1e-10:raise ValueError('invalid prediction')
    if any(not Decimal(str(x)).is_finite() or Decimal(str(x))<=1 for x in prices):raise ValueError('invalid price')
    pick=0 if p[0]>=p[1] else 1;net=Decimal(0) if winner=='draw' else Decimal(str(prices[pick]))-1 if ('home','away')[pick]==winner else Decimal(-1)
    return ('home','away')[pick],net
def ledger_metrics(rows):
    net=Decimal(0);days=Counter();months=Counter()
    for r in rows:
        _,value=settle(r['p'],r['prices'],r['winner']);net+=value;days[r['date']]+=value;months[r['date'][:7]]+=value
    balance=peak=dd=Decimal(0)
    for day in sorted(days):balance+=days[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    return {'n':len(rows),'net':net,'roi':net/len(rows) if rows else None,'monthly':dict(months),'drawdown':dd if rows else None}
