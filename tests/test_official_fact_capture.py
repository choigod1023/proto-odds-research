import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from official_fact_capture import normalize_kbo_schedule, normalize_mlb_feed, semantic_hash

def game():
    return {'LE_ID':1,'SR_ID':5,'SEASON_ID':2026,'G_DT':'20261101','G_ID':'20261101HTSS0','G_TM':'14:00','AWAY_ID':'HT','HOME_ID':'SS','GAME_STATE_SC':'1','CANCEL_SC_ID':'0','START_PIT_CK':1,'T_PIT_P_ID':55633,'T_PIT_P_NM':'away','B_PIT_P_ID':53375,'B_PIT_P_NM':'home'}
def body(g=None): return json.dumps({'code':100,'game':[g or game()]}).encode()
RECEIPT='2026-10-31T01:00:00+00:00'

def test_kbo_source_identity_times_unknown_and_probable():
    raw=body(); out=normalize_kbo_schedule(raw,expected_date='20261101',received_at=RECEIPT)
    assert len(out['games'])==1 and len(out['facts'])==2
    for f in out['facts']:
        assert f['official']['raw_sha256']==hashlib.sha256(raw).hexdigest()
        assert f['official']['confirmed'] is False and f['after_value']['announcement_status']=='probable'
        assert f['canonical_game_id'] is f['before_value'] is f['decision_at'] is None
        assert f['market']['odds'] is None and not f['official']['external_receipt_verified']
        assert f['official']['received_at']==RECEIPT
        assert f['semantic_sha256']==semantic_hash(f)
        assert f['fact_id']=='official-fact-v1:'+semantic_hash(f)

def test_receipt_and_result_poisoning_do_not_generate_new_fact():
    g=game(); first=normalize_kbo_schedule(body(g),expected_date='20261101',received_at=RECEIPT)['facts']
    g.update(W_PIT_P_ID=123,L_PIT_P_ID=234,HOME_SCORE=999,AWAY_SCORE=777)
    later=normalize_kbo_schedule(body(g),expected_date='20261101',received_at='2026-10-31T02:00:00+00:00')['facts']
    assert [f['fact_id'] for f in first]==[f['fact_id'] for f in later]
    assert first[0]['official']['raw_sha256']!=later[0]['official']['raw_sha256']
    g['B_PIT_P_ID']=1001
    assert normalize_kbo_schedule(body(g),expected_date='20261101',received_at=RECEIPT)['facts'][0]['fact_id'] != first[0]['fact_id']

def test_reject_mismatched_or_stale_and_do_not_invent():
    g=game();g['HOME_ID']='LG'
    with pytest.raises(ValueError,match='game_id_team_mismatch'): normalize_kbo_schedule(body(g),expected_date='20261101',received_at=RECEIPT)
    g=game();g['T_PIT_P_ID']=g['B_PIT_P_ID']
    with pytest.raises(ValueError,match='same_player'): normalize_kbo_schedule(body(g),expected_date='20261101',received_at=RECEIPT)
    g=game();g['START_PIT_CK']=0
    r=normalize_kbo_schedule(body(g),expected_date='20261101',received_at=RECEIPT);assert not r['facts'] and len(r['missing'])==2
    r=normalize_kbo_schedule(body(),expected_date='20261101',received_at='2026-11-01T04:30:00+00:00');assert not r['games'] and not r['facts']
    with pytest.raises(ValueError,match='timezone_required'): normalize_kbo_schedule(body(),expected_date='20261101',received_at='2026-10-31T01:00:00')

def mlb_feed():
    boxes={}
    for side,base,team in [('home',10,100),('away',30,200)]:
        boxes[side]={'team':{'id':team},'battingOrder':list(range(base,base+9)), 'players':{'ID'+str(i):{'person':{'id':i,'fullName':str(i)}} for i in range(base,base+10)}}
    return {'gamePk':1,'metaData':{'timeStamp':'20261029_125959'},'gameData':{'datetime':{'dateTime':'2026-10-29T15:00:00Z'},'status':{'codedGameState':'S'},'teams':{'home':{'id':100},'away':{'id':200}},'probablePitchers':{'home':{'id':19},'away':{'id':39}}},'liveData':{'plays':{'allPlays':[]},'boxscore':{'teams':boxes}}}

def test_mlb_existing_guard_probable_vs_lineup_and_game_team():
    feed=mlb_feed(); raw=json.dumps(feed).encode(); params={'game_pk':1,'scheduled_at':'2026-10-29T15:00:00Z','received_at':'2026-10-29T13:00:00Z'}
    facts=normalize_mlb_feed(raw,**params);assert len(facts)==4
    assert all(f['official']['confirmed'] is False for f in facts if f['kind']=='probable_starting_pitcher')
    assert all(f['official']['confirmed'] is True and len(f['player_ids'])==9 for f in facts if f['kind']=='announced_batting_order')
    feed['liveData']['boxscore']['teams']['home']['team']['id']=200
    with pytest.raises(ValueError,match='team_identity'): normalize_mlb_feed(json.dumps(feed).encode(),**params)
    feed=mlb_feed(); feed['gameData']['status']['codedGameState']='I'
    with pytest.raises(ValueError): normalize_mlb_feed(json.dumps(feed).encode(),**params)

def test_actual_bundle_matches_contract_and_no_results():
    bundle=json.loads((ROOT/'experiments/verified-fact-capture-20261031/capture.json').read_text(encoding='utf8'))
    assert len(bundle['games'])==1 and len(bundle['facts'])==2 and bundle['request_attempts']==2
    assert bundle['missing'][0]['reason']=='not_announced'
    for fact in bundle['facts']:
        assert fact['semantic_sha256']==semantic_hash(fact)
        assert fact['fact_id']=='official-fact-v1:'+semantic_hash(fact)
        assert fact['official_game_id']==bundle['games'][0]['official_game_id']
        assert fact['official']['raw_sha256']==bundle['requests'][0]['raw_sha256']
        assert fact['official']['confirmed'] is False
        assert 'score' not in fact['after_value']

