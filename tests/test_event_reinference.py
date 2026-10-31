import copy
import importlib.util
import sys
from pathlib import Path
from datetime import datetime

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from event_reinference import InvalidInput, polling_plan, run_batch

NOW = '2026-10-30T09:30:00+00:00'

def job(game='g1'):
    return {'identity': {'league':'KBO','canonical_game_id':game,'official_game_id':'game-1',
        'product_id':'p1','home_team_id':'h','away_team_id':'a','kickoff_at':'2026-10-30T10:30:00+00:00'},
        'status':'scheduled', 'snapshot_at':NOW, 'model_revision':'model-v1',
        'quote':{'product_id':'p1','status':'open','market':'win','period':'full','line':None,
                 'outcomes':['home','away'],'odds':{'home':1.8,'away':2.1},
                 'observed_at':'2026-10-30T09:29:00+00:00','received_at':NOW},
        'facts':[{'kind':'lineup','canonical_game_id':game,'content':{'team_id':'h','players':['p1']},
                  'observed_at':'2026-10-30T09:20:00+00:00','received_at':NOW}]}

def run(jobs, *, success=None, seen=None, verify=lambda *_: True, infer=lambda *_:{'probability_vector':[0.5,0.5]}, quota=3, now=NOW):
    return run_batch(jobs, now=now, quote_max_age_seconds=600, fact_max_age_seconds=3600,
                     max_inferences=quota, max_jobs=10, last_success={} if success is None else success, last_seen={} if seen is None else seen,
                     verify=verify, infer=infer)

def test_schedule_is_bounded_and_live_does_not_claim_model_support():
    kickoff='2026-10-30T10:30:00+00:00'
    assert polling_plan('scheduled',kickoff,'2026-10-28T00:00:00+00:00')['after_seconds']==21600
    assert polling_plan('scheduled',kickoff,'2026-10-30T03:00:00+00:00')['after_seconds']==3600
    assert polling_plan('scheduled',kickoff,NOW)['after_seconds']==900
    assert polling_plan('scheduled',kickoff,'2026-10-30T10:00:00+00:00')['phase']=='frozen'
    assert polling_plan('live',kickoff,NOW)=={'phase':'live_research_adapter_unsupported','after_seconds':None}
    assert polling_plan('postponed',kickoff,NOW)['phase']=='closed'
    with pytest.raises(InvalidInput): polling_plan('scheduled',kickoff,'2026-10-30T09:30:00')

def test_change_dedup_retries_quota_and_out_of_order():
    a=job(); counter=[]
    def infer(j,_): counter.append(j['identity']['canonical_game_id']); return {'probability_vector':[.55,.45]}
    first=run([a,copy.deepcopy(a)],infer=infer); assert [x['status'] for x in first['outcomes']]==['inferred','unchanged']; assert counter==['g1']
    same=copy.deepcopy(a); same['snapshot_at']='2026-10-30T09:31:00+00:00'; same['quote']['received_at']=same['snapshot_at']
    repeat=run([same],success=first['last_success'],infer=infer,now=same['snapshot_at']); assert repeat['outcomes'][0]['status']=='unchanged'; assert len(counter)==1
    older=run([a],success=repeat['last_success'],infer=infer); assert older['outcomes'][0]['reason']=='out_of_order_snapshot'
    changed=copy.deepcopy(same); changed['snapshot_at']='2026-10-30T09:32:00+00:00'; changed['quote']['received_at']=changed['snapshot_at']; changed['quote']['observed_at']=changed['snapshot_at']; changed['quote']['odds']['home']=1.9
    failed=run([changed],success=repeat['last_success'],infer=lambda *_: (_ for _ in ()).throw(RuntimeError()),now=changed['snapshot_at']); assert failed['outcomes'][0]['reason']=='worker_failed'; assert failed['last_success']==repeat['last_success']
    blocked=run([changed,job('g2')],success=repeat['last_success'],quota=0,now=changed['snapshot_at']); assert all(x['reason']=='inference_quota' for x in blocked['outcomes'])
    done=run([changed],success=failed['last_success'],infer=infer,now=changed['snapshot_at']); assert done['outcomes'][0]['status']=='inferred'; assert len(counter)==2

