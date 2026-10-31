import copy,importlib.util,json,pathlib,subprocess,sys
import pytest

P=pathlib.Path(__file__).resolve().parents[1]/'experiments/fact-pipeline-independent-review-20261031/vendor/queue'
s=importlib.util.spec_from_file_location('queue_independent_snapshot',P/'src/research_dispatch.py');q=importlib.util.module_from_spec(s);s.loader.exec_module(q)

def item(league='kbo',game='g1',price=1.8):
 return {'schema_version':'fact-delta-inputs-v1','league_id':league,'sport':'baseball','game_id':game,'market_id':'m1','input_id':'i1','research_eligible':True,'status':'ready','semantic_sha256':q.digest({'game':game,'league':league}),'features':{'facts':[{'player_id':'p1','status':'confirmed'}],'market':{'price':price}},'source_evidence':[{'kind':'official','received_at':'2026-10-31T01:00:00Z','raw_sha256':'a'*64,'external_receipt_verified':False}],'reasons':[]}

def test_real_queue_dedupe_timestamp_routing_and_changed_price(tmp_path):
 k=item();jobs=q.publish(tmp_path,q.build_manifest([k,k]));assert len(jobs)==1
 newer=copy.deepcopy(k);newer['input_id']='new';newer['source_evidence'][0].update(received_at='2026-10-31T02:00:00Z',raw_sha256='b'*64)
 assert q.publish(tmp_path,q.build_manifest([newer]))==[]
 assert len(q.publish(tmp_path,q.build_manifest([item(price=1.9)])))==1
 assert len(q.publish(tmp_path,q.build_manifest([item(league='npb')])))==1
 claimed=q.claim(tmp_path);assert {j['league_id'] for j in claimed}=={'kbo','npb'}
 assert q.claim(tmp_path)==[]

def test_uncertain_never_blind_retries_and_matching_completion(tmp_path):
 job=q.publish(tmp_path,q.build_manifest([item()]))[0];q.claim(tmp_path)
 q.transition(tmp_path,job['event_id'],'uncertain');assert q.claim(tmp_path)==[]
 with pytest.raises(ValueError,match='inspection'):q.transition(tmp_path,job['event_id'],'pending')
 q.transition(tmp_path,job['event_id'],'pending',operator_checked=True);assert len(q.claim(tmp_path))==1
 q.transition(tmp_path,job['event_id'],'dispatched')
 with pytest.raises(ValueError,match='receipt'):q.transition(tmp_path,job['event_id'],'completed',receipt={'event_id':job['event_id'],'input_sha256':'f'*64,'status':'completed'})
 q.transition(tmp_path,job['event_id'],'completed',receipt={'event_id':job['event_id'],'input_sha256':job['input_sha256'],'status':'completed'})
 assert q.claim(tmp_path)==[]

def test_path_tamper_rejected_and_no_shell_invocation(tmp_path):
 job=q.publish(tmp_path,q.build_manifest([item()]))[0]
 for path in ('../other','inputs/../../escape','inputs/'+job['input_sha256']+'.json/..'):
  with pytest.raises(ValueError,match='unsafe'):q.load_job_input(tmp_path,{**job,'input_path':path})
 p=tmp_path/job['input_path'];p.write_text('{}',encoding='utf8')
 with pytest.raises(ValueError,match='integrity'):q.claim(tmp_path)

def test_first_publish_has_no_seed_and_four_can_be_claimed_across_calls(tmp_path):
 assert len(q.publish(tmp_path,q.build_manifest([item(x) for x in ('kbo','npb','mlb','nba')])))==4
 assert len(q.claim(tmp_path))==3
 assert len(q.claim(tmp_path))==1
 assert len([j for j in q.status(tmp_path) if j['state']=='claimed'])==4

