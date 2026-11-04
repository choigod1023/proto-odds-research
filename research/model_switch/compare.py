"""Recalculate three fixed arms and risk on identical archived fixtures.

Old source publication claims are never promoted to actual pregame receipts.
This module does not run production models, collect pages, or trade.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from devig import shin

ARMS=('vote','market','m0_actual')
M0='kbo-actual-training-2026-M0_actual'
KST=timezone(timedelta(hours=9))
PROTOCOL=Path(__file__).with_name('protocol.json')


def digest(raw): return hashlib.sha256(raw).hexdigest()


def stamp(value):
    t=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if t.utcoffset() is None: raise ValueError('explicit source timezone required')
    return t


def price(value):
    if isinstance(value,bool): raise ValueError('invalid price')
    try: p=Decimal(str(value))
    except (InvalidOperation,ValueError): raise ValueError('invalid price')
    if not p.is_finite() or p<=1: raise ValueError('invalid price')
    return p


def choose(q):
    if type(q) not in (int,float) or not math.isfinite(q) or not 0<=q<=1: raise ValueError('invalid probability')
    return None if abs(q-.5)<=1e-12 else 'home' if q>.5 else 'away'


def votes(entries):
    authors=defaultdict(list)
    for v in entries:
        if v['side'] not in ('home','away') or not v['source'] or not v['author']: raise ValueError('invalid vote identity')
        authors[(v['source'],v['author'])].append(v)
    electorate=[]
    for author,items in authors.items():
        first=min(stamp(v['observed_at']) for v in items)
        initial=[v for v in items if stamp(v['observed_at'])==first]
        if len({v['side'] for v in initial})==1: electorate.append(min(initial,key=lambda v:(v['url'],v['sha256'])))
    if not electorate: return None,0
    q=sum(v['side']=='home' for v in electorate)/len(electorate)
    return choose(q),len(electorate)


def normalize(rows,m0_predictions):
    ids=set(); markets=set(); output=[]; failure_counts=Counter()
    for r in rows:
        fid=r['fixture_id']; key=(r['official_id'],r['market_id'])
        if fid in ids or key in markets: raise ValueError('duplicate fixture or market')
        ids.add(fid);markets.add(key)
        if not r['market_id'].endswith(':full_game_2way') or set(r['prices'])!={'home','away'}:
            raise ValueError('identical complete full-game two-way market required')
        odds={s:price(p) for s,p in r['prices'].items()}
        h,a=r['scores']
        if any(type(x) is not int or x<0 for x in (h,a)): raise ValueError('invalid official score')
        winner='home' if h>a else 'away' if a>h else 'draw'
        if winner!=r['winner']: raise ValueError('winner disagrees with score')
        m0=m0_predictions[fid]; q=m0['probabilities_2way']['home']
        if m0['event_date']!=stamp(r['kickoff']).astimezone(KST).date().isoformat() or abs(q-r['model_home_probabilities'][M0])>1e-12:
            raise ValueError('frozen M0 prediction binding differs')
        m0_side=choose(q); vote_side,nv=votes(r['votes'])
        market_prob=shin([float(odds['home']),float(odds['away'])]);market_side=choose(market_prob[0])
        selections={'vote':vote_side,'market':market_side,'m0_actual':m0_side}
        # Verify the old result instead of treating old summary numbers as input.
        for arm,legacy in [('vote','vote'),('market','market'),('m0_actual',M0)]:
            if selections[arm]!=r['selections'][legacy]: raise ValueError('old selection disagrees with reconstructed rule')
        if any(p is None for p in selections.values()): raise ValueError('three-arm common active cohort requires unique choices')
        profit={arm:Decimal(0) if winner=='draw' else odds[s]-1 if s==winner else Decimal(-1) for arm,s in selections.items()}
        for arm,legacy in [('vote','vote'),('market','market'),('m0_actual',M0)]:
            if abs(profit[arm]-Decimal(str(r['profit'][legacy])))>Decimal('1e-10'): raise ValueError('stored settlement differs')
        reasons=set(r['strict_failure_reasons'])
        # The input is this already-audited retrospective schema; it cannot
        # certify live eligibility by toggling a boolean on the same row.
        if r['price_time_verified'] is not True:reasons.add('price_receipt_time_unverified')
        if any(stamp(v['observed_at'])>stamp(r['kickoff'])-timedelta(minutes=30) for v in r['votes']):
            reasons.add('original_vote_observed_after_t30')
        reasons.add('retrospective_actual_lineup_and_frozen_model_not_live_capture')
        failure_counts.update(reasons)
        output.append({'fixture_id':fid,'official_id':r['official_id'],'market_id':r['market_id'],
                       'kickoff':r['kickoff'],'date':stamp(r['kickoff']).astimezone(KST).date().isoformat(),
                       'winner':winner,'prices':{s:str(x) for s,x in odds.items()},'vote_authors':nv,
                       'selections':selections,'profit':{k:str(v) for k,v in profit.items()},
                       'strict_pregame_eligible':False,'strict_failure_reasons':sorted(reasons)})
    return sorted(output,key=lambda r:(stamp(r['kickoff']),r['fixture_id'])),dict(failure_counts)


def daily_arrays(rows):
    first=datetime.fromisoformat(rows[0]['date']).date();last=datetime.fromisoformat(rows[-1]['date']).date()
    dates=[(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
    # Columns: bets, decided, and profit/hits for each arm. Missing dates are
    # zero observed bets, not evidence that no real games took place.
    values=np.zeros((len(dates),8));index={d:i for i,d in enumerate(dates)}
    for r in rows:
        v=values[index[r['date']]];v[0]+=1;v[1]+=r['winner']!='draw'
        for j,arm in enumerate(ARMS):
            v[2+j]+=float(r['profit'][arm]);v[5+j]+=r['winner']!='draw' and r['selections'][arm]==r['winner']
    return dates,values


def drawdown(profits):
    peak=curve=worst=0.
    for net in profits:
        curve+=float(net);peak=max(peak,curve);worst=max(worst,peak-curve)
    return worst


def metrics(rows,arm,dates,values):
    net=sum((Decimal(r['profit'][arm]) for r in rows),Decimal(0));decided=[r for r in rows if r['winner']!='draw']
    active=values[:,0]>0;daily=values[active,2+ARMS.index(arm)]
    months=defaultdict(lambda:{'bets':0,'net':Decimal(0),'hits':0,'decided':0})
    for r in rows:
        m=months[r['date'][:7]];m['bets']+=1;m['net']+=Decimal(r['profit'][arm]);m['decided']+=r['winner']!='draw';m['hits']+=r['winner']!='draw' and r['selections'][arm]==r['winner']
    monthly={m:{'bets':v['bets'],'net_units':str(v['net']),'roi':float(v['net']/v['bets']),'hits':v['hits'],'decided':v['decided']} for m,v in sorted(months.items())}
    tail=np.sort(daily)[:max(1,math.ceil(.2*len(daily)))]
    return {'bets':len(rows),'decided':len(decided),'hits':sum(r['selections'][arm]==r['winner'] for r in decided),
            'accuracy':sum(r['selections'][arm]==r['winner'] for r in decided)/len(decided) if decided else None,
            'refunds':len(rows)-len(decided),'net_units':str(net),'roi':float(net/len(rows)),
            'max_daily_close_drawdown_units':drawdown(values[:,2+ARMS.index(arm)]),
            'worst_observed_day_units':float(min(daily)), 'worst_month_units':float(min(v['net'] for v in months.values())),
            'worst20pct_active_days_mean_units':float(np.mean(tail)),
            'positive_observed_months':sum(v['net']>0 for v in months.values()),'months':monthly,
            'daily_close_curve_units':np.cumsum(values[:,2+ARMS.index(arm)]).tolist()}


def bootstrap(values,block,replicates,seed):
    if type(block) is not int or block<=0 or type(replicates) is not int or replicates<=0: raise ValueError('invalid bootstrap budget')
    length=len(values);rng=np.random.default_rng(seed+block);total=np.zeros((replicates,values.shape[1]))
    # Paired circular calendar-day blocks, identical draws for all arms.
    for offset in range(0,length,block):
        starts=rng.integers(0,length,replicates);width=min(block,length-offset)
        sums=np.zeros_like(total)
        for j in range(width):sums+=values[(starts+j)%length]
        total+=sums
    total=total[(total[:,0]>0)&(total[:,1]>0)]
    q=.05/(2*3*2) # family of three arm pairs and two metrics, fixed in protocol
    out={}
    for left,right in [('m0_actual','vote'),('market','vote'),('m0_actual','market')]:
        l,r=ARMS.index(left),ARMS.index(right)
        roi=(total[:,2+l]-total[:,2+r])/total[:,0]
        accuracy=(total[:,5+l]-total[:,5+r])/total[:,1]
        out[left+'_minus_'+right]={'roi_delta_adjusted_interval':np.quantile(roi,[q,1-q]).tolist(),
                                  'accuracy_delta_adjusted_interval':np.quantile(accuracy,[q,1-q]).tolist(),
                                  'valid_replicates':len(total)}
    return out


def capped_wallet(rows,arm):
    # Sensitivity only: actual settlement clocks are not present in the archive.
    byday=defaultdict(list)
    for r in rows:byday[r['date']].append(r)
    months=defaultdict(lambda:{'net':Decimal(0),'bets':0,'skipped':0});curve=[]
    stake=Decimal(10000);limit=Decimal(50000)
    for day,games in sorted(byday.items()):
        m=months[day[:7]];reserved=Decimal(0);chosen=[]
        for r in games:
            if m['net']-reserved-stake < -limit:m['skipped']+=1;continue
            reserved+=stake;m['bets']+=1;chosen.append(r)
        net=sum((Decimal(r['profit'][arm])*stake for r in chosen),Decimal(0));m['net']+=net;curve.append(float(net))
        if m['net'] < -limit: raise AssertionError('simulated loss cap violated')
    bets=sum(m['bets'] for m in months.values());net=sum((m['net'] for m in months.values()),Decimal(0))
    return {'scenario':'KST day-end settlement assumption; simultaneous exposure reserved',
            'bets':bets,'skipped':sum(m['skipped'] for m in months.values()),'net_krw':str(net),
            'roi':float(net/(stake*bets)) if bets else None,'max_daily_close_drawdown_krw':drawdown(curve),
            'months':{k:{'net_krw':str(v['net']),'bets':v['bets'],'skipped':v['skipped']} for k,v in sorted(months.items())},
            'real_wallet_modified':False,'real_settlement_clocks_verified':False,'same_fixtures_after_stopping':False}


def run(source,model_artifact,output):
    raw=Path(source).read_bytes();protocol=json.loads(PROTOCOL.read_bytes())
    if digest(raw)!=protocol['source_rows_sha256']:raise ValueError('corrected source 103 cohort differs')
    original=json.loads(raw)
    if len(original)!=103:raise ValueError('wrong cohort size')
    model_raw=Path(model_artifact).read_bytes()
    if any(r['model_artifact_sha256'][M0]!=digest(model_raw) for r in original):raise ValueError('frozen model artifact differs')
    model={r['fixture_id']:r for r in json.loads(model_raw)['predictions']['actual']['M0_actual']}
    rows,reasons=normalize(original,model);dates,values=daily_arrays(rows)
    bs=protocol['bootstrap'];boot={str(b):bootstrap(values,b,bs['replicates'],bs['seed']) for b in bs['calendar_block_days']}
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'source-103.json.gz').write_bytes(gzip.compress(raw,mtime=0))
    result={'scope':'retrospective three-rule diagnostic; not actual operating-source performance',
            'source_rows_sha256':digest(raw),'m0_artifact_sha256':digest(model_raw),'protocol_sha256':digest(PROTOCOL.read_bytes()),
            'league':'KBO','fixtures':len(rows),'start':rows[0]['kickoff'],'end':rows[-1]['kickoff'],
            'calendar_days':len(dates),'observed_bet_dates':int(sum(values[:,0]>0)),
            'strict_live_common_fixtures':0,'strict_exclusion_counts':reasons,
            'metrics':{a:metrics(rows,a,dates,values) for a in ARMS},'calendar_dates':dates,
            'paired_circular_block_bootstrap':boot,'bootstrap_is_exploratory_not_proof':True,
            'monthly_loss_cap_sensitivity':{a:capped_wallet(rows,a) for a in ARMS},
            'promotion':{'status':'blocked','model_selected':None,'reason':'No audited pregame three-arm common cohort; repeated historical data and wide paired uncertainty cannot authorize a switch.'},
            'production_changed':False,'paper_wallet_changed':False}
    for name,obj in [('results.json',result),('normalized-rows.json',rows)]:
        with (output/name).open('x',encoding='utf8',newline='\n') as f:json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    compact={k:result[k] for k in ('fixtures','calendar_days','observed_bet_dates','promotion')}
    compact['metrics']={a:{k:v for k,v in m.items() if k not in ('daily_close_curve_units','months')} for a,m in result['metrics'].items()}
    print(json.dumps(compact,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--m0-artifact',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.source,a.m0_artifact,a.output)
