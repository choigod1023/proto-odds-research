"""Independent arithmetic and fail-closed checks for the fixed KBO contract.

No model worker module is imported. Frozen artifact adapters belong in a later step.
"""
import hashlib
import json
import math
import subprocess
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

CONTRACT_REPO=Path('C:/Users/user/Documents/Codex/kbnext')
CONTRACT_PATH=Path('experiments/kbo-player-effects-20261103/contract.json')
CONTRACT_COMMIT='7ceda125409fd29e1178b40f7d0066606be92d2a'
CONTRACT_SHA='6ffdf7947559df052c248a1f9fb5e7ad871321f52e7920126934bf21771465ed'
ORDER=(4.7,4.6,4.5,4.4,4.3,4.2,4.1,4.0,3.9)
WEIGHTS=tuple(x/(sum(ORDER)/len(ORDER)) for x in ORDER)

def contract():
    """Read exact registered bytes from Git, independently of the checkout."""
    raw=subprocess.check_output(['git','show',f'{CONTRACT_COMMIT}:{CONTRACT_PATH.as_posix()}'],cwd=CONTRACT_REPO)
    if hashlib.sha256(raw).hexdigest()!=CONTRACT_SHA:raise ValueError('contract hash mismatch')
    c=json.loads(raw)
    if c['schema']!='kbo-player-effects-fixed-research-v1' or len(c['candidate_families'])!=3:raise ValueError('unexpected contract')
    if c['candidate_families']['M1_roster_poisson']['player_l2_grid']!=[1,10,100] or c['candidate_families']['M2_roster_outcome']['player_l2_grid']!=[1,10,100]:raise ValueError('unexpected candidates')
    if c['source_truth']['historical_prematch_receipt_proven'] is not False or c['source_truth']['raw_pitcher_bf_must_not_be_used_as_batters_faced'] is not True:raise ValueError('invalid source scope')
    return c

def prior_history(records,target_day):
    """Select prior-day records without inspecting values, outcomes or target labels."""
    target=date.fromisoformat(target_day)
    return [x for x in records if date.fromisoformat(x['date'])<target]

def assert_prior(records,target_day,target_id):
    for item in records:
        if item.get('fixture_id')==target_id or date.fromisoformat(item['date'])>=date.fromisoformat(target_day):raise ValueError('target/same-date/future history')

def numeric(value,mean,scale):
    """Fit-only parameters supplied by caller; observed zero remains observed."""
    if not all(math.isfinite(x) for x in (mean,scale)) or scale<=0:raise ValueError('invalid scaler')
    if value is None:return 0.0,1
    if not math.isfinite(value):raise ValueError('invalid observation')
    return (value-mean)/scale,0

def exposures(batters,starter,hitter_vocabulary,pitcher_vocabulary):
    """Position-weighted hitter vector and separate opposing-starter vector.

    Unknown IDs have zero identity exposure; separate flags make unknown and
    wholly absent rosters distinguishable. No learned indicator coefficient is
    assumed to be zero merely because an identity effect is neutral.
    """
    hitters={};pitchers={};missing=batters is None
    if not missing:
        if len(batters)!=9 or len(set(batters))!=9 or any(not isinstance(p,str) or not p for p in batters):raise ValueError('invalid lineup')
        unknown=sum(p not in hitter_vocabulary for p in batters)
        for pid,weight in zip(batters,WEIGHTS):
            if pid in hitter_vocabulary:hitters[pid]=hitters.get(pid,0.)+weight
    else:unknown=0
    if starter is not None and (not isinstance(starter,str) or not starter):raise ValueError('invalid starter')
    if starter in pitcher_vocabulary:pitchers[starter]=1.0
    return {'hitters':hitters,'opposing_starter':pitchers,'lineup_missing':int(missing),'unknown_hitter_count':unknown,'starter_missing':int(starter is None),'unknown_starter':int(starter is not None and starter not in pitcher_vocabulary)}

def signed_difference(home,away):
    """Keep hitter and pitcher roles separate for 3-way home-minus-away design."""
    result={}
    for role in ('hitters','opposing_starter'):
        ids=set(home[role])|set(away[role]);result[role]={p:home[role].get(p,0.)-away[role].get(p,0.) for p in ids}
    return result

