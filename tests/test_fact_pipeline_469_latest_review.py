import copy,importlib.util,json,sys,subprocess
from pathlib import Path
import pytest
from test_fact_pipeline_469_review import V,fixture,cap,ad,adapter_for

spec=importlib.util.spec_from_file_location('queue_469_final',V/'queue_469_latest/src/research_dispatch.py');q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)

def make_manifest():
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0];return q.build_manifest([row])

def test_old_claim_forged_event_and_dispatched_cannot_prepare(tmp_path):
 job=q.publish(tmp_path,make_manifest())[0]
 with pytest.raises(ValueError,match='stale claim'):q.dispatch_request(tmp_path,{**job,'event_id':'f'*64,'state':'claimed'})
 original=q.claim(tmp_path)[0];q.transition(tmp_path,original['event_id'],'pending',operator_checked=True)
 with pytest.raises(ValueError,match='stale claim'):q.dispatch_request(tmp_path,original)
 current=q.claim(tmp_path)[0];assert original['claim_id']!=current['claim_id']
 with pytest.raises(ValueError,match='stale claim'):q.dispatch_request(tmp_path,original)
 assert q.dispatch_request(tmp_path,current)['threadId']==q.WORKERS['kbo']
 q.transition(tmp_path,current['event_id'],'dispatched')
 with pytest.raises(ValueError,match='stale claim'):q.dispatch_request(tmp_path,current)

def test_real_capture_adapter_queue_seed_and_price_change(tmp_path):
 data=fixture();b=data['bundle'];c=data['context']
 def run(bundle,context,label):
  cap.run_capture_pipeline(bundle,adapter=adapter_for(context),build_manifest=q.build_manifest,raw_path=tmp_path/(label+'raw'),inputs_path=tmp_path/(label+'input'),manifest_path=tmp_path/(label+'manifest'))
  return json.loads((tmp_path/(label+'manifest')).read_text(encoding='utf8'))
 first=run(b,c,'one');assert len(first['cases'])==1 and first['cases'][0]['input']['status']=='ready'
 root=tmp_path/'queue';assert q.publish(root,first,seed=True)==[] and q.claim(root)==[]
 later=copy.deepcopy(b);later['facts'][0]['official']['received_at']='2026-10-31T09:01:00+00:00';later['requests'][0]['received_at']='2026-10-31T09:01:00+00:00'
 assert q.publish(root,run(later,c,'two'))==[]
 fresh=copy.deepcopy(b);ctx=copy.deepcopy(c);fresh['facts'][0]['market']['odds']['home']=1.85;ctx['market']['odds']['home']=1.85
 assert len(q.publish(root,run(fresh,ctx,'three')))==1

def test_uncertain_and_forged_input_path(tmp_path):
 job=q.publish(tmp_path,make_manifest())[0]
 with pytest.raises(ValueError,match='unsafe'):q.load_job_input(tmp_path,{**job,'input_path':'../../outside'})
 job=q.claim(tmp_path)[0];q.transition(tmp_path,job['event_id'],'uncertain');assert q.claim(tmp_path)==[]
 with pytest.raises(ValueError,match='inspection'):q.transition(tmp_path,job['event_id'],'pending')

def test_seed_and_field_normalization_latest(tmp_path):
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0]
 row['prompt']='untrusted';row['threadId']='bad';row['source_evidence'][0]['external_receipt_verified']=True
 manifest=q.build_manifest([row]);safe=manifest['cases'][0]['input']
 assert 'prompt' not in safe and 'threadId' not in safe
 assert all(x['external_receipt_verified'] is False for x in safe['source_evidence'])
 forged=copy.deepcopy(manifest);forged['cases'][0]['input']['source_evidence'][0]['external_receipt_verified']=True
 with pytest.raises(ValueError,match='integrity'):q.validate_manifest(forged)
 assert q.publish(tmp_path,manifest,seed=True)==[] and q.publish(tmp_path,manifest)==[]

def test_latest_still_allows_multiple_calls_over_three_outstanding(tmp_path):
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0];items=[]
 for league in ('kbo','npb','nba','mlb'):
  x=copy.deepcopy(row);x['league_id']=league;x['game_id']=league;items.append(x)
 q.publish(tmp_path,q.build_manifest(items));assert len(q.claim(tmp_path))==3 and len(q.claim(tmp_path))==1

def test_latest_cli_seed(tmp_path):
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0];source=tmp_path/'input.json';source.write_text(json.dumps([row]),encoding='utf8')
 cmd=[sys.executable,str(V/'queue_469_latest/scripts/dispatch_league_research.py'),'--state-dir',str(tmp_path/'state')]
 result=subprocess.run(cmd+['publish','--adapter-inputs',str(source),'--seed'],capture_output=True,text=True,encoding='utf8',check=True)
 repeat=subprocess.run(cmd+['publish','--adapter-inputs',str(source)],capture_output=True,text=True,encoding='utf8',check=True)
 assert json.loads(result.stdout)==json.loads(repeat.stdout)==[]
