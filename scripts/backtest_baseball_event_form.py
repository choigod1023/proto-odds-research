"""Frozen historical recent-form/relief models, evaluated against same-game 9:1."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
from collections import defaultdict
from datetime import date,timedelta
from pathlib import Path
from decimal import Decimal
import argparse,hashlib,json,sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit,logit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities
from collect_baseball_event_history import read

RECENT=['obp14','slg14','k14','obp30','slg30','obp_change','slg_change','starter_era30',
        'starter_kbb30','starter_rest','team_obp14','team_slg14','team_ra14']
BULLPEN=['relief_pitches1','relief_pitches3','relief_consecutive','relief_top3_used1','relief_kbb28']
CHANGES=['lineup_replacements','lineup_strength_revision','probable_starter_change']
BUNDLES={'recent':RECENT,'bullpen':BULLPEN,'combined':RECENT+BULLPEN,'combined_changes':RECENT+BULLPEN+CHANGES}


def market(row):
    t=row.get('target')
    return market_probabilities(t['prices'])[t['order'].index('home')] if t else market_probabilities(row['historical_prices'])[0]


def reference(row):
    t=row.get('target');m=market(row)
    return .9*m+.1*t['vote_counts'].get('home',0)/t['voters'] if t else m


def objective(beta,x,y,offset,ridge):
    eta=offset+x@beta
    return (float(np.mean(np.logaddexp(0,eta)-y*eta)+ridge*beta@beta/2),
            x.T@(expit(eta)-y)/len(y)+ridge*beta)


def fit(rows,bundle,ridge):
    names=BUNDLES[bundle];x=np.array([[r['features'][n] for n in names] for r in rows])
    scale=np.where(x.std(axis=0)>1e-8,x.std(axis=0),1.);x=np.clip(x/scale,-6,6)
    offset=logit([market(r) for r in rows]);y=np.array([r['hg']>r['ag'] for r in rows],float)
    result=minimize(objective,np.zeros(x.shape[1]),args=(x,y,offset,ridge),method='L-BFGS-B',jac=True,
                    options=dict(maxiter=2000,gtol=1e-7,ftol=1e-11))
    if not result.success:raise ValueError(str(result.message))
    return dict(names=names,scale=scale.tolist(),beta=result.x.tolist(),ridge=ridge,
                train_n=len(rows),first=min(r['date'] for r in rows),last=max(r['date'] for r in rows))


def predict(config,rows,base=market):
    initial=np.array([base(r) for r in rows])
    if config is None:return initial
    x=np.clip(np.array([[r['features'][n] for n in config['names']] for r in rows])/np.array(config['scale']),-6,6)
    return expit(logit(initial)+x@np.array(config['beta']))


def metrics(rows,ps,total=None):
    if not rows:return dict(n=0,accuracy=None,roi=None)
    wins=pushes=0;net=Decimal(0);monthly=defaultdict(Decimal);picks=[];probs=[];labels=[]
    for r,p in zip(rows,ps):
        home=p>=.5;t=r.get('target');odds=t['prices'] if t else r['historical_prices'];order=t['order'] if t else ['home','away']
        selected=Decimal(str(odds[order.index('home' if home else 'away')]))
        if r['hg']==r['ag']:pushes+=1;profit=Decimal(0)
        else:
            correct=home==(r['hg']>r['ag']);wins+=int(correct);profit=selected-1 if correct else Decimal(-1)
            probs.append(float(p));labels.append(int(r['hg']>r['ag']))
        net+=profit;monthly[r['date'][:7]]+=profit;picks.append(home)
    p=np.clip(probs,1e-9,1-1e-9);y=np.array(labels)
    return dict(n=len(rows),wins=wins,pushes=pushes,accuracy=wins/(len(rows)-pushes) if len(rows)>pushes else None,
                roi=float(net/len(rows)),net_krw=int(net*10000),coverage=len(rows)/(total or len(rows)),
                brier=float(np.mean((p-y)**2)) if len(y) else None,
                logloss=float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p))) if len(y) else None,
                home_picks=int(sum(picks)),monthly_units={k:float(v) for k,v in monthly.items()})


def paired_interval(rows,p,q):
    weeks=defaultdict(lambda:np.zeros(4))
    for r,a,b in zip(rows,p,q):
        d=date.fromisoformat(r['date']);k=(d-timedelta(days=d.weekday())).isoformat()
        draw=r['hg']==r['ag'];homewin=r['hg']>r['ag'];t=r.get('target');odds=t['prices'] if t else r['historical_prices'];order=t['order'] if t else ['home','away']
        pick=[a>=.5,b>=.5];hits=[int(s==homewin and not draw) for s in pick]
        nets=[0 if draw else odds[order.index('home' if s else 'away')]-1 if s==homewin else -1 for s in pick]
        weeks[k]+=[hits[0]-hits[1],nets[0]-nets[1],int(not draw),1]
    if len(weeks)<2:return dict(weeks=len(weeks))
    values=np.array(list(weeks.values()));rng=np.random.default_rng(20261029)
    sums=values[rng.integers(len(values),size=(3000,len(values)))].sum(axis=1);valid=sums[:,2]>0
    return dict(weeks=len(weeks),accuracy_delta95=np.quantile(sums[valid,0]/sums[valid,2],[.025,.975]).tolist(),
                roi_delta95=np.quantile(sums[:,1]/sums[:,3],[.025,.975]).tolist())


def run(folder):
    facts=sorted(read(folder/'event-features.jsonl'),key=lambda r:(r['date'],r['gamePk']))
    development=[r for r in facts if r['role']=='train'];train=[r for r in development if r['date']<'2024-08-04'];val=[r for r in development if r['date']>='2024-08-04']
    evaluation=[r for r in facts if r['role']=='evaluation']
    assert max(r['date'] for r in train)<min(r['date'] for r in val)<min(r['date'] for r in evaluation)
    if len(train)<80 or len(val)<40:raise ValueError('insufficient chronological development cohort')
    models={};configs={};predictions={}
    for bundle in BUNDLES:
        for ridge in [.01,.1,1.]:
            name=f'{bundle}:l{ridge:g}';selection=fit(train,bundle,ridge);final=fit(development,bundle,ridge)
            models[name]=dict(validation=metrics(val,predict(selection,val)),selection_fit=selection,final_fit=final)
            configs[name]=final
    models['market_null']=dict(validation=metrics(val,predict(None,val)),selection_fit=None,final_fit=None);configs['market_null']=None
    chosen=min(models,key=lambda n:(-models[n]['validation']['accuracy'],models[n]['validation']['brier'],n))
    new_only=min((n for n in models if n!='market_null'),key=lambda n:(-models[n]['validation']['accuracy'],models[n]['validation']['brier'],n))
    partitions={'all':evaluation,'2025_postseason':[r for r in evaluation if r['date'].startswith('2025')],
                '2026_before_july':[r for r in evaluation if '2026-01-01'<=r['date']<'2026-07-01'],
                '2026_july_onward':[r for r in evaluation if r['date']>='2026-07-01']}
    for name,config in configs.items():
        ps=predict(config,evaluation);transfer=predict(config,evaluation,reference)
        models[name].update(evaluation_market=metrics(evaluation,ps),evaluation_91=metrics(evaluation,transfer))
        predictions[name]=transfer
    results={}
    for period,rows in partitions.items():
        base=np.array([reference(r) for r in rows]);m=np.array([market(r) for r in rows]);p=predict(configs[chosen],rows,reference)
        results[period]=dict(existing91=metrics(rows,base),market=metrics(rows,m),selected91=metrics(rows,p),
            changes_vs91=int(sum((a>=.5)!=(b>=.5) for a,b in zip(p,base))),interval=paired_interval(rows,p,base),
            fixed_bundle_comparisons={})
        for bundle in BUNDLES:
            name=min((n for n in models if n.startswith(bundle+':')),key=lambda n:(-models[n]['validation']['accuracy'],models[n]['validation']['brier'],n))
            results[period]['fixed_bundle_comparisons'][bundle]=dict(selected_on_2024_validation=name,metrics=metrics(rows,predict(configs[name],rows,reference)))
    ledger=[]
    for r in evaluation:
        ledger.append(dict(r,market_probability=market(r),existing91_probability=reference(r),
            probabilities={n:float(predict(configs[n],[r],reference)[0]) for n in configs}))
    (folder/'event-predictions.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in ledger),encoding='utf8',newline='\n')
    report=dict(train_n=len(train),validation_n=len(val),development_n=len(development),evaluation_n=len(evaluation),
        selected=chosen,best_transformed=new_only,models=models,periods=results,
        fitting_note='market-offset coefficients trained on 2024 prices, transferred to 9:1 at evaluation; no 2024 tipster training',
        input_sha256=hashlib.sha256((folder/'event-features.jsonl').read_bytes().replace(b'\r\n',b'\n')).hexdigest())
    (folder/'event-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in report.items() if k!='models'},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);run(p.parse_args().folder)
