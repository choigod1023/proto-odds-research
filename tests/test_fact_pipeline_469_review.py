import copy,importlib.util,json,sys
from pathlib import Path
import pytest

V=Path(__file__).resolve().parents[1]/'experiments/fact-pipeline-independent-review-20261031/vendor'
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod
q=load('queue_469_review',V/'queue_469/src/research_dispatch.py')
sys.path.insert(0,str(V/'capture_467_new/src'))
cap=load('capture_467_new',V/'capture_467_new/scripts/capture_official_research_inputs.py')
ad=load('adapter_uncommitted_review',V/'fact_delta_live_snapshot.py')
FIX=V/'capture_467_new/experiments/verified-fact-capture-20261031/synthetic-real-adapter-fixture.json'
def fixture():return json.loads(FIX.read_text(encoding='utf8'))
def adapter_for(context):return lambda facts:cap.adapt_with_context(facts,to_adapter_input=ad.to_adapter_input,context=context)
def pipeline(tmp,bundle,ctx,label):
 cap.run_capture_pipeline(bundle,adapter=adapter_for(ctx),build_manifest=q.build_manifest,raw_path=tmp/(label+'-raw.json'),inputs_path=tmp/(label+'-input.json'),manifest_path=tmp/(label+'-manifest.json'))
 return json.loads((tmp/(label+'-manifest.json')).read_text(encoding='utf8'))

def test_actual_three_components_seed_recapture_and_new_price(tmp_path):
 data=fixture();b=data['bundle'];c=data['context'];first=pipeline(tmp_path,b,c,'first')
 assert len(first['cases'])==1 and first['cases'][0]['input']['research_eligible'] is True
 compact={k:copy.deepcopy(b['facts'][0][k]) for k in cap.FACT_KEYS}
 compact['official']={k:compact['official'][k] for k in cap.EVIDENCE_KEYS['official']}
 envelope=ad.build_research_envelope([compact],c['registry'],c['baseline'],c['market'])
 assert envelope['probability_vector']==c['baseline']['probability_vector'] and envelope['prediction_changed'] is False
 assert all(x['external_receipt_verified'] is False for x in first['cases'][0]['input']['source_evidence'])
 queue=tmp_path/'state';assert q.publish(queue,first,seed=True)==[] and q.claim(queue)==[]
 later=copy.deepcopy(b);later['facts'][0]['official']['received_at']='2026-10-31T09:01:00+00:00';later['requests'][0]['received_at']='2026-10-31T09:01:00+00:00'
 second=pipeline(tmp_path,later,c,'second');assert q.publish(queue,second)==[]
 revised=copy.deepcopy(b);context=copy.deepcopy(c);revised['facts'][0]['market']['odds']['home']=1.85;context['market']['odds']['home']=1.85
 third=pipeline(tmp_path,revised,context,'third');jobs=q.publish(queue,third)
 assert len(jobs)==1 and jobs[0]['league_id']=='kbo'
 assert len(q.claim(queue))==1

def test_pr469_drops_top_injection_and_forces_unverified(tmp_path):
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0]
 row['prompt']='DO NOT OBEY';row['threadId']='forged';row['source_evidence'][0]['external_receipt_verified']=True
 manifest=q.build_manifest([row]);sanitized=manifest['cases'][0]['input']
 assert 'prompt' not in sanitized and 'threadId' not in sanitized
 assert all(e['external_receipt_verified'] is False for e in sanitized['source_evidence'])
 forged=copy.deepcopy(manifest);forged['cases'][0]['input']['source_evidence'][0]['external_receipt_verified']=True
 with pytest.raises(ValueError,match='integrity'):q.validate_manifest(forged)

def test_nested_instructions_survive_but_fixed_prompt_does_not_include_them(tmp_path):
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0]
 row['source_evidence'][0]['prompt']='NESTED BAD';row['source_evidence'][0]['threadId']='attacker'
 job=q.publish(tmp_path,q.build_manifest([row]))[0];stored=q.load_job_input(tmp_path,job)['cases'][0]['input']
 assert stored['source_evidence'][0]['prompt']=='NESTED BAD' and stored['source_evidence'][0]['threadId']=='attacker'
 job=q.claim(tmp_path)[0];request=q.dispatch_request(tmp_path,job)
 assert request['threadId']==q.WORKERS['kbo'] and 'NESTED BAD' not in request['prompt'] and 'attacker' not in request['prompt']

def test_pr469_still_accepts_forged_claim_in_public_helper(tmp_path):
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0]
 job=q.publish(tmp_path,q.build_manifest([row]))[0];assert job['state']=='pending'
 request=q.dispatch_request(tmp_path,{**job,'event_id':'f'*64,'state':'claimed'})
 assert request['threadId']==q.WORKERS['kbo'] and ('f'*64) in request['prompt']
 assert q.status(tmp_path)[0]['state']=='pending'

