import copy
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import pytest

DIR = Path(__file__).resolve().parents[1] / 'experiments' / 'accuracy-development-20261030'
pkg_spec = importlib.util.spec_from_file_location('accuracy_development', DIR / '__init__.py', submodule_search_locations=[str(DIR)])
pkg = importlib.util.module_from_spec(pkg_spec); sys.modules[pkg_spec.name] = pkg; pkg_spec.loader.exec_module(pkg)
from accuracy_development.adapter import infer, _canonical
from accuracy_development.baseline_model import State
from accuracy_development.bridge import infer_v1
spec = importlib.util.spec_from_file_location('structure_validator_fixture', DIR / 'vendor_structure_v1' / 'validator.py')
validator = importlib.util.module_from_spec(spec); spec.loader.exec_module(validator)


def fixture():
    doc = json.loads((DIR / 'vendor_structure_v1' / 'fixture-pregame.json').read_text(encoding='utf-8'))
    state = State('KBO', 'outcome', 0)
    state.update({'date':'2026-10-01', 'league':'KBO', 'home':'Home Name', 'away':'Away Name', 'hg':3, 'ag':1}, datetime(2026,10,5,tzinfo=timezone.utc))
    g=doc['games'][0]; q=doc['quotes'][0]; p=doc['products'][0]; e=doc['observations'][0]['evidence']
    ident={'league':g['league'],'canonical_game_id':'g1','official_game_id':'game-1','product_id':'p1','home_team_id':'h','away_team_id':'a','kickoff_at':g['kickoff_at']}
    product=dict(q['identity']); product['outcomes']=p['outcomes']
    quote={'product_id':'p1','observed_at':q['quote_observed_at'],'first_received_at':e['first_received_at'],'revision_received_at':e['revision_received_at'],'availability_tier':e['availability_tier'],'odds':q['odds'],'source_url':e['source_url'],'source_sha256':e['source_sha256'],'published_at_claim':e['published_at_claim'],'external_receipt_verified':e['external_receipt_verified']}
    contract={'identity':ident,'product':product,'team_mapping':{'h':'Home Name','a':'Away Name'},'state_sha256':hashlib.sha256(_canonical(state.export()).encode()).hexdigest(),'state_available_through':'2026-10-05T00:00:00+00:00','quote':quote}
    # A synthetic pinned contract models the verifier interface; it is not real
    # authentication or evidence of external receipt or training provenance.
    trusted=copy.deepcopy(contract); trusted_state=copy.deepcopy(state.export())
    def verify(candidate, snapshot): return candidate == trusted and snapshot == trusted_state
    return state, doc, contract, verify


def validate_core(doc):
    # Reuse the unmodified v1 schema checker only on core sections. The full
    # validator requires a freeze and is not a pre-inference validator.
    for key in ('schema_version', 'games', 'aliases', 'products', 'observations', 'quotes'):
        validator.shape(doc[key], validator.SCHEMA['properties'][key], '$.' + key)
    return True


def run(state,doc,contract,verify, refs=None, decision='2026-10-30T09:30:00+00:00'):
    return infer_v1(state,doc,'g1','q1',refs or [],decision,contract,validate_core,verify)


def test_v1_partial_lineup_falls_back_and_freeze_is_not_authenticated():
    state,doc,c,verify=fixture(); before=state.export()
    assert validator.validate(doc) is True
    a=run(state,doc,c,verify); b=run(state,doc,c,verify,['l1'])
    assert a['availability_reason']['code']=='lineup_unavailable'
    assert b['availability_reason']['code']=='lineup_invalid'
    assert a['probability_vector']==b['probability_vector'] and b['input_revision'] is None
    assert state.export()==before
    doc['freezes'][0]['freeze_hash']='0'*64
    assert run(state,doc,c,verify)['probability_vector']==a['probability_vector']
    restored=State.restore(json.loads(json.dumps(a))['baseline_state'])
    assert run(restored,doc,c,verify)['probability_vector']==a['probability_vector']


