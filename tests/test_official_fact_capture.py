import base64
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
    assert result['inputs']==1 and result['unmatched_fact_count']==2 and json.loads(manifest.read_text(encoding='utf8'))['test_only']
    assert json.loads(inputs.read_text(encoding='utf8'))[0]['research_eligible'] is False
    def fake_ready(facts):
        r=adapter(facts);r[0]['status']='ready';r[0]['research_eligible']=True;return r
    with pytest.raises(ValueError,match='unmapped_facts_in_ready_batch'):
        script.run_capture_pipeline(bundle,adapter=fake_ready,build_manifest=fake_dispatch,raw_path=tmp_path/'raw2.json',inputs_path=tmp_path/'i2.json',manifest_path=tmp_path/'m2.json')
    assert (tmp_path/'raw2.json').exists() and not (tmp_path/'i2.json').exists()
    def leaks(facts):
        r=adapter(facts);r[0]['features']['score']=5;return r
    with pytest.raises(ValueError,match='target_result_in_features'):
        script.run_capture_pipeline(bundle,adapter=leaks,build_manifest=fake_dispatch,raw_path=tmp_path/'raw3.json',inputs_path=tmp_path/'i3.json',manifest_path=tmp_path/'m3.json')

def test_real_adapter_output_ready_without_external_certification(tmp_path):
    spec=importlib.util.spec_from_file_location('capture_ready',ROOT/'scripts/capture_official_research_inputs.py');script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    fixture=json.loads((ROOT/'experiments/verified-fact-capture-20261031/synthetic-real-adapter-fixture.json').read_text(encoding='utf8'))
    bundle,context,actual_output=fixture['bundle'],fixture['context'],fixture['adapter_output']
    assert fixture['synthetic_only'] and actual_output['status']=='ready' and actual_output['research_eligible']
    assert all(e['external_receipt_verified'] is False for e in actual_output['source_evidence'])
    def recorded_adapter(facts,registry,baseline,market,*,input_id,workloads):
        assert 'external_receipt_verified' not in facts[0]['official']
        assert registry==context['registry'] and baseline==context['baseline'] and market==context['market']
        assert input_id==context['input_id'] and workloads==context['workloads']
        return copy.deepcopy(actual_output)
    adapter=lambda facts:script.adapt_with_context(facts,to_adapter_input=recorded_adapter,context=context)
    result=script.run_capture_pipeline(bundle,adapter=adapter,build_manifest=lambda rows:{'accepted':len(rows)},raw_path=tmp_path/'raw',inputs_path=tmp_path/'inputs',manifest_path=tmp_path/'manifest')
    assert result['inputs']==1 and json.loads((tmp_path/'inputs').read_text(encoding='utf8'))[0]['status']=='ready'
    altered=copy.deepcopy(bundle); altered['requests'][0]['raw_sha256']='0'*64
    with pytest.raises(ValueError,match='unsupported_ready_input'):
        script.run_capture_pipeline(altered,adapter=adapter,build_manifest=lambda rows:{},raw_path=tmp_path/'raw2',inputs_path=tmp_path/'inputs2',manifest_path=tmp_path/'manifest2')
    altered=copy.deepcopy(bundle); altered['facts'][0]['official']['confirmed']=False
    with pytest.raises(ValueError,match='unsupported_ready_input'):
        script.run_capture_pipeline(altered,adapter=adapter,build_manifest=lambda rows:{},raw_path=tmp_path/'raw3',inputs_path=tmp_path/'inputs3',manifest_path=tmp_path/'manifest3')
    altered=copy.deepcopy(bundle); altered['facts'][0]['official']['url']='https://example.org/not-official'
    with pytest.raises(ValueError,match='unsupported_ready_input'):
        script.run_capture_pipeline(altered,adapter=adapter,build_manifest=lambda rows:{},raw_path=tmp_path/'raw4',inputs_path=tmp_path/'inputs4',manifest_path=tmp_path/'manifest4')

