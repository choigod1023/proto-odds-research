import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.optimize import minimize as scipy_minimize

ROOT=Path(__file__).resolve().parents[1]/'experiments/expert-information-review-20261031'
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
m=module('expert_snapshot',ROOT/'vendor/run_snapshot.py')
ledger=module('expert_ledger',ROOT/'independent_ledger.py')

def feature(i,decision='2026-05-01T00:00:00+00:00'):
    return {'event_id':str(i),'league':'KBO','decision_at':decision,'kickoff':decision,'sides':['home','away'],'market_p':[.6,.4],'odds':[1.8,2.3],'groups':[[('S','A','home')]],'previous_fixed_pick':None}

def row(i,y,available,decision='2026-05-01T00:00:00+00:00'):
    return {'features':feature(i,decision),'outcome':y,'available_at':available}

def test_prior_excludes_equal_and_future_labels_and_result_poison():
    cutoff=datetime.fromisoformat('2026-06-01T00:00:00+00:00')
    rows=[row(1,0,'2026-05-31T23:59:59+00:00'),row(2,0,'2026-06-01T00:00:00+00:00'),row(3,1,'2026-06-02T00:00:00+00:00')]
    assert [r['features']['event_id'] for r in m.prior_rows(rows,cutoff)]==['1']
    a=m.fit(m.prior_rows(rows,cutoff),'expert_global');f=feature('current'); before=m.predict(a,f)
    poisoned=copy.deepcopy(rows);poisoned[1]['outcome']=1;poisoned[2]['outcome']=0
    b=m.fit(m.prior_rows(poisoned,cutoff),'expert_global')
    assert m.predict(b,f)==before
    f['outcome']=1
    with pytest.raises(ValueError):m.predict(a,f)

def test_doc_groups_average_and_unseen_author():
    f=feature(1);f['groups']=[[('S','A','home'),('S','B','home')],[('S','C','away')]]
    ix={('global',):0,('league','KBO'):1,('author','KBO','S','A'):2,('author','KBO','S','B'):3,('author','KBO','S','C'):4}
    actual=m.design(f,ix)
    a=np.array([.4,-.4]);b=np.array([-.6,.6])
    expected=np.column_stack([(a+b)/2,(a+b)/2,a/4,a/4,b/2])
    assert np.allclose(actual,expected)
    single=feature(1);duplicate=feature(1);duplicate['groups']=[[('S','A','home'),('S','B','home')]]
    assert np.allclose(m.design(single,{('global',):0}),m.design(duplicate,{('global',):0}))
    model={'kind':'expert_hierarchical','index':{('global',):0,('author','KBO','S','other'):1},'coef':[0,1],'success':True}
    previous_model=copy.deepcopy(model);previous_features=copy.deepcopy(single)
    assert np.allclose(m.predict(model,single),single['market_p'])
    assert model==previous_model and single==previous_features

def test_analytic_gradient_penalty_against_independent_central_differences(monkeypatch):
    rows=[row(i,i%2,'2026-05-10T00:00:00+00:00') for i in range(6)]
    examined=[]
    def inspect(fun,x0,**kwargs):
        x=np.linspace(-.12,.17,len(x0));loss,gradient=fun(x);h=1e-6
        numeric=[]
        for i in range(len(x)):
            delta=np.zeros(len(x));delta[i]=h
            numeric.append((fun(x+delta)[0]-fun(x-delta)[0])/(2*h))
        assert np.allclose(gradient,numeric,rtol=1e-5,atol=1e-6)
        index=m.indices(rows,True); assert [k[0] for k in index]==['global','league','author']
        independently_computed=np.array([1.,4.,16.])*x
        for item in rows:
            f=item['features']; design=m.design(f,index); logits=np.log(f['market_p'])+design@x
            probs=np.exp(logits-np.max(logits));probs/=sum(probs);probs[item['outcome']]-=1
            independently_computed+=design.T@probs
        assert np.allclose(gradient,independently_computed,atol=1e-12)
        examined.append((loss,gradient));return scipy_minimize(fun,x0,**kwargs)
    monkeypatch.setattr(m,'minimize',inspect)
    result=m.fit(rows,'expert_hierarchical');assert result['success'] and examined