def test_pipeline_injection_preserves_missing_and_rejects_fake_ready(tmp_path):
    spec=importlib.util.spec_from_file_location('capture_script',ROOT/'scripts/capture_official_research_inputs.py')
    script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    bundle=json.loads((ROOT/'experiments/verified-fact-capture-20261031/capture.json').read_text(encoding='utf8'))
    def adapter(facts):
        assert len(facts)==2
        return [{'schema_version':'fact-delta-inputs-v1','league_id':'kbo','sport':'baseball','game_id':None,'market_id':None,'input_id':'missing','research_eligible':False,'status':'baseline_only','features':{'pitcher_status':'probable'},'source_evidence':[{'raw_sha256':facts[0]['official']['raw_sha256']}],'reasons':['missing_confirmed_fact_and_market']}]
    def fake_dispatch(rows): return {'test_only':True,'count':len(rows)}
    raw=tmp_path/'raw.json'; inputs=tmp_path/'research-inputs.json'; manifest=tmp_path/'manifest.json'
    result=script.run_capture_pipeline(bundle,adapter=adapter,build_manifest=fake_dispatch,raw_path=raw,inputs_path=inputs,manifest_path=manifest)
    assert result['inputs']==1 and json.loads(manifest.read_text(encoding='utf8'))['test_only']
    assert json.loads(inputs.read_text(encoding='utf8'))[0]['research_eligible'] is False
    def fake_ready(facts):
        r=adapter(facts);r[0]['status']='ready';r[0]['research_eligible']=True;return r
    with pytest.raises(ValueError,match='unsupported_ready_input'):
        script.run_capture_pipeline(bundle,adapter=fake_ready,build_manifest=fake_dispatch,raw_path=tmp_path/'raw2.json',inputs_path=tmp_path/'i2.json',manifest_path=tmp_path/'m2.json')
    assert (tmp_path/'raw2.json').exists() and not (tmp_path/'i2.json').exists()
    def leaks(facts):
        r=adapter(facts);r[0]['features']['score']=5;return r
    with pytest.raises(ValueError,match='target_result_in_features'):
        script.run_capture_pipeline(bundle,adapter=leaks,build_manifest=fake_dispatch,raw_path=tmp_path/'raw3.json',inputs_path=tmp_path/'i3.json',manifest_path=tmp_path/'m3.json')

def test_pipeline_ready_requires_confirmed_timed_fact_and_same_market(tmp_path):
    spec=importlib.util.spec_from_file_location('capture_ready',ROOT/'scripts/capture_official_research_inputs.py');script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    bundle=json.loads((ROOT/'experiments/verified-fact-capture-20261031/capture.json').read_text(encoding='utf8'))
    fact=bundle['facts'][0];fact['canonical_game_id']='canonical';fact['decision_at']='2026-10-31T02:00:00+00:00'
    fact['official'].update(confirmed=True,external_receipt_verified=True)
    fact['kind']='confirmed_starting_pitcher';fact['after_value']['announcement_status']='confirmed';fact['semantic_sha256']=semantic_hash(fact)
    fact['claim'].update(url='https://example.org/claim',published_at='2026-10-31T00:00:00+00:00',received_at='2026-10-31T01:00:00+00:00',raw_sha256='a'*64)
    fact['market'].update(product_id='market1',market='moneyline',period='full',outcomes=['home','away'],odds=[1.9,2.0],observed_at='2026-10-31T01:00:00+00:00',received_at='2026-10-31T01:01:00+00:00')
    def adapter(facts): return [{'schema_version':'fact-delta-inputs-v1','league_id':'kbo','sport':'baseball','game_id':'canonical','market_id':'market1','input_id':'a','research_eligible':True,'status':'ready','features':{'pitcher':facts[0]['player_ids'][0]},'source_evidence':[facts[0]['official']],'reasons':[]}]
    result=script.run_capture_pipeline(bundle,adapter=adapter,build_manifest=lambda rows:{'accepted':len(rows)},raw_path=tmp_path/'raw',inputs_path=tmp_path/'inputs',manifest_path=tmp_path/'manifest')
    assert result['inputs']==1
    fact['official']['url']='https://example.org/not-official'
    with pytest.raises(ValueError,match='unsupported_ready_input'):
        script.run_capture_pipeline(bundle,adapter=adapter,build_manifest=lambda rows:{},raw_path=tmp_path/'raw2',inputs_path=tmp_path/'inputs2',manifest_path=tmp_path/'manifest2')


def test_ready_guard_rejects_missing_claim_url_without_exception():
    spec=importlib.util.spec_from_file_location('capture_null_claim',ROOT/'scripts/capture_official_research_inputs.py');script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    bundle=json.loads((ROOT/'experiments/verified-fact-capture-20261031/capture.json').read_text(encoding='utf8')); f=bundle['facts'][0]
    f['canonical_game_id']='canonical';f['decision_at']='2026-10-31T02:00:00+00:00';f['kind']='confirmed_starting_pitcher';f['after_value']['announcement_status']='confirmed';f['semantic_sha256']=semantic_hash(f);f['official'].update(confirmed=True,external_receipt_verified=True);f['market']['product_id']='market'
    assert script.eligible_fact(f,{'game_id':'canonical','market_id':'market','league_id':'kbo','sport':'baseball','source_evidence':[f['official']]}) is False
