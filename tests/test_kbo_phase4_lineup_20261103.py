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