def poisson_3way(home,away,tol=1e-13):
    """Independent joint Poisson grid and explicit upper bound on omitted mass."""
    if not all(math.isfinite(x) and 0<x<=100 for x in (home,away)):raise ValueError('invalid rate')
    def masses(rate):
        term=math.exp(-rate);p=[term];s=term
        for n in range(1,10000):
            term*=rate/n;p.append(term);s+=term
            if n>rate and max(0.,1.-s)<=tol/2:return p,max(0.,1.-s)
        raise ValueError('tail convergence failure')
    h,ht=masses(home);a,at=masses(away);bound=ht+at
    if bound>tol:raise ValueError('tail bound too large')
    p=[0.,0.,0.]
    for i,x in enumerate(h):
        for j,y in enumerate(a):p[0 if i>j else 1 if i==j else 2]+=x*y
    z=sum(p)
    if z<=0 or abs(1-z)>tol+1e-12:raise ValueError('bad probability mass')
    return [v/z for v in p],bound

def softmax(logits):
    if len(logits)!=3 or any(not math.isfinite(v) for v in logits):raise ValueError('invalid logits')
    top=max(logits);v=[math.exp(x-top) for x in logits];total=sum(v)
    return [x/total for x in v]

def select_penalty(losses,tolerance=1e-12):
    """Choose within the registered grid; tied losses prefer shrinkage."""
    if set(losses)!={1,10,100} or any(not math.isfinite(x) for x in losses.values()):raise ValueError('invalid validation grid')
    best=min(losses.values())
    return max(penalty for penalty,value in losses.items() if value<=best+tolerance)

def select_family(metrics,tolerance=1e-12):
    """Use validation log loss, then Brier, then registered complexity order."""
    names=('M0','M1','M2')
    if set(metrics)!=set(names) or any(not all(math.isfinite(v) for v in metrics[n]) for n in names):raise ValueError('invalid validation metrics')
    best=min(metrics[n][0] for n in names);eligible=[n for n in names if metrics[n][0]<=best+tolerance]
    brier=min(metrics[n][1] for n in eligible)
    return next(n for n in names if n in eligible and metrics[n][1]<=brier+tolerance)

def settlement(probabilities,prices,result):
    """2-way pick with fixed home tie; draw is an all-stake refund."""
    if len(probabilities)!=2 or len(prices)!=2 or result not in ('home','away','draw'):raise ValueError('invalid market')
    if not all(math.isfinite(x) and 0<=x<=1 for x in probabilities) or abs(sum(probabilities)-1)>1e-10:raise ValueError('invalid probabilities')
    if any(Decimal(str(x))<=1 or not Decimal(str(x)).is_finite() for x in prices):raise ValueError('invalid odds')
    pick=0 if probabilities[0]>=probabilities[1] else 1
    net=Decimal(0) if result=='draw' else Decimal(str(prices[pick]))-1 if ('home','away')[pick]==result else Decimal(-1)
    return ('home','away')[pick],net

def summarize(records):
    """Return exact unit profit, refund-inclusive ROI, months and daily-close DD."""
    daily=Counter();months=Counter();net=Decimal(0)
    for r in records:
        _,amount=settlement(r['p'],r['prices'],r['winner']);net+=amount;daily[r['date']]+=amount;months[r['date'][:7]]+=amount
    balance=peak=drawdown=Decimal(0)
    for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
    return {'n':len(records),'net':net,'roi':net/len(records) if records else None,'months':dict(months),'drawdown':drawdown if records else None}

def draw_diagnostic(records):
    """Separate mean draw probability and decisive-game conditional performance."""
    if not records:raise ValueError('empty diagnostic cohort')
    losses=[];briers=[];hits=0;draws=0;average=0.
    for r in records:
        p=r['probabilities'];winner=r['winner']
        if set(p)!={'home','draw','away'} or winner not in ('home','draw','away') or any(not math.isfinite(x) or x<0 for x in p.values()) or abs(sum(p.values())-1)>1e-10:raise ValueError('invalid diagnostic row')
        average+=p['draw'];draws+=int(winner=='draw')
        if winner=='draw':continue
        denom=p['home']+p['away']
        if denom<=0:raise ValueError('invalid non-draw conditioning')
        q=[p['home']/denom,p['away']/denom];y=0 if winner=='home' else 1
        losses.append(-math.log(max(q[y],1e-15)));briers.append(sum((x-int(i==y))**2 for i,x in enumerate(q)));hits+=int((0 if q[0]>=q[1] else 1)==y)
    n=len(records);d=len(losses)
    return {'n':n,'observed_draws':draws,'observed_draw_rate':draws/n,'mean_draw_probability':average/n,'decisive_n':d,'decisive_hits':hits,'conditional_accuracy':hits/d if d else None,'conditional_logloss':sum(losses)/d if d else None,'conditional_brier':sum(briers)/d if d else None}