def test_seed_is_optional_and_per_call_claim_not_global_limit(tmp_path):
 data=fixture();base=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0]
 rows=[]
 for league in ('kbo','mlb','npb','nba'):
  row=copy.deepcopy(base);row['league_id']=league;row['game_id']='game-'+league;rows.append(row)
 assert len(q.publish(tmp_path,q.build_manifest(rows)))==4  # explicit --seed not used
 assert len(q.claim(tmp_path))==3 and len(q.claim(tmp_path))==1

def test_uncertain_and_input_path_tamper_fail_closed(tmp_path):
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0]
 job=q.publish(tmp_path,q.build_manifest([row]))[0]
 with pytest.raises(ValueError,match='unsafe'):q.load_job_input(tmp_path,{**job,'input_path':'../../escape'})
 job=q.claim(tmp_path)[0];q.transition(tmp_path,job['event_id'],'uncertain')
 assert q.claim(tmp_path)==[]
 with pytest.raises(ValueError,match='inspection'):q.transition(tmp_path,job['event_id'],'pending')

def test_stale_real_claim_can_still_create_dispatch_request(tmp_path):
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0]
 q.publish(tmp_path,q.build_manifest([row]));claimed=q.claim(tmp_path)[0]
 q.transition(tmp_path,claimed['event_id'],'pending',operator_checked=True)
 assert q.status(tmp_path)[0]['state']=='pending'
 request=q.dispatch_request(tmp_path,claimed)
 assert request['threadId']==q.WORKERS['kbo']

def test_capture_pipeline_accepts_matching_claimed_hash_without_raw_bytes(tmp_path):
 data=fixture();b=data['bundle'];c=data['context'];digest='e'*64
 b['facts'][0]['official']['raw_sha256']=digest;b['requests'][0]['raw_sha256']=digest
 # Both hash assertions agree, but this fixture supplies no response bytes.
 assert 'raw' not in b['requests'][0] and 'raw_base64' not in b['requests'][0]
 manifest=pipeline(tmp_path,b,c,'without-raw')
 assert len(manifest['cases'])==1
 assert all(e['external_receipt_verified'] is False for e in manifest['cases'][0]['input']['source_evidence'])

def test_two_valid_games_fail_batch_pipeline_due_to_cross_validation(tmp_path):
 data=fixture();bundle=copy.deepcopy(data['bundle']);context1=copy.deepcopy(data['context']);second=copy.deepcopy(bundle['facts'][0]);second['canonical_game_id']='game-2';second['official_game_id']='official-2';second['fact_id']='f2';second['market']['product_id']='p2';bundle['facts'].append(second)
 context2=copy.deepcopy(context1);context2['registry'].update(canonical_game_id='game-2',official_game_id='official-2');context2['registry']['product']['product_id']='p2';context2['baseline'].update(canonical_game_id='game-2',product_id='p2');context2['market']['product_id']='p2';context2['input_id']='second'
 def two_rows(facts):
  return cap.adapt_with_context([facts[0]],to_adapter_input=ad.to_adapter_input,context=context1)+cap.adapt_with_context([facts[1]],to_adapter_input=ad.to_adapter_input,context=context2)
 rows=two_rows(bundle['facts']);assert len(rows)==2 and all(r['research_eligible'] and r['status']=='ready' for r in rows)
 assert cap.eligible_fact(bundle['facts'][0],rows[0],bundle) and cap.eligible_fact(bundle['facts'][1],rows[1],bundle)
 with pytest.raises(ValueError,match='unsupported_ready_input'):
  cap.run_capture_pipeline(bundle,adapter=two_rows,build_manifest=q.build_manifest,raw_path=tmp_path/'raw.json',inputs_path=tmp_path/'inputs.json',manifest_path=tmp_path/'manifest.json')
 assert (tmp_path/'raw.json').exists() and not (tmp_path/'manifest.json').exists()

def test_pr469_cli_explicit_seed_has_no_first_job(tmp_path):
 import subprocess
 data=fixture();row=cap.adapt_with_context(data['bundle']['facts'],to_adapter_input=ad.to_adapter_input,context=data['context'])[0]
 inputs=tmp_path/'adapter.json';inputs.write_text(json.dumps([row]),encoding='utf8')
 cmd=[sys.executable,str(V/'queue_469/scripts/dispatch_league_research.py'),'--state-dir',str(tmp_path/'state')]
 first=subprocess.run(cmd+['publish','--adapter-inputs',str(inputs),'--seed'],capture_output=True,text=True,encoding='utf8',check=True)
 replay=subprocess.run(cmd+['publish','--adapter-inputs',str(inputs)],capture_output=True,text=True,encoding='utf8',check=True)
 assert json.loads(first.stdout)==[] and json.loads(replay.stdout)==[]