def test_player_event_change_is_scoped_per_game_and_delivery_is_not_learned_effect():
    a=job(); first=run([a]); changed=copy.deepcopy(a); changed['facts'].append({'kind':'injury','canonical_game_id':'g1','content':{'player_id':'p1','state':'out'},'observed_at':'2026-10-30T09:31:00+00:00','received_at':'2026-10-30T09:31:00+00:00'}); changed['snapshot_at']='2026-10-30T09:31:00+00:00'
    result=run([changed,job('g2')],success=first['last_success'],now=changed['snapshot_at']); assert [x['status'] for x in result['outcomes']]==['inferred','inferred']; assert result['metrics']['attempted']==2

@pytest.mark.parametrize(('mutate','reason'),[
 (lambda x:x['quote'].update(status='suspended'),'market_not_open'),
 (lambda x:x['quote'].update(observed_at='2026-10-30T09:00:00+00:00'),'quote_stale'),
 (lambda x:x['quote'].update(received_at='2026-10-30T09:31:00+00:00'),'quote_unavailable'),
 (lambda x:x['quote']['odds'].pop('away'),'invalid_quote_vector'),
 (lambda x:x['quote'].update(product_id='other'),'quote_product_mismatch'),
 (lambda x:x.update(status='live'),'live_research_adapter_unsupported'),
 (lambda x:x.update(status='postponed'),'game_not_scheduled'),
 (lambda x:x['facts'][0].update(canonical_game_id='wrong'),'fact_game_mismatch'),
 (lambda x:x['facts'][0].update(observed_at='2026-10-30T07:00:00+00:00'),'fact_stale'),
])
def test_invalid_event_withholds_without_inference(mutate,reason):
    a=job(); mutate(a); result=run([a],infer=lambda *_: pytest.fail('invalid input ran'))
    assert result['outcomes'][0]['reason']==reason and result['last_success']=={}

def test_failure_closed_and_t30_boundary():
    assert run([job()],verify=lambda *_: False)['outcomes'][0]['reason']=='evidence_unverified'
    assert run([job()],infer=lambda *_:{'probability_vector':[float('nan'),0]})['outcomes'][0]['reason']=='invalid_model_output'
    a=job(); a['snapshot_at']='2026-10-30T10:00:00+00:00'; a['quote'].update(observed_at=a['snapshot_at'],received_at=a['snapshot_at']); a['facts']=[]
    assert run([a],now=a['snapshot_at'])['outcomes'][0]['reason']=='pregame_frozen'
    a['snapshot_at']='2026-10-30T09:59:59+00:00'; a['quote'].update(observed_at=a['snapshot_at'],received_at=a['snapshot_at'])
    assert run([a],now=a['snapshot_at'])['outcomes'][0]['status']=='inferred'
    assert run([a],now='2026-10-30T10:00:01+00:00')['outcomes'][0]['reason']=='pregame_frozen'