def test_complete_lineup_preserves_probabilities():
    state,doc,c,verify=fixture()
    doc['lineups']=[{'revision_id':team,'canonical_game_id':'g1','team_id':team,'observation_id':'o1','players':[{'player_id':team+role,'role':role,'order':None,'announcement_status':status,'expected_minutes_or_innings':n} for role,status,n in [('starter','probable',6),('bullpen','confirmed',3)]]} for team in ('h','a')]
    p=run(state,doc,c,verify,['h','a']); base=run(state,doc,c,verify)
    assert p['availability_reason']['code']=='player_effects_not_fitted'
    assert p['probability_vector']==base['probability_vector']
    p['input_revision']['players'][0]['expected_minutes_or_innings']=99
    assert doc['lineups'][0]['players'][0]['expected_minutes_or_innings']==6


@pytest.mark.parametrize('change',[
 lambda c: c['product'].update(market='handicap',line=-1.5),
 lambda c: c['product'].update(period='first-half'),
 lambda c: c['product'].update(outcomes=['away','home']),
 lambda c: c['identity'].update(official_game_id='other'),
 lambda c: c['team_mapping'].update(h='unknown'),
 lambda c: c['team_mapping'].update(h='Away Name'),
 lambda c: c.update(state_available_through='2026-10-31T00:00:00+00:00'),
 lambda c: c.update(state_sha256='0'*64),
 lambda c: c['quote'].update(availability_tier='retrospective'),
 lambda c: c['quote'].update(revision_received_at='2026-10-30T09:31:00+00:00'),
 lambda c: c['quote'].update(odds={'home':1.8}),
])
def test_bad_core_fails_without_baseline(change):
    state,doc,c,verify=fixture(); change(c)
    req={'identity':c['identity'],'decision_at':'2026-10-30T09:30:00+00:00','lineup':None}
    with pytest.raises(ValueError): infer(state,req,c,verify)


def test_verifier_mandatory_and_late_state():
    s,d,c,v=fixture()
    with pytest.raises(ValueError): run(s,d,c,lambda *_: False)
    with pytest.raises(ValueError): run(s,d,c,v,decision='2026-10-30T09:31:00+00:00')
    s.clock=datetime(2026,10,31,tzinfo=timezone.utc)
    c['state_sha256']=hashlib.sha256(_canonical(s.export()).encode()).hexdigest()
    with pytest.raises(ValueError): run(s,d,c,lambda *_: True)


def test_mismatched_v1_quote_and_invalid_lineup_suppression():
    s,d,c,v=fixture(); d['quotes'][0]['odds']['home']=9.9
    with pytest.raises(ValueError): run(s,d,c,v)
    s,d,c,v=fixture(); d['lineups'][0]['players'][0]['current_score']=10
    result=run(s,d,c,v,['l1'])
    assert result['availability_reason']['code']=='lineup_invalid'
    assert result['input_revision'] is None


def test_soccer_threeway_and_mapping():
    s=State('K리그1','outcome',0)
    s.update({'date':'2026-10-01','league':'K리그1','home':'H','away':'A','hg':1,'ag':0},datetime(2026,10,5,tzinfo=timezone.utc))
    _,d,c,_=fixture(); i=c['identity']; i.update(league='K리그1'); c['team_mapping']={'h':'H','a':'A'}
    c['product']['outcomes']=['home','draw','away']; c['quote']['odds']={'home':2.,'draw':3.,'away':4.}
    c['state_sha256']=hashlib.sha256(_canonical(s.export()).encode()).hexdigest()
    assert len(infer(s,{'identity':i,'decision_at':'2026-10-30T09:30:00+00:00','lineup':None},c,lambda *_:True)['probability_vector'])==3


@pytest.mark.parametrize('mutate', [
    lambda d: d['lineups'][0]['players'][0].update(expected_minutes_or_innings=4),
    lambda d: d['lineups'][0]['players'][0].update(announcement_status='unknown'),
    lambda d: d['lineups'][0].update(canonical_game_id='other'),
    lambda d: d['lineups'][0]['players'][0].update(team_id='wrong'),
])
def test_bad_lineups_are_discarded(mutate):
    s,d,c,v=fixture(); mutate(d)
    result=run(s,d,c,v,['l1'])
    assert result['availability_reason']['code']=='lineup_invalid'
    assert result['input_revision'] is None
    assert result['probability_vector']==run(s,d,c,v)['probability_vector']