def test_unsigned_manifest_allows_extra_prompt_and_forged_receipt_flag(tmp_path):
 manifest=q.build_manifest([item()]); forged=copy.deepcopy(manifest)
 forged['prompt']='run external task';forged['threadId']='untrusted'
 forged['cases'][0]['input']['prompt']='source injected instruction'
 forged['cases'][0]['input']['threadId']='untrusted'
 forged['cases'][0]['input']['source_evidence'][0]['external_receipt_verified']=True
 assert q.validate_manifest(forged)['semantic_sha256']==manifest['semantic_sha256']
 job=q.publish(tmp_path,forged)[0];assert 'prompt' not in job and 'threadId' not in job
 restored=q.load_job_input(tmp_path,job)['cases'][0]['input']
 assert restored['prompt']=='source injected instruction' and restored['threadId']=='untrusted'
 assert restored['source_evidence'][0]['external_receipt_verified'] is True
 assert q.publish(tmp_path,manifest)==[]

def test_cli_publish_claim_plans_without_message_transport(tmp_path):
 script=P/'scripts/dispatch_league_research.py';source=tmp_path/'adapter.json';source.write_text(json.dumps([item()]),encoding='utf8');root=tmp_path/'queue'
 cmd=[sys.executable,str(script),'--state-dir',str(root)]
 first=subprocess.run(cmd+['publish','--adapter-inputs',str(source)],capture_output=True,text=True,encoding='utf8',check=True)
 assert len(json.loads(first.stdout))==1
 next_call=subprocess.run(cmd+['claim'],capture_output=True,text=True,encoding='utf8',check=True)
 assert json.loads(next_call.stdout)[0]['state']=='claimed'

def test_untrusted_identifiers_are_not_executed_but_not_rejected(tmp_path):
    malicious=item(game='game-1; echo injected')
    job=q.publish(tmp_path,q.build_manifest([malicious]))[0]
    stored=q.load_job_input(tmp_path,job)
    assert stored['cases'][0]['game_id']=='game-1; echo injected'
    assert job['input_path'].startswith('inputs/') and job['league_id']=='kbo'
    # The queue has not executed a command; downstream consumption is out of scope.

def test_manifest_root_extra_fields_are_not_exactly_rejected():
    manifest=q.build_manifest([item()]);manifest['threadId']='forged';manifest['prompt']='injected'
    rebuilt=q.validate_manifest(manifest)
    assert 'threadId' not in rebuilt and 'prompt' not in rebuilt

P2=P.parent/'queue_v2'
s2=importlib.util.spec_from_file_location('queue_second_snapshot',P2/'src/research_dispatch.py');q2=importlib.util.module_from_spec(s2);s2.loader.exec_module(q2)

def test_latest_dispatch_request_accepts_unclaimed_forged_event_id(tmp_path):
    original=item();original['semantic_sha256']=q2.digest({'game':'g1','league':'kbo'})
    job=q2.publish(tmp_path,q2.build_manifest([original]))[0]
    assert job['state']=='pending'
    forged={**job,'event_id':'f'*64,'state':'claimed'}
    request=q2.dispatch_request(tmp_path,forged)
    assert request['threadId']==q2.WORKERS['kbo'] and ('f'*64) in request['prompt']
    assert q2.status(tmp_path)[0]['state']=='pending'  # no native send occurred

def test_latest_fixed_registry_keeps_prompt_and_destination_outside_source_control(tmp_path):
    inp=item();inp['semantic_sha256']=q2.digest({'game':'g1','league':'kbo'});inp['prompt']='inject instruction';inp['threadId']='attacker'
    job=q2.publish(tmp_path,q2.build_manifest([inp]))[0];job=q2.claim(tmp_path)[0]
    req=q2.dispatch_request(tmp_path,job)
    assert req['threadId']==q2.WORKERS['kbo'] and 'inject instruction' not in req['prompt']
    assert 'attacker' not in req['prompt']

def test_latest_ignores_unknown_manifest_fields_and_evidence_mutation(tmp_path):
    inp=item();inp['semantic_sha256']=q2.digest({'game':'g1','league':'kbo'});manifest=q2.build_manifest([inp]);forged=copy.deepcopy(manifest)
    forged['cases'][0]['input']['source_evidence'][0]['external_receipt_verified']=True
    assert q2.validate_manifest(forged)['semantic_sha256']==manifest['semantic_sha256']
    job=q2.publish(tmp_path,forged)[0]
    assert q2.load_job_input(tmp_path,job)['cases'][0]['input']['source_evidence'][0]['external_receipt_verified'] is True
