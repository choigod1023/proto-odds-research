import copy
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'experiments/accuracy-cross-review-20261030/vendor'
sys.path.insert(0, str(ROOT / 'development'))
from adapter import infer
from baseline_model import State
spec = importlib.util.spec_from_file_location('cross_structure', ROOT / 'structure/validator.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)

def load(path):
    return json.loads((ROOT / path).read_text(encoding='utf8'))

def request():
    return {'identity': {'league':'KBO','official_game_id':'game-1','product_id':'p1','home_team_id':'h','away_team_id':'a','kickoff_at':'2026-10-30T10:00:00+00:00'},'decision_at':'2026-10-30T09:30:00+00:00','lineup':None}

def lineup():
    return {'official_game_id':'game-1','source_published_at_claim':'2026-10-30T09:00:00+00:00','first_received_at':'2026-10-30T09:00:00+00:00','revision_received_at':'2026-10-30T09:00:00+00:00','source_url':'https://example.org/source','raw_sha256':'a'*64,'availability_tier':'observed','external_receipt_verified':False,'sport':'baseball','players':[{'team_id':team,'player_id':team+role,'role':role,'order':None,'announcement_status':'probable','expected_minutes_or_innings':amount} for team in ('h','a') for role,amount in (('starter',6),('bullpen',3))]}

def test_v0_cannot_be_directly_validated_as_v1():
    out=infer(State('KBO','outcome',0),request())
    with pytest.raises(v.Invalid): v.validate(out)
    doc=load('structure/fixture-pregame.json')
    with pytest.raises(ValueError): infer(State('KBO','outcome',0),doc)
    assert out['schema_version']=='provisional-v0' and doc['schema_version']=='v1'

def test_mapping_needs_trusted_quote_and_uses_distinct_hash():
    out=infer(State('KBO','outcome',0),request())
    doc=load('structure/fixture-pregame.json'); f=doc['freezes'][0]
    f.update(model_id=out['model_id'],model_version=out['model_version'],probabilities=dict(zip(('home','away'),out['probability_vector'])),selection='home',lineup_revision_ids=[])
    f['freeze_hash']=v.digest(f)
    assert v.validate(doc)
    assert out['freeze_hash']!=f['freeze_hash']
    no_quote=copy.deepcopy(doc); no_quote['quotes']=[]
    with pytest.raises(v.Invalid): v.validate(no_quote)

def test_hash_does_not_commit_to_referenced_quote_without_snapshot():
    before=load('structure/fixture-pregame.json'); after=copy.deepcopy(before)
    after['quotes'][0]['odds']['home']=9.9
    assert before['freezes'][0]['freeze_hash']==after['freezes'][0]['freeze_hash']
    assert v.validate(before) and v.validate(after)
    with pytest.raises(v.Invalid,match='history changed'): v.validate_append(before,after)
    changed=copy.deepcopy(before); changed['freezes'][0]['selection']='away'
    with pytest.raises(v.Invalid,match='freeze hash'): v.validate(changed)
    changed['freezes'][0]['freeze_hash']=v.digest(changed['freezes'][0])
    assert v.validate(changed)
    with pytest.raises(v.Invalid,match='history changed'): v.validate_append(before,changed)

def test_v1_partial_lineup_is_valid_but_v0_requires_two_teams_complete():
    doc=load('structure/fixture-pregame.json'); assert v.validate(doc)
    r=request(); r['lineup']=lineup(); r['lineup']['players']=r['lineup']['players'][:1]
    r['lineup']['players'][0]['expected_minutes_or_innings']=None
    with pytest.raises(ValueError): infer(State('KBO','outcome',0),r)

def test_bad_lineup_raises_but_absent_lineup_keeps_baseline():
    state=State('KBO','outcome',0); r=request(); before=state.export()
    baseline=infer(state,r); r['lineup']=lineup(); good=infer(state,r)
    assert good['probability_vector']==baseline['probability_vector']
    r['lineup']['revision_received_at']='2026-10-30T09:31:00+00:00'
    with pytest.raises(ValueError): infer(state,r)
    assert state.export()==before
    r['lineup']=None
    assert infer(state,r)['probability_vector']==baseline['probability_vector']

@pytest.mark.parametrize('mutate',[
    lambda r:r['lineup']['players'][0].update(player_id='abullpen'),
    lambda r:r['lineup']['players'][0].update(expected_minutes_or_innings=7),
    lambda r:r['lineup']['players'][0].update(expected_minutes_or_innings=None),
    lambda r:r['lineup'].update(official_game_id='other'),
    lambda r:r['lineup'].update(external_receipt_verified=True),
])
def test_player_guards(mutate):
    r=request();r['lineup']=lineup();mutate(r)
    with pytest.raises(ValueError): infer(State('KBO','outcome',0),r)

def test_product_and_availability_claim_are_not_interpreted_by_adapter():
    state=State('KBO','outcome',0); r=request(); baseline=infer(state,r)
    r['identity']['product_id']='first-half-handicap-minus-1.5'; r['lineup']=lineup();r['lineup']['availability_tier']='retrospective'
    out=infer(state,r)
    assert out['probability_vector']==baseline['probability_vector']
    assert out['identity']['product_id']=='first-half-handicap-minus-1.5'
    doc=load('structure/fixture-pregame.json');doc['observations'][0]['evidence']['availability_tier']='retrospective'
    with pytest.raises(v.Invalid,match='unavailable at decision'):v.validate(doc)

def test_adapter_decision_inside_t30_cannot_enter_v1():
    r=request();r['decision_at']='2026-10-30T09:45:00+00:00'; infer(State('KBO','outcome',0),r)
    doc=load('structure/fixture-pregame.json');doc['freezes'][0]['decision_at']=r['decision_at'];doc['freezes'][0]['freeze_hash']=v.digest(doc['freezes'][0])
    with pytest.raises(v.Invalid,match='not pregame'):v.validate(doc)

def test_result_linkage_is_not_pregame_identity_proof():
    rows=load('connection/official-2025.json'); links=load('connection/links.json')['links']
    assert len(rows)==275 and len(links)==23 and len({x['official_game_id'] for x in rows})==275
    assert len({x['official_game_id'] for x in rows}-{x['official_game_id'] for x in links})==252
    assert all(x['product_id'] is None and x['result_final_at'] is None and datetime.fromisoformat(x['first_received_at'])>datetime.fromisoformat(x['kickoff_at']) for x in rows)
    # Equal date and teams do not distinguish two fixtures; final score does so only after play.
    fixtures=[{'id':'one','score':[1,0]},{'id':'two','score':[0,1]}]
    linked=lambda score:[x['id'] for x in fixtures if x['score']==score]
    assert linked([1,0])==['one'] and linked([0,1])==['two']
    assert len(fixtures)==2  # before the result the identity is ambiguous

def test_unrecognized_player_and_team_ids_are_not_resolved_to_authority():
    state=State('KBO','outcome',0); r=request(); r['lineup']=lineup()
    r['lineup']['players'][0]['player_id']='invented-but-nonempty'
    assert infer(state,r)['input_revision']['players'][0]['player_id']=='invented-but-nonempty'
    state.ensure('h'); state.ensure('a'); state.mean[state.index['h']]=2.0
    known=infer(state,request())['probability_vector']
    different=request();different['identity']['home_team_id']='unknown-authority-id'
    unknown=infer(state,different)['probability_vector']
    assert known!=unknown  # unseen team silently receives the prior rather than resolved strength

def test_soccer_allocations_and_player_uniqueness():
    r=request(); r['identity']['league']='K리그2'; l=lineup();l['sport']='soccer'
    l['players']=[dict(team_id=t,player_id=f'{t}{i}',role='player',order=i,announcement_status='confirmed',expected_minutes_or_innings=90) for t in ('h','a') for i in range(1,12)]
    r['lineup']=l; state=State('K리그2','outcome',0)
    assert len(infer(state,r)['probability_vector'])==3
    l['players'][0]['expected_minutes_or_innings']=89
    with pytest.raises(ValueError,match='incomplete'): infer(state,r)