def test_wrapper_requires_real_context_and_never_fills_capture_missing_values():
    spec=importlib.util.spec_from_file_location('capture_context',ROOT/'scripts/capture_official_research_inputs.py');script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    capture=json.loads((ROOT/'experiments/verified-fact-capture-20261031/capture.json').read_text(encoding='utf8'))
    fixture=json.loads((ROOT/'experiments/verified-fact-capture-20261031/synthetic-real-adapter-fixture.json').read_text(encoding='utf8'))
    with pytest.raises(ValueError,match='adapter_context_missing'):script.adapt_with_context(capture['facts'],to_adapter_input=lambda *a,**k:None,context={})
    def check(facts,registry,baseline,market,*,input_id,workloads):
        assert all(f['canonical_game_id'] is None and f['market']['odds'] is None and f['decision_at'] is None for f in facts)
        assert all(f['official']['confirmed'] is False and f['after_value']['announcement_status']=='probable' for f in facts)
        return {'status':'rejected'}
    assert script.adapt_with_context(capture['facts'],to_adapter_input=check,context=fixture['context'])==[{'status':'rejected'}]

def test_ready_guard_rejects_missing_claim_url_without_exception():
    spec=importlib.util.spec_from_file_location('capture_null_claim',ROOT/'scripts/capture_official_research_inputs.py');script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    fixture=json.loads((ROOT/'experiments/verified-fact-capture-20261031/synthetic-real-adapter-fixture.json').read_text(encoding='utf8')); bundle=fixture['bundle']; f=bundle['facts'][0];f['claim']['url']=None
    assert script.eligible_fact(f,fixture['adapter_output'],bundle) is False


def test_recorded_bytes_are_verified_and_reject_tampering():
    spec=importlib.util.spec_from_file_location('capture_bytes',ROOT/'scripts/capture_official_research_inputs.py');script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    saved=json.loads((ROOT/'experiments/verified-fact-capture-20261031/capture-with-bytes.json').read_text(encoding='utf8'))
    old=json.loads((ROOT/'experiments/verified-fact-capture-20261031/capture.json').read_text(encoding='utf8'))
    assert len(saved['requests'])==2 and all(script.valid_response_bytes(e) for e in saved['requests'])
    assert not any(script.valid_response_bytes(e) for e in old['requests'])
    assert {f['fact_id'] for f in saved['facts']}=={f['fact_id'] for f in old['facts']}
    for e in saved['requests']:
        raw=base64.b64decode(e['raw_base64'],validate=True)
        assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['raw_sha256']
        for defect in ('base64','hash','length','missing','oversize'):
            bad=copy.deepcopy(e)
            if defect=='base64': bad['raw_base64']='!invalid!'
            if defect=='hash': bad['raw_sha256']='0'*64
            if defect=='length': bad['bytes']+=1
            if defect=='missing': bad.pop('raw_base64')
            if defect=='oversize': bad['bytes']=script.MAX_BODY+1
            assert not script.valid_response_bytes(bad)

def test_ready_gate_refuses_hash_only_and_invalid_body():
    spec=importlib.util.spec_from_file_location('capture_ready_bytes',ROOT/'scripts/capture_official_research_inputs.py');script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    fixture=json.loads((ROOT/'experiments/verified-fact-capture-20261031/synthetic-real-adapter-fixture.json').read_text(encoding='utf8')); b=fixture['bundle'];f=b['facts'][0];r=fixture['adapter_output']
    assert script.eligible_fact(f,r,b)
    for defect in ('missing','changed_bytes','count','timestamp'):
        bad=copy.deepcopy(b)
        if defect=='missing': bad['requests'][0].pop('raw_base64')
        if defect=='changed_bytes': bad['requests'][0]['raw_base64']=base64.b64encode(b'altered').decode()
        if defect=='count': bad['requests'][0]['bytes']+=1
        if defect=='timestamp': bad['requests'][0]['received_at']='2026-10-31T09:00:01+00:00'
        assert not script.eligible_fact(f,r,bad)

def test_fetch_records_bytes_only_and_enforces_limits():
    spec=importlib.util.spec_from_file_location('capture_fetch_bytes',ROOT/'scripts/capture_official_research_inputs.py');script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    class Response:
        status_code=200
        def __init__(self,body,encoding='identity'): self.body=body;self.headers={'Content-Encoding':encoding};self.closed=False
        def iter_content(self,chunk_size): yield self.body
        def close(self): self.closed=True
    class Session:
        def __init__(self,r): self.response=r
        def post(self,url,**kwargs):
            assert kwargs['timeout']==15 and kwargs['allow_redirects'] is False and kwargs['stream'] is True
            return self.response
    data=b'public-official-response'; response=Response(data)
    raw,entry=script.fetch(Session(response),'https://official.invalid/test',{})
    assert raw==data and script.valid_response_bytes(entry) and response.closed
    assert set(entry)=={'url','status','received_at','raw_sha256','bytes','raw_base64'}
    response=Response(data,'gzip')
    with pytest.raises(ValueError,match='encoded_response_not_supported'): script.fetch(Session(response),'https://official.invalid/test',{})
    assert response.closed
    response=Response(b'x'*(script.MAX_BODY+1))
    with pytest.raises(ValueError,match='response_size_limit'): script.fetch(Session(response),'https://official.invalid/test',{})
    assert response.closed


