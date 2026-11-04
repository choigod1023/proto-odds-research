from copy import deepcopy
from datetime import date, timedelta
import json

import numpy as np
import pytest

from research.m0_improvement import experiment as e


def row():
    r={'fixture_id':'target','event_date':'2026-04-01','season':2026,'home_id':'A','away_id':'B',
       'features':{k:{'value':None} for k in e.FEATURES},'roster':{},'raw_features':{}}
    for side in ('home','away'):
        players=[side+str(i) for i in range(9)]
        r['roster'][side]={'hitters':players,'starter':side+'starter'}
        r['features'][side+'_rest_days']['value']=180
        r['raw_features'][side+'_lineup_strength']={'value':[{'player_id':p,'history':[{'fixture_id':'earlier','date':'2026-03-25','ab':1,'hit':1,'bb':0}]} for p in players]}
        r['raw_features'][side+'_starter_strength']={'value':{'projected_player_id':side+'starter','appearances':[{'fixture_id':'earlier','date':'2026-03-25','er':0,'outs':3}]}}
    return r


def test_transform_is_pure_and_rest_missing_does_not_become_observed_zero():
    r=row();before=deepcopy(r);r['features']['away_rest_days']['value']=None
    before=deepcopy(r);out=e.transform(r,'rest_cap')
    assert r==before and out['features']['home_rest_days']['value']==7
    assert out['features']['away_rest_days']['value'] is None
    assert e.transform(r,'baseline')==r


def test_sparse_histories_are_regularized_with_fixed_pseudocounts():
    out=e.transform(row(),'small_sample_shrinkage')
    assert out['features']['home_lineup_strength']['value']==pytest.approx(34/101)
    assert out['features']['home_starter_strength']['value']==pytest.approx(243/57)
    assert out['features']['home_rest_days']['value']==180
    assert e.transform(row(),'combined')['features']['home_rest_days']['value']==7


@pytest.mark.parametrize('defect',['future','target','owner','bad_hit','negative_outs','duplicate'])
def test_reject_unusable_history_and_identity(defect):
    r=row();h=r['raw_features']['home_lineup_strength']['value'][0]['history'][0]
    if defect=='future':h['date']=r['event_date']
    elif defect=='target':h['fixture_id']=r['fixture_id']
    elif defect=='owner':r['raw_features']['home_starter_strength']['value']['projected_player_id']='wrong'
    elif defect=='bad_hit':h['hit']=3
    elif defect=='negative_outs':r['raw_features']['home_starter_strength']['value']['appearances'][0]['outs']=-1
    else:r['raw_features']['home_lineup_strength']['value'][0]['history'].append(deepcopy(h))
    with pytest.raises(ValueError):e.transform(r,'small_sample_shrinkage')


def test_candidate_selection_cannot_use_test_roi():
    scores={v:{'accuracy':.6,'log_loss_3way':1.,'roi':1000} for v in e.VARIANTS}
    scores['rest_cap'].update(accuracy=.59,log_loss_3way=.7)
    scores['small_sample_shrinkage'].update(accuracy=.61,log_loss_3way=.9,roi=-1000)
    assert e.select_candidate(scores)=='small_sample_shrinkage'
    scores['small_sample_shrinkage']['log_loss_3way']=1.1
    assert e.select_candidate(scores)=='baseline'


def test_settlement_counts_draw_as_zero_profit_not_hit_and_rejects_bad_price():
    predictions=[{'fixture_id':'a','probabilities_3way':{'home':.5,'draw':.1,'away':.4},'home_probability':5/9},
                 {'fixture_id':'b','probabilities_3way':{'home':.5,'draw':.1,'away':.4},'home_probability':5/9}]
    labels={'a':{'home_score':2,'away_score':1,'status':'final'},'b':{'home_score':2,'away_score':2,'status':'final'}}
    result=e.metrics(predictions,labels,{'a':{'home':1.2,'away':2.},'b':{'home':1.8,'away':2.}})
    assert result['hits']==1 and result['decided']==1 and result['refunds']==1
    assert result['net_units']=='0.2' and result['roi']==.1
    with pytest.raises(ValueError):e.metrics(predictions,labels,{'a':{'home':float('nan'),'away':2.}})
    with pytest.raises(ValueError):e.metrics(predictions,labels,{'a':{'home':1.2,'away':float('nan')}})


def test_real_fit_respects_gap_and_probabilities_are_normalized():
    rows=[];labels={}
    for i in range(25):
        r=row();r.update(fixture_id=str(i),event_date=(date(2024,4,1)+timedelta(days=i)).isoformat(),season=2024)
        for side in ('home','away'):
            for person in r['raw_features'][side+'_lineup_strength']['value']: person['history'][0]['date']='2024-03-01'
            r['raw_features'][side+'_starter_strength']['value']['appearances'][0]['date']='2024-03-01'
        rows.append(r);labels[str(i)]={'home_score':i%6,'away_score':i%5,'status':'final'}
    with pytest.raises(ValueError):e.fit(rows,labels,'baseline','2024-04-27')
    model=e.fit(rows,labels,'baseline','2025-04-01')
    with pytest.raises(ValueError,match='overlaps training'):e.predict(model,rows)
    targets=deepcopy(rows)
    for r in targets:r.update(event_date='2025-04-01',season=2025)
    pred=e.predict(model,targets)
    assert model['training_n']==25 and model['promoted'] is False
    assert all(sum(p['probabilities_3way'].values())==pytest.approx(1) for p in pred)
    assert np.isfinite(model['beta']).all()


def test_gzip_duplicate_fixtures_rejected(tmp_path):
    import gzip
    p=tmp_path/'rows.gz';p.write_bytes(gzip.compress(b'{"fixture_id":"a"}\n{"fixture_id":"a"}\n'))
    with pytest.raises(ValueError):e.load_rows(p)


def test_freeze_checks_committed_bytes(tmp_path):
    # Paths outside this task cannot masquerade as its frozen development artifact.
    p=tmp_path/'dev.json';p.write_text(json.dumps({'test_labels_read':False}))
    with pytest.raises(ValueError):e.committed(p,'a'*40)


@pytest.mark.parametrize('variant',e.VARIANTS)
def test_all_candidates_reject_future_lineage_and_negative_rest(variant):
    r=row();r['features']['home_rest_days']['value']=-1
    with pytest.raises(ValueError,match='negative rest'):e.transform(r,variant)
    r=row();r['raw_features']['home_lineup_strength']['value'][0]['history'][0]['date']=r['event_date']
    with pytest.raises(ValueError,match='future'):e.transform(r,variant)
