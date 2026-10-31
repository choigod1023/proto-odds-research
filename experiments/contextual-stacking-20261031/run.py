"""One preregistered retrospective comparison; no production or wallet writes."""
from collections import Counter,defaultdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import numpy as np
from common import HERE,ROOT,LEAGUES,COMPONENTS,at,canonical,read_gz,write_gz,save,sha
from model import study


def settle(feature,p,label,method,weight=None):
    probability=np.array(p,dtype=float);pick=int(np.argmax(probability));y=label['outcome']
    if len(probability)!=len(feature['sides']) or not np.isfinite(probability).all() or (probability<0).any() or abs(probability.sum()-1)>1e-9:
        raise ValueError('invalid forecast')
    market=np.array(feature['components']['market']);maximum=float(market.max())
    top=np.flatnonzero(np.abs(market-maximum)<1e-12)
    group='underdog' if market[pick]<maximum-1e-12 else 'favorite' if len(top)==1 else 'top_tie'
    hit=pick==y;profit=Decimal(str(feature['odds'][pick]))-1 if hit else Decimal(-1)
    return {'id':feature['id'],'league':feature['league'],'day':feature['day'],
            'week':at(feature['decision_at']).strftime('%G-W%V'),'method':method,'pick':pick,
            'probabilities':probability.tolist(),'outcome':y,'hit':int(hit),'odds':feature['odds'][pick],
            'profit':str(profit),'logloss':float(-np.log(max(probability[y],1e-15))),
            'brier':float(sum((probability[i]-int(i==y))**2 for i in range(len(probability)))),
            'market_p':float(market[pick]),'chosen_p':float(probability[pick]),'group':group,
            'market_rank':1+int(np.sum(market>market[pick]+1e-12)),
            'side':feature['sides'][pick],'weights':weight}


def metric(rows,direction_only=False):
    n=len(rows)
    if not n:return {'n':0,'hit_rate':None,'roi':None}
    daily=defaultdict(Decimal);monthly=defaultdict(Decimal)
    for r in rows:
        daily[r['day']]+=Decimal(r['profit']);monthly[r['day'][:7]]+=Decimal(r['profit'])
    balance=peak=drawdown=Decimal(0)
    for day in sorted(daily):
        balance+=daily[day];peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
    return {'n':n,'hits':sum(r['hit'] for r in rows),'hit_rate':sum(r['hit'] for r in rows)/n,
            'roi':float(sum(Decimal(r['profit']) for r in rows)/n),
            'logloss':None if direction_only else sum(r['logloss'] for r in rows)/n,
            'brier':None if direction_only else sum(r['brier'] for r in rows)/n,
            'monthly_profit_units':{m:float(v) for m,v in sorted(monthly.items())},
            'daily_close_max_drawdown_units':float(drawdown),
            'mean_market_p':sum(r['market_p'] for r in rows)/n,
            'mean_chosen_p':None if direction_only else sum(r['chosen_p'] for r in rows)/n,
            'selection_groups':dict(Counter(r['group'] for r in rows))}


def paired(rows,baseline):
    base={r['id']:r for r in baseline}
    if {r['id'] for r in rows}!=set(base):raise ValueError('unmatched comparison')
    data=defaultdict(lambda:np.zeros(3))
    for r in rows:
        b=base[r['id']];data[r['week']]+=np.array([1.,r['hit']-b['hit'],float(Decimal(r['profit'])-Decimal(b['profit']))])
    weeks=sorted(data);values=np.array([data[w] for w in weeks]);rng=np.random.default_rng(1031)
    draws=values[rng.integers(0,len(weeks),size=(2000,len(weeks)))].sum(axis=1)
    intervals=np.quantile(draws[:,1:]/draws[:,0,None],[.025,.975],axis=0)
    changed=sum(r['pick']!=base[r['id']]['pick'] for r in rows)
    return {'n':len(rows),'weeks':len(weeks),'changed_picks':changed,
            'accuracy_delta':float(values[:,1].sum()/len(rows)),
            'roi_delta':float(values[:,2].sum()/len(rows)),
            'accuracy_difference_ci95':intervals[:,0].tolist(),'roi_difference_ci95':intervals[:,1].tolist(),
            'warning':'descriptive repeated retrospective analysis; not fresh confirmatory inference'}


def archived_author_predictions():
    path=ROOT/'experiments/underdog-diagnostics-20261031/inputs/expert-forecasts.jsonl'
    result={}
    for line in path.read_text(encoding='utf8').splitlines():
        row=json.loads(line)
        if row['model']!='selected':continue
        key=(row['league']+'|'+row['event_id'],row['mode'])
        if key in result:raise ValueError('duplicate author reference')
        result[key]=row['probabilities']
    return result


