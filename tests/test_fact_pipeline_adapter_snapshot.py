import copy,importlib.util,sys
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'experiments/fact-pipeline-independent-review-20261031/vendor/fact_delta_snapshot.py'
s=importlib.util.spec_from_file_location('fact_independent_snapshot',P);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)

def sample():
    identity={'canonical_game_id':'c1','official_game_id':'g1','league':'KBO','sport':'baseball','kickoff_at':'2026-11-01T10:00:00+00:00','home_team_id':'h','away_team_id':'a','player_teams':{'p1':'h','p2':'h'},'product':{'product_id':'p','market':'win','period':'full','line':None,'outcomes':['home','away']}}
    ev={'url':'https://example.org/doc','published_at':'2026-11-01T08:00:00+00:00','received_at':'2026-11-01T08:30:00+00:00','raw_sha256':'a'*64}
    official=dict(ev,identity_checked=True,confirmed=True)
    market={**identity['product'],'odds':{'home':1.8,'away':2.2},'observed_at':'2026-11-01T09:00:00+00:00','received_at':'2026-11-01T09:00:00+00:00'}
    fact={'fact_id':'f1','canonical_game_id':'c1','official_game_id':'g1','league':'KBO','sport':'baseball','kind':'confirmed_starter_change','before_value':{'player_id':'p1','status':'probable'},'after_value':{'player_id':'p2','status':'confirmed'},'units':'player','player_ids':['p1','p2'],'claim':ev,'official':official,'decision_at':'2026-11-01T09:30:00+00:00','kickoff_at':identity['kickoff_at'],'market':market,'revision':1}
    baseline={'canonical_game_id':'c1','product_id':'p','decision_at':fact['decision_at'],'outcomes':['home','away'],'probability_vector':[.6,.4],'input_content_sha256':'b'*64,'freeze_hash':'c'*64}
    return fact,identity,baseline

def test_empty_fact_list_preserves_baseline_without_certifying():
    fact,reg,base=sample();out=m.build_research_envelope([],reg,base,fact['market'])
    assert out['probability_vector']==base['probability_vector'] and out['prediction_changed'] is False
    assert out['external_receipt_verified'] is False
    valid=m.build_research_envelope([fact],reg,base,fact['market'])
    assert valid['features'][0]['externally_verified'] is False

def test_invalid_player_fact_raises_instead_of_returning_baseline():
    f,r,b=sample();f['player_ids']=[]
    with pytest.raises(m.InvalidFact):m.build_research_envelope([f],r,b,f['market'])
    assert m.build_research_envelope([],r,b,f['market'])['probability_vector']==b['probability_vector']

def test_identical_before_after_still_accepted_as_change():
    f,r,b=sample();f['before_value']=copy.deepcopy(f['after_value'])
    out=m.build_research_envelope([f],r,b,f['market'])
    assert out['features'][0]['before_value']==out['features'][0]['after_value']
    assert out['features'][0]['kind']=='confirmed_starter_change'

def test_reject_late_wrong_market_and_current_score():
    f,r,b=sample()
    for mutate in (lambda q:q['official'].update(received_at='2026-11-01T09:31:00+00:00'),lambda q:q['market'].update(product_id='wrong'),lambda q:q.update(target_score=[1,0])):
        x=copy.deepcopy(f);mutate(x)
        with pytest.raises(m.InvalidFact):m.build_research_envelope([x],r,b,f['market'])

