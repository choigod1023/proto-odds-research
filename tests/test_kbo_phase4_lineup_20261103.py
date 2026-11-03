import copy,importlib.util,inspect,sys
from datetime import date
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'experiments'/'kbo-player-model-20261103';spec=importlib.util.spec_from_file_location('kbo_phase4_test',P/'phase4_predict.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

def test_training_operations_are_forbidden():
    with pytest.raises(AssertionError,match='forbidden'):m.forbid_training()
    src=inspect.getsource(m.build)
    assert 'model.fit=forbid_training' in src and 'model.minimize=forbid_training' in src
    assert '.fit(' not in src and '.minimize(' not in src

def test_source_tier_does_not_claim_pregame():
    assert m.SCENARIO=='retrospective_official_batting_order_assumed_available'
    assert m.DATA_SCENARIO=='retrospective_official_confirmed_lineup_assumed_available'
    assert m.HASH['overlay']=='ddc66e073ada98ef9cbf469fac64aed53e45ac3d4e44a1e4330f2c661eced627'

def test_prior_history_poisoning_rejected():
    known={'target':date(2026,4,2),'old':date(2026,4,1),'same':date(2026,4,2),'future':date(2026,4,3)}
    target=known['target']
    m.adapter.check_nested({'fixture_id':'old','date':'2026-04-01'},'target',target,known)
    for poison in ({'fixture_id':'target'},{'fixture_id':'same'},{'fixture_id':'future'},{'date':'2026-04-02'}):
        with pytest.raises(ValueError):m.adapter.check_nested(poison,'target',target,known)

def test_counterfactual_scalar_uses_existing_formula_and_keeps_missing():
    name='home_lineup_strength';assert m.adapter.scalar(name,[{'player_id':'x','history':None}]) is None
    assert m.adapter.scalar(name,[{'player_id':'x','history':[{'ab':4,'hit':2,'bb':1}]}])==pytest.approx(.6)

def test_canonical_non_lineup_difference_visible():
    a={'home_team_strength':{'value':1},'home_starter_strength':{'value':None}}
    b=copy.deepcopy(a);assert m.canonical(a)==m.canonical(b)
    b['home_team_strength']['value']=2;assert m.canonical(a)!=m.canonical(b)

spec2=importlib.util.spec_from_file_location('kbo_phase4_score_test',P/'phase4_score.py');score=importlib.util.module_from_spec(spec2);sys.modules[spec2.name]=score;spec2.loader.exec_module(score)

def test_score_refuses_outcomes_when_prediction_freeze_invalid(monkeypatch):
    seen=[]
    def reject(path,sha):
        seen.append(str(path))
        raise ValueError('frozen input hash mismatch')
    monkeypatch.setattr(score,'read',reject)
    with pytest.raises(ValueError,match='hash mismatch'):score.frozen_inputs()
    assert len(seen)==1 and 'phase4' in seen[0] and 'predictions.json' in seen[0]

def test_paired_threeway_score_with_synthetic_outcomes():
    from datetime import timedelta
    def f(h,d,a):return {'probabilities_3way':{'home':h,'draw':d,'away':a},'probabilities_2way':{'home':h/(h+a),'away':a/(h+a)}}
    pred={'predictions':[]}; labels=[]
    for i in range(193):
        fid=str(i);pred['predictions'].append({'fixture_id':fid,'event_date':str(date(2026,1,1)+timedelta(days=i)),'original_player':f(.6,.1,.3),'counterfactual_player':f(.3,.1,.6),'frozen_team':f(.6,.1,.3)})
        labels.append({'fixture_id':fid,'home_score':0,'away_score':1})
    res,rows=score.all193(pred,labels)
    assert res['models']['original_player']['accuracy_3way']==0
    assert res['models']['counterfactual_player']['accuracy_3way']==1
    assert res['counterfactual_vs_original']['changed_2way_picks']==193
    assert res['counterfactual_vs_original']['accuracy_delta_ci95']==[1,1]
    assert len(rows)==193