def test_two_game_rows_check_only_their_own_facts_and_reject_unmapped(tmp_path):
    spec=importlib.util.spec_from_file_location('capture_two_games',ROOT/'scripts/capture_official_research_inputs.py');script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    fixture=json.loads((ROOT/'experiments/verified-fact-capture-20261031/synthetic-real-adapter-fixture.json').read_text(encoding='utf8'))
    bundle=fixture['bundle']; first=bundle['facts'][0];second=copy.deepcopy(first);second['canonical_game_id']='game-2';second['official_game_id']='official-2';second['market']['product_id']='product-2';bundle['facts'].append(second)
    rows=[fixture['adapter_output'],copy.deepcopy(fixture['adapter_output'])];rows[1]['game_id']='game-2';rows[1]['market_id']='product-2';rows[1]['input_id']='second'
    adapter=lambda facts:copy.deepcopy(rows)
    result=script.run_capture_pipeline(bundle,adapter=adapter,build_manifest=lambda values:{'count':len(values)},raw_path=tmp_path/'a',inputs_path=tmp_path/'b',manifest_path=tmp_path/'c')
    assert result['inputs']==2 and result['unmatched_fact_count']==0
    with pytest.raises(ValueError,match='unmapped_facts_in_ready_batch'):
        script.run_capture_pipeline(bundle,adapter=lambda facts:copy.deepcopy(rows[:1]),build_manifest=lambda values:{},raw_path=tmp_path/'a2',inputs_path=tmp_path/'b2',manifest_path=tmp_path/'c2')
    bad=copy.deepcopy(bundle);bad['facts'][1]['official']['confirmed']=False
    with pytest.raises(ValueError,match='unsupported_ready_input'):
        script.run_capture_pipeline(bad,adapter=adapter,build_manifest=lambda values:{},raw_path=tmp_path/'a3',inputs_path=tmp_path/'b3',manifest_path=tmp_path/'c3')
    with pytest.raises(ValueError,match='mixed_context_facts'):
        script.adapt_with_context(bundle['facts'],to_adapter_input=lambda *a,**k:None,context=fixture['context'])


def test_single_context_capture_selects_its_fixture_on_multi_game_day(monkeypatch):
    spec=importlib.util.spec_from_file_location('capture_selected_game',ROOT/'scripts/capture_official_research_inputs.py')
    script=importlib.util.module_from_spec(spec);spec.loader.exec_module(script)
    first=game();second=game()
    second.update(G_ID='20261101LGKT0',AWAY_ID='LG',HOME_ID='KT')
    schedule=json.dumps({'code':100,'game':[first,second]}).encode()
    lineup=json.dumps([[{'LINEUP_CK':False}]]).encode()
    calls=[]
    def fetch(_session,url,data):
        calls.append((url,data))
        raw=schedule if url==script.KBO_URL else lineup
        return raw,{'url':url,'status':200,'received_at':RECEIPT,
                    'raw_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                    'raw_base64':base64.b64encode(raw).decode()}
    monkeypatch.setattr(script,'fetch',fetch)
    result=script.capture('20261101',object(),official_game_id=second['G_ID'])
    assert [g['official_game_id'] for g in result['games']]==[second['G_ID']]
    assert {f['official_game_id'] for f in result['facts']}=={second['G_ID']}
    assert result['selection']['other_scheduled_games']==1
    assert calls[1][1]['gameId']==second['G_ID'] and len(calls)==2
    missing=script.capture('20261101',object(),official_game_id='20261101XXYY0')
    assert not missing['games'] and not missing['facts'] and missing['request_attempts']==1
    with pytest.raises(ValueError,match='invalid_target_game_id'):
        script.capture('20261101',object(),official_game_id='../different-day')
