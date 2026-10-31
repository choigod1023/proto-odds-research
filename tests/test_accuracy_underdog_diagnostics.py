import importlib.util
from pathlib import Path
from decimal import Decimal
import pytest

P=Path(__file__).resolve().parents[1]/'experiments/underdog-diagnostics-20261031/run.py'
spec=importlib.util.spec_from_file_location('underdog_diagnostics',P); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def test_same_market_under_favorite_and_ties():
    assert m.classify([.41,.35,.24],0)=='favorite'
    assert m.classify([.41,.35,.24],1)=='underdog'
    assert m.classify([.41,.35,.24],2)=='underdog'
    assert m.classify([.4,.4,.2],0)=='top_tie'
    assert m.classify([.4,.4,.2],2)=='underdog'
    with pytest.raises(ValueError): m.classify([.5,float('nan')],0)
    with pytest.raises(ValueError): m.classify([.5,.2],0)

def test_decimal_profit_and_actual_ordered_run():
    assert m.price_profit([1.83,1.91],1,1)==Decimal('.91')
    assert m.price_profit([1.83,1.91],1,0)==Decimal('-1')
    rows=[{'hit':0,'market_p':.4,'predicted_p':None,'odds':2,'profit':'-1','decision_at':'2026-01-03T00:00:00+00:00','event_id':'3'},
          {'hit':1,'market_p':.4,'predicted_p':None,'odds':2,'profit':'1','decision_at':'2026-01-01T00:00:00+00:00','event_id':'1'},
          {'hit':0,'market_p':.4,'predicted_p':None,'odds':2,'profit':'-1','decision_at':'2026-01-02T00:00:00+00:00','event_id':'2'}]
    result=m.summary(rows); assert result['longest_loss_run']==2
    assert result['run_start'].startswith('2026-01-02') and result['run_independent_market_failure_product']==pytest.approx(.36)

def test_comparison_is_paired_and_correct():
    a={'decision_at':'2026-01-01T00:00:00+00:00','hit':1,'profit':'1.1'}
    b={'decision_at':a['decision_at'],'hit':0,'profit':'-1'}
    a2={'decision_at':'2026-01-08T00:00:00+00:00','hit':1,'profit':'1.1'}
    b2={'decision_at':a2['decision_at'],'hit':0,'profit':'-1'}
    ci=m.paired_interval([(a,b),(a2,b2)]); assert ci['accuracy_delta_95']==[1,1] and ci['roi_delta_95']==[2.1,2.1]

def test_replay_preserves_samples_and_original_settlements():
    d=m.collect(); assert len(d['expert_122'])==610 and len(d['state_445'])==2207
    assert sum(r['hit'] for r in d['expert_122'] if r['model']=='expert_frozen')==63
    assert sum(r['hit'] for r in d['expert_122'] if r['model']=='market')==67
    assert len([r for r in d['state_445'] if r['model']=='state_frozen'])==436
    assert all(r['league']!='K리그2' for r in d['state_445'] if r['model'].startswith('state_'))
    assert all(r['predicted_p'] is None for rows in d.values() for r in rows if r['model']=='vote')