def test_research_adapter_runs_with_its_verified_contract():
    spec=importlib.util.spec_from_file_location('existing_accuracy_test',ROOT/'tests/test_accuracy_development.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    state, doc, contract, _=module.fixture(); before=state.export(); a=job(); a['identity']=copy.deepcopy(contract['identity']); a['facts']=[]
    timestamp='2026-10-30T09:29:59+00:00'; a['snapshot_at']=timestamp; contract['decision_at']=timestamp
    a['quote'].update(odds=copy.deepcopy(contract['quote']['odds']),observed_at=contract['quote']['observed_at'],received_at=contract['quote']['revision_received_at'])
    trusted=copy.deepcopy(contract)
    def verifier(candidate, snapshot): return candidate == trusted and snapshot == before
    def actual(j, now):
        request={'identity':j['identity'],'decision_at':now.isoformat(),'lineup':None}
        return module.infer(state,request,contract,verifier)
    out=run_batch([a],now=timestamp,quote_max_age_seconds=3600,fact_max_age_seconds=3600,max_inferences=1,max_jobs=1,last_success={},last_seen={},verify=lambda j,n: j['identity']==contract['identity'] and j['quote']['odds']==contract['quote']['odds'],infer=actual)
    assert out['outcomes'][0]['status']=='inferred'; assert state.export()==before
    assert out['outcomes'][0]['result']['availability_reason']['code']=='lineup_unavailable'


def test_older_or_ambiguous_quote_and_fact_do_not_roll_back_last_success():
    first=run([job()]); old=first['last_success']; newer=copy.deepcopy(job()); newer['snapshot_at']='2026-10-30T09:31:00+00:00'
    newer['quote']['odds']['home']=1.95
    assert run([newer],success=old,now=newer['snapshot_at'])['outcomes'][0]['reason']=='out_of_order_quote'
    newer=copy.deepcopy(job()); newer['snapshot_at']='2026-10-30T09:31:00+00:00'; newer['facts']=[]
    assert run([newer],success=old,now=newer['snapshot_at'])['outcomes'][0]['reason']=='out_of_order_fact'
    assert old==first['last_success']


def test_oversized_batch_refused_before_any_work():
    with pytest.raises(InvalidInput, match='batch_limit_exceeded'):
        run_batch([job(),job('g2')],now=NOW,quote_max_age_seconds=600,fact_max_age_seconds=3600,max_inferences=1,max_jobs=1,last_success={},last_seen={},verify=lambda *_: pytest.fail('must not verify'),infer=lambda *_: pytest.fail('must not run'))

def test_suspension_or_stale_input_never_reuses_result_as_current():
    a=job(); first=run([a]); stopped=copy.deepcopy(a); stopped['snapshot_at']='2026-10-30T09:31:00+00:00'; stopped['quote']['status']='suspended'
    out=run([stopped],success=first['last_success'],now=stopped['snapshot_at']); assert out['outcomes']==[{'game_id':'g1','status':'withheld','reason':'market_not_open'}]
    assert out['last_success']==first['last_success']


def test_verifier_runs_before_unchanged_response():
    first=run([job()]); second=run([job()],success=first['last_success'],verify=lambda *_:False)
    assert second['outcomes'][0]['reason']=='evidence_unverified'


def test_frozen_input_cannot_mutate_prior_record():
    first=run([job()]); saved=copy.deepcopy(first['last_success'])
    result=run([job()],success=first['last_success'],now='2026-10-30T10:00:01+00:00')
    assert result['outcomes'][0]['reason']=='pregame_frozen'
    assert result['last_success']==saved==first['last_success']


def test_suspension_and_failed_inference_block_older_open_snapshots_and_allow_retry():
    a=job(); first=run([a]); halted=copy.deepcopy(a); halted['snapshot_at']='2026-10-30T09:31:00+00:00'; halted['quote']['status']='suspended'
    blocked=run([halted],success=first['last_success'],seen=first['last_seen'],now=halted['snapshot_at'])
    assert blocked['outcomes'][0]['reason']=='market_not_open'
    replay=run([a],success=blocked['last_success'],seen=blocked['last_seen'],now=halted['snapshot_at'])
    assert replay['outcomes'][0]['reason']=='out_of_order_snapshot'
    changed=copy.deepcopy(a); changed['snapshot_at']='2026-10-30T09:32:00+00:00'; changed['quote'].update(observed_at=changed['snapshot_at'],received_at=changed['snapshot_at'],odds={'home':1.9,'away':2.1})
    failed=run([changed],success=blocked['last_success'],seen=blocked['last_seen'],now=changed['snapshot_at'],infer=lambda *_: (_ for _ in ()).throw(RuntimeError()))
    assert failed['outcomes'][0]['reason']=='worker_failed'
    retry=run([changed],success=failed['last_success'],seen=failed['last_seen'],now=changed['snapshot_at'])
    assert retry['outcomes'][0]['status']=='inferred'
