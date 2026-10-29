"""Bounded feature transformations and selective prediction, offline research only."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
from collections import Counter,defaultdict
from datetime import date,timedelta
from pathlib import Path
from itertools import product
import argparse
import hashlib
import json
import math
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit,logit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities

LEGACY=['legacy_log_era','kbb','obp','slg','xw_allowed','xera','xw']
RATES=['legacy_log_era','kbb','fip','k','bb','hr','contact','gb','endurance','efficiency','obp','slg','iso','batk','batbb',
    'weighted_obp','weighted_slg','top4_power','bottom5_obp','batting_depth','bat_dispersion']
MATCHUP=RATES+['log5_matchup','strikeout_matchup','power_matchup','discipline_matchup','pitcher_run_environment','power_run_environment']
GAP=MATCHUP+['average_vs_prior_xba','slg_vs_prior_xslg','era_vs_prior_xera','xw','xw_allowed','xera']
BUNDLES={'legacy7':LEGACY,'rates':RATES,'matchup':MATCHUP,'gap':GAP,'groups':GAP,'nonlinear':GAP}
GROUPS=['early_season','novice_starter','low_batter_sample','postseason']
GATES=['all','market60','market65','model55','model60','agreement']


def read(path):return [json.loads(s) for s in path.read_text(encoding='utf8').splitlines() if s]


def market_p(row):
    if 'target' in row:
        t=row['target'];return market_probabilities(t['prices'])[t['order'].index('home')]
    return market_probabilities(row['historical_prices'])[0]


def reference_p(row):
    if 'target' not in row:return market_p(row)
    t=row['target'];return .9*market_p(row)+.1*t['vote_counts'].get('home',0)/t['voters']


def split_dates(rows,fraction=.7):
    ordered=sorted(rows,key=lambda r:(r['date'],r['gamePk']))
    cut=ordered[min(len(ordered)-1,int(len(ordered)*fraction))]['date']
    return [r for r in ordered if r['date']<cut],[r for r in ordered if r['date']>=cut]


def design(rows,config):
    raw=np.array([[r['features'][n] for n in config['names']] for r in rows])
    z=np.clip((raw-np.array(config['center']))/np.array(config['scale']),-6,6)
    result=z
    if config['bundle']=='groups':
        result=np.concatenate([z]+[z*np.array([r['groups'][g] for r in rows])[:,None] for g in config['groups']],axis=1)
    if config['bundle']=='nonlinear':result=np.c_[z,z*np.abs(z)]
    if config['link']=='intercept':result=np.c_[np.ones(len(rows)),result]
    return result


def objective(beta,x,y,offset,ridge,intercept):
    eta=offset+x@beta
    penalty=np.array(beta,copy=True)
    if intercept:penalty[0]=0
    return (float(np.mean(np.logaddexp(0,eta)-y*eta)+ridge*np.dot(penalty,penalty)/2),
        x.T@(expit(eta)-y)/len(y)+ridge*penalty)


def fit(rows,bundle,link,ridge):
    names=BUNDLES[bundle]
    raw=np.array([[r['features'][n] for n in names] for r in rows])
    config=dict(bundle=bundle,link=link,ridge=ridge,names=names,
        center=(raw.mean(axis=0) if link=='intercept' else np.zeros(len(names))).tolist(),
        scale=np.where(raw.std(axis=0)>1e-8,raw.std(axis=0),1).tolist(),
        groups=[g for g in GROUPS if min(sum(r['groups'][g] for r in rows),sum(not r['groups'][g] for r in rows))>=40])
    x=design(rows,config);y=np.array([int(r['hg']>r['ag']) for r in rows])
    offset=np.array([logit(market_p(r)) for r in rows]) if link=='market_offset' else np.zeros(len(rows))
    result=minimize(objective,np.zeros(x.shape[1]),args=(x,y,offset,ridge,link=='intercept'),jac=True,method='L-BFGS-B',
        options=dict(maxiter=3000,maxls=40,ftol=1e-10,gtol=1e-6))
    if not result.success:raise ValueError(f'{bundle}/{link}/{ridge}: {result.message}')
    config.update(beta=result.x.tolist(),train_n=len(rows),train_first=rows[0]['date'],train_last=rows[-1]['date'],success=True)
    return config


def predict(config,rows,use91=False):
    if config is None:return np.array([reference_p(r) if use91 else market_p(r) for r in rows])
    offset=np.array([logit(reference_p(r) if use91 else market_p(r)) for r in rows]) if config['link']=='market_offset' else 0
    return expit(offset+design(rows,config)@np.array(config['beta']))


def measure(rows,ps,mask=None):
    mask=np.ones(len(rows),dtype=bool) if mask is None else np.array(mask,dtype=bool)
    selected=[(r,float(p)) for r,p,keep in zip(rows,ps,mask) if keep]
    if not selected:return dict(n=0,coverage=0.,accuracy=None,roi=None,brier=None)
    hit=push=0;profit=[];prob=[];labels=[];monthly=defaultdict(float)
    for r,p in selected:
        prices=r['target']['prices'] if 'target' in r else r['historical_prices']
        order=r['target']['order'] if 'target' in r else ['home','away']
        side='home' if p>=.5 else 'away';winner='home' if r['hg']>r['ag'] else 'away'
        if r['hg']==r['ag']:push+=1;net=0.
        else:
            correct=side==winner;hit+=int(correct);net=prices[order.index(side)]-1 if correct else -1.
            prob.append(p);labels.append(int(winner=='home'))
        profit.append(net);monthly[r['date'][:7]]+=net
    p=np.clip(prob,1e-9,1-1e-9);y=np.array(labels)
    return dict(n=len(selected),coverage=len(selected)/len(rows),wins=hit,pushes=push,
        accuracy=hit/(len(selected)-push) if len(selected)>push else None,
        roi=float(np.mean(profit)),net_krw=round(sum(profit)*10000),
        brier=float(np.mean((p-y)**2)) if len(y) else None,
        logloss=float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p))) if len(y) else None,
        mean_confidence=float(np.mean([max(p,1-p) for _,p in selected])),
        mean_odds=float(np.mean([r['target']['prices'][r['target']['order'].index('home' if p>=.5 else 'away')] if 'target' in r else r['historical_prices'][int(p<.5)] for r,p in selected])),
        monthly_units=dict(monthly))


def gate(name,market,model):
    if name=='all':return np.ones(len(model),dtype=bool)
    if name=='agreement':return (market>=.5)==(model>=.5)
    base=market if name.startswith('market') else model
    threshold=int(name[-2:])/100
    return np.maximum(base,1-base)>=threshold


def select(models,objective_name):
    if objective_name=='logloss':return min(models,key=lambda n:(models[n]['validation']['logloss'],models[n]['validation']['brier'],n))
    return min(models,key=lambda n:(-models[n]['validation']['accuracy'],models[n]['validation']['brier'],n))


def choose_gate(metrics):
    eligible=[g for g,m in metrics.items() if m['n']>=20 and m['coverage']>=.5]
    return min(eligible,key=lambda g:(-metrics[g]['accuracy'],metrics[g]['brier'],-metrics[g]['coverage'],g))


def interval(rows,a,b,mask=None):
    mask=np.ones(len(rows),dtype=bool) if mask is None else mask
    groups=defaultdict(lambda:np.zeros(4))
    for r,p,q,keep in zip(rows,a,b,mask):
        if not keep:continue
        d=date.fromisoformat(r['date']);week=(d-timedelta(days=d.weekday())).isoformat()
        order=r['target']['order'];prices=r['target']['prices'];draw=r['hg']==r['ag']
        win='home' if r['hg']>r['ag'] else 'away';sides=['home' if v>=.5 else 'away' for v in [p,q]]
        hits=[int(s==win and not draw) for s in sides]
        nets=[0 if draw else prices[order.index(s)]-1 if s==win else -1 for s in sides]
        groups[week]+=[hits[0]-hits[1],nets[0]-nets[1],int(not draw),1]
    if len(groups)<2:return dict(weeks=len(groups),accuracy_delta95=[None,None],roi_delta95=[None,None])
    values=np.array(list(groups.values()));rng=np.random.default_rng(20261029)
    sums=values[rng.integers(len(values),size=(3000,len(values)))].sum(axis=1);valid=sums[:,2]>0
    return dict(weeks=len(groups),accuracy_delta95=np.quantile(sums[valid,0]/sums[valid,2],[.025,.975]).tolist(),
        roi_delta95=np.quantile(sums[:,1]/sums[:,3],[.025,.975]).tolist())


def dramatic(a,b,ci):
    lo=ci.get('accuracy_delta95',[None])[0]
    return bool(a['n'] and a['coverage']>=.5 and a['accuracy']-b['accuracy']>=.10-1e-12 and a['roi']>=b['roi'] and lo is not None and lo>0)


def cohort_partitions(cohort):
    result={}
    for league in ['KBO','NPB','MLB']:
        rows=[dict(target=r,date=r['date'],hg=r['hg'],ag=r['ag']) for r in cohort if r['league']==league]
        market=np.array([market_p(r) for r in rows]);baseline=np.array([reference_p(r) for r in rows])
        vote=np.array([r['target']['vote_counts'].get('home',0)/r['target']['voters'] for r in rows])
        confidence=np.maximum(market,1-market);parts={'all':np.ones(len(rows),dtype=bool),
            'vote_market_agree':(vote>=.5)==(market>=.5),'vote_market_disagree':(vote>=.5)!=(market>=.5),
            'vote_can_flip_market':confidence<=5/9,'vote_cannot_flip_market':confidence>5/9,
            'multiple_voters':np.array([r['target']['voters']>=2 for r in rows]),
            'single_voter':np.array([r['target']['voters']==1 for r in rows])}
        for lo,hi in [(.5,.55),(.55,.6),(.6,.65),(.65,.7),(.7,1.0001)]:parts[f'confidence{lo:g}_{hi:g}']=(confidence>=lo)&(confidence<hi)
        result[league]={name:dict(market=measure(rows,market,mask),existing91=measure(rows,baseline,mask),vote=measure(rows,vote,mask)) for name,mask in parts.items()}
    return result


def run(folder):
    facts=read(folder/'mlb-feature-facts.jsonl');priced=sorted([r for r in facts if r['role']=='train' and 'historical_prices' in r],key=lambda r:(r['date'],r['gamePk']))
    train,val=split_dates(priced);evaluation=sorted([r for r in facts if r['role']=='evaluation'],key=lambda r:(r['date'],r['gamePk']))
    if len(train)<80 or len(val)<40:raise ValueError('insufficient chronological priced development sample')
    assert train[-1]['date']<val[0]['date']<=val[-1]['date']<evaluation[0]['date']
    models={};all_prob={};val_prob={};fit_params={}
    for bundle,link,ridge in product(BUNDLES,['intercept','neutral','market_offset'],[.0001,.001,.01,.1]):
        name=f'{bundle}:{link}:l{ridge:g}';m=fit(train,bundle,link,ridge);pv=predict(m,val)
        models[name]=dict(validation=measure(val,pv));val_prob[name]=pv
        final=fit(priced,bundle,link,ridge);pe=predict(final,evaluation)
        fit_params[name]=dict(selection_fit=m,final_fit=final);models[name]['evaluation']=measure(evaluation,pe);all_prob[name]=pe
    models['market_null']=dict(validation=measure(val,predict(None,val)),evaluation=measure(evaluation,predict(None,evaluation)))
    all_prob['market_null']=predict(None,evaluation);val_prob['market_null']=predict(None,val);fit_params['market_null']=dict(selection_fit=None,final_fit=None)
    selected={o:select(models,o) for o in ['logloss','accuracy']}
    policies={};chosen_gates={};gate_tables={};uncertainty={};dramatic_flags={}
    baseline=np.array([reference_p(r) for r in evaluation]);market=np.array([market_p(r) for r in evaluation]);vm=np.array([market_p(r) for r in val])
    for objective_name,name in selected.items():
        p=all_prob[name];pv=val_prob[name]
        gs={g:measure(val,pv,gate(g,vm,pv)) for g in GATES};chosen=choose_gate(gs);chosen_gates[objective_name]=chosen
        gate_tables[objective_name]={g:dict(validation=gs[g],evaluation=measure(evaluation,p,gate(g,market,p)),
            matched_existing91=measure(evaluation,baseline,gate(g,market,p))) for g in GATES}
        config=fit_params[name]['final_fit']
        transfer=predict(config,evaluation,use91=True) if config is None or config['link']=='market_offset' else .9*baseline+.1*p
        for suffix,pred,mask in [('all',p,np.ones(len(evaluation),dtype=bool)),('selected_gate',p,gate(chosen,market,p)),('existing91_transfer',transfer,np.ones(len(evaluation),dtype=bool))]:
            policy=objective_name+':'+suffix
            actual=measure(evaluation,pred,mask);matched=measure(evaluation,baseline,mask);ci=interval(evaluation,pred,baseline,mask)
            policies[policy]=dict(actual=actual,matched_existing91=matched);uncertainty[policy]=ci;dramatic_flags[policy]=dramatic(actual,matched,ci)
    report=dict(train_n=len(train),validation_n=len(val),development_n=len(priced),evaluation_n=len(evaluation),
        train_dates=[train[0]['date'],train[-1]['date']],validation_dates=[val[0]['date'],val[-1]['date']],
        candidate_count=len(models),selected=selected,selected_gates=chosen_gates,models=models,fit_params=fit_params,
        baselines=dict(market=measure(evaluation,market),existing91=measure(evaluation,baseline)),
        policies=policies,gates=gate_tables,uncertainty=uncertainty,dramatic=dramatic_flags,
        partitions=cohort_partitions(read(folder/'baseball-common-cohort.jsonl')),
        input_sha256={n:hashlib.sha256((folder/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for n in ['mlb-feature-facts.jsonl','baseball-common-cohort.jsonl']})
    (folder/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8',newline='\n')
    ledger=[]
    for i,r in enumerate(evaluation):ledger.append(dict(r,probabilities={n:float(p[i]) for n,p in all_prob.items()},market_probability=float(market[i]),existing91_probability=float(baseline[i])))
    (folder/'predictions.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in ledger),encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k in ['train_n','validation_n','candidate_count','selected','selected_gates','policies','dramatic']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);run(p.parse_args().folder)
