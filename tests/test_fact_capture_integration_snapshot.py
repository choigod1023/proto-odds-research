import copy,importlib.util,json,sys
from pathlib import Path
import pytest
from test_fact_pipeline_adapter_snapshot import sample
from test_fact_pipeline_queue_snapshot import q

V=Path(__file__).resolve().parents[1]/'experiments/fact-pipeline-independent-review-20261031/vendor'
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
sys.path.insert(0,str(V/'capture/src'))
cap=load('independent_capture_cli',V/'capture/scripts/capture_official_research_inputs.py')
fc=load('official_fact_capture',V/'capture/src/official_fact_capture.py')
adapter=load('fact_delta_current_interim',V/'fact_delta_live_snapshot.py')

def confirmed_sample():
 f,r,b=sample();raw=copy.deepcopy(f);raw['kind']='confirmed_starting_pitcher';raw['after_value']['announcement_status']='confirmed';raw['official']['url']=fc.KBO_URL;raw['official']['external_receipt_verified']=True
 raw['market']['odds']=[1.8,2.2];raw['semantic_sha256']=fc.semantic_hash(raw)
 return raw,r,b

def mapper(registry,baseline):
 def call(facts):
  normalized=copy.deepcopy(facts[0]);normalized.pop('semantic_sha256');normalized['kind']='confirmed_starter_change';normalized['after_value'].pop('announcement_status');normalized['official'].pop('external_receipt_verified')
  normalized['market']['odds']={'home':1.8,'away':2.2}
  return [adapter.to_adapter_input([normalized],registry,baseline,normalized['market'],input_id='synthetic')]
 return call

def test_capture_adapter_queue_local_witness_claim_is_not_verified(tmp_path):
 fact,registry,baseline=confirmed_sample(); get=mapper(registry,baseline);row=get([fact])[0]
 assert row['research_eligible'] is True
 assert all(ev['external_receipt_verified'] is False for ev in row['source_evidence'])
 assert cap.eligible_fact(fact,row) is True  # trusts caller's boolean, no witness supplied
 bundle={'facts':[fact]}
 result=cap.run_capture_pipeline(bundle,adapter=get,build_manifest=q.build_manifest,raw_path=tmp_path/'raw.json',inputs_path=tmp_path/'in.json',manifest_path=tmp_path/'manifest.json')
 assert result['inputs']==1
 manifest=json.loads((tmp_path/'manifest.json').read_text(encoding='utf8'))
 assert len(manifest['cases'])==1
 assert len(q.publish(tmp_path/'queue',manifest))==1
 without_witness=copy.deepcopy(fact);without_witness['official']['external_receipt_verified']=False
 assert cap.eligible_fact(without_witness,row) is False

def test_real_probable_capture_no_research_job(tmp_path):
 g={'LE_ID':1,'SR_ID':5,'SEASON_ID':2026,'G_DT':'20261101','G_ID':'20261101HTSS0','G_TM':'14:00','AWAY_ID':'HT','HOME_ID':'SS','GAME_STATE_SC':'1','CANCEL_SC_ID':'0','START_PIT_CK':1,'T_PIT_P_ID':55633,'T_PIT_P_NM':'away','B_PIT_P_ID':53375,'B_PIT_P_NM':'home'}
 payload=json.dumps({'code':100,'game':[g]}).encode();captured=fc.normalize_kbo_schedule(payload,expected_date='20261101',received_at='2026-10-31T01:00:00Z')
 assert len(captured['facts'])==2 and all(f['official']['confirmed'] is False for f in captured['facts'])
 f,r,b=sample(); row=adapter.to_adapter_input(captured['facts'],r,b,f['market'],input_id='synthetic')
 assert row['status']=='rejected' and row['research_eligible'] is False
 assert q.build_manifest([row])['cases']==[]