def run():
    features=read_gz(HERE/'inputs/features.jsonl.gz');labels={r['id']:r for r in read_gz(HERE/'inputs/labels.jsonl.gz')}
    provenance=json.loads((HERE/'inputs/provenance.json').read_text(encoding='utf8'))
    if sha(HERE/'inputs/features.jsonl.gz')!=provenance['features_sha256'] or sha(HERE/'inputs/labels.jsonl.gz')!=provenance['labels_sha256']:
        raise ValueError('prepared input hash mismatch')
    selection,models,test=study(features,labels)
    # Flipping every evaluation outcome cannot alter fit, selection or any forecast.
    poisoned={key:dict(value) for key,value in labels.items()}
    for r in test:poisoned[r['id']]['outcome']=(poisoned[r['id']]['outcome']+1)%len(r['sides'])
    repeated,repeated_models,repeated_test=study(features,poisoned)
    if canonical(selection)!=canonical(repeated):raise AssertionError('test-label leakage into selection')
    if [r['id'] for r in repeated_test]!=[r['id'] for r in test]:raise AssertionError('outcome-dependent evaluation membership')
    pred={};weight={}
    for method,model in models.items():
        pred[method],weight[method]=model.predict(test)
        if canonical((pred[method],weight[method]))!=canonical(repeated_models[method].predict(test)):
            raise AssertionError('test-label leakage into forecast')
    author=archived_author_predictions();records=[];forecasts=[];agreement=[]
    for i,row in enumerate(test):
        native={name:row['components'][name] for name in ('market','score_static','outcome_static')}
        native['fixed91']=(.9*np.array(row['components']['market'])+.1*np.array(row['raw_vote_score'])).tolist()
        vote=np.array(row['raw_vote_score']);top=np.flatnonzero(vote==vote.max())
        if len(top)!=1:raise ValueError('unique-vote benchmark requires explicit abstention handling')
        native['vote_direction']=vote.tolist()
        for mode in ('frozen','prequential'):
            native['prior_author_'+mode]=author[(row['id'],mode)]
        for name in models:native[name]=pred[name][i]
        for name,p in native.items():
            records.append(settle(row,p,labels[row['id']],name,weight[name][i] if name in weight else None))
            forecasts.append({'id':row['id'],'decision_at':row['decision_at'],'method':name,
                              'probabilities':p,'weights':weight[name][i] if name in weight else None})
        component_picks=[int(np.argmax(row['components'][name])) for name in COMPONENTS]
        unique_tops=all(np.count_nonzero(np.isclose(p,max(p),rtol=0,atol=1e-12))==1 for p in row['components'].values())
        unanimous=unique_tops and len(set(component_picks))==1
        agreement.append({'id':row['id'],'league':row['league'],'unanimous_unique':unanimous,
                          'unanimous_wrong':unanimous and component_picks[0]!=labels[row['id']]['outcome'],
                          'component_picks':component_picks})
    grouped=defaultdict(list)
    for r in records:grouped[r['method']].append(r)
    summary={name:metric(rows,name=='vote_direction') for name,rows in grouped.items()}
    league={l:{name:metric([r for r in rows if r['league']==l],name=='vote_direction') for name,rows in grouped.items()} for l in LEAGUES}
    strata={};contrasts={}
    for name,rows in grouped.items():
        strata[name]={group:metric([r for r in rows if r['group']==group],name=='vote_direction') for group in ('favorite','underdog','top_tie')}
        under_ids={r['id'] for r in rows if r['group']=='underdog'}
        strata[name]['underdog_selection_share']=len(under_ids)/len(test)
        strata[name]['market_on_same_underdog_events']=metric([r for r in grouped['market'] if r['id'] in under_ids])
        strata[name]['underdog_by_league']={l:metric([r for r in rows if r['league']==l and r['group']=='underdog'],name=='vote_direction') for l in LEAGUES}
        contrasts[name]={base:paired(rows,grouped[base]) for base in ('market','fixed91')}
    save(HERE/'results.json',{'selection':selection,'summary':summary,'by_league':league,'underdog':strata,
                            'paired':contrasts,'shared_errors':{'n':len(test),
                            'same_unique_pick':sum(r['unanimous_unique'] for r in agreement),
                            'same_unique_wrong':sum(r['unanimous_wrong'] for r in agreement),
                            'limitation':'Those fixed-vector unanimous errors cannot be repaired by ANY nonnegative mixture. Not a ceiling for a new model with new inputs, nor an attainable oracle accuracy.'},
                            'limitations':provenance,'test_label_flip_invariant':True})
    write_gz(HERE/'forecasts.jsonl.gz',forecasts);write_gz(HERE/'settlements.jsonl.gz',records)
    save(HERE/'agreement.json',agreement)
    save(HERE/'execution.json',{'protocol_lf_sha256':hashlib.sha256((HERE/'protocol.json').read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
        'addendum_lf_sha256':hashlib.sha256((HERE/'underdog-addendum.json').read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
        'selected':selection['selected'],'test_n':len(test),'new_independent_events':0,
        'forecasts_sha256':sha(HERE/'forecasts.jsonl.gz'),'settlements_sha256':sha(HERE/'settlements.jsonl.gz')})
    print(json.dumps({'selection':selection,'summary':summary,'shared_errors':json.loads((HERE/'results.json').read_text(encoding='utf8'))['shared_errors']},ensure_ascii=False))


if __name__=='__main__':run()