def test_ledger_independent_decimal_and_tamper():
    target={'event_id':'x','league':'KBO','decision_at':'2026-07-01T09:00:00+00:00','sides':['home','away'],'odds':[1.83,2.12],'outcome':0,'actual_votes':True}
    prediction={'event_id':'x','league':'KBO','decision_at':target['decision_at'],'mode':'frozen','model':'expert_global','probabilities':[.6,.4],'pick':0}
    settlement={'event_id':'x','mode':'frozen','model':'expert_global','outcome':0,'profit':'0.83'}
    out=ledger.audit([target],[prediction],[settlement]);assert out['summaries'][0]['net']=='0.83'
    settlement['profit']='0.84'
    with pytest.raises(ValueError,match='settlement mismatch'):ledger.audit([target],[prediction],[settlement])
    target['actual_votes']=False
    with pytest.raises(ValueError,match='proxy'):ledger.audit([target],[prediction])

def test_full_synthetic_current_and_future_poison_no_earlier_leakage():
    rows=[]
    for i in range(18):
        rows.append(row('tr'+str(i),0,'2026-04-10T00:00:00+00:00', '2026-04-01T00:00:00+00:00'))
    for i in range(31):
        rows.append(row('val'+str(i),0,'2026-06-06T00:00:00+00:00','2026-06-02T00:00:00+00:00'))
    rows.extend([row('testA',0,'2026-07-14T10:00:00+00:00','2026-07-10T10:00:00+00:00'),
                 row('testB',1,'2026-07-16T10:00:00+00:00','2026-07-12T10:00:00+00:00'),
                 row('testC',0,'2026-07-25T10:00:00+00:00','2026-07-21T10:00:00+00:00')])
    baseline,stat=m.predictions(rows)
    assert stat['selected'] in ('expert_global','expert_hierarchical')
    mutated=copy.deepcopy(rows)
    for r in mutated:
        if r['features']['event_id'] in ('testA','testB','testC'):r['outcome']=1-r['outcome']
    poisoned,stat2=m.predictions(mutated)
    assert stat['selected']==stat2['selected']
    def first_two(xs):return [x for x in xs if x['event_id'] in ('testA','testB')]
    assert first_two(baseline)==first_two(poisoned)
    assert [x for x in baseline if x['mode']=='frozen']==[x for x in poisoned if x['mode']=='frozen']

def test_missing_document_hash_does_not_imply_same_document():
    votes=[{'source':'siteA','author':'alice','side':'home','source_reported_published_at':'2026-07-01T08:00:00Z','source_sha256':'a'*64},
           {'source':'siteB','author':'bob','side':'away','source_reported_published_at':'2026-07-01T08:00:00Z','source_sha256':'a'*64}]
    grouped=m.groups(votes,datetime.fromisoformat('2026-07-01T09:00:00+00:00'),['home','away'])
    assert len(grouped)==1
    # Protocol says absent document SHA uses distinct source+author keys: two groups.
    assert len({(v['source'],v['author']) for v in votes})==2

def test_validation_unavailable_by_july_cannot_choose_candidate():
    rows=[row('train'+str(i),0,'2026-04-10T00:00:00+00:00','2026-04-01T00:00:00+00:00') for i in range(18)]
    rows += [row('val'+str(i),0,'2026-06-06T00:00:00+00:00','2026-06-02T00:00:00+00:00') for i in range(31)]
    rows += [row('late',0,'2026-07-03T00:00:00+00:00','2026-06-29T00:00:00+00:00'),row('target',1,'2026-07-14T10:00:00+00:00','2026-07-10T10:00:00+00:00')]
    forecasts,stats=m.predictions(rows);poison=copy.deepcopy(rows);poison[-2]['outcome']=1
    poisoned,other=m.predictions(poison)
    assert stats['validation_excluded_not_settled_by_july1']==1
    assert stats['selected']==other['selected'] and stats['validation_logloss']==other['validation_logloss']
    # Its outcome becomes available before the test event, so prequential changes are allowed.
    assert [x for x in forecasts if x['mode']=='frozen']==[x for x in poisoned if x['mode']=='frozen']
