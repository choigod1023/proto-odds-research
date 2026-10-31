import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from src.fact_delta_inputs import InvalidFact, build_research_envelope, bullpen_workload, extract_feature, to_adapter_input

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'experiments/fact-delta-research-adapter-20261031/example-bundle.json'


def fixture():return json.loads(FIX.read_text(encoding='utf8'))
def build(x):return build_research_envelope(x['facts'],x['registry'],x['baseline'],x['market'],workloads=x['workloads'])


def test_synthetic_end_to_end_keeps_baseline_and_hashes():
    x=fixture(); before=copy.deepcopy(x); output=build(x)
    assert x==before and output['probability_vector']==x['baseline']['probability_vector']
    assert output['baseline_input_content_sha256']==x['baseline']['input_content_sha256']
    assert output['baseline_freeze_hash']==x['baseline']['freeze_hash']
    assert output['prediction_changed'] is False and output['external_receipt_verified'] is False
    assert output['features'][0]['after_value']['status']=='confirmed'
    assert output['features'][0]['before_value']['status']=='probable'
    h=output.pop('research_envelope_sha256')
    canon=json.dumps(output,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    assert h==hashlib.sha256(canon.encode()).hexdigest()
    x['facts'][0]['after_value']['player_id']='changed'
    assert output['features'][0]['after_value']['player_id']=='p-new'


def test_missing_before_value_remains_unknown():
    x=fixture();x['facts'][0]['before_value']=None
    f=build(x)['features'][0]
    assert f['before_value'] is None and not f['before_available'] and f['reason']=='earlier_value_unavailable'
    assert build(x)['probability_vector']==[.55,.45]


def test_no_facts_preserves_baseline_but_still_requires_market():
    x=fixture();x['facts']=[]
    assert build(x)['availability_reason']=='candidate_model_unavailable'
    x['market']['odds'].pop('away')
    with pytest.raises(InvalidFact):build(x)


@pytest.mark.parametrize('mutate',[
 lambda x: x['facts'][0]['official'].update(confirmed=False),
 lambda x: x['facts'][0]['official'].update(identity_checked=False),
 lambda x: x['facts'][0]['after_value'].update(status='probable'),
 lambda x: x['facts'][0]['official'].update(received_at='2026-10-31T09:31:00+00:00'),
 lambda x: x['facts'][0]['claim'].update(received_at='2026-10-31T09:31:00+00:00'),
 lambda x: x['facts'][0].update(decision_at='2026-10-31T09:31:00+00:00'),
 lambda x: x['facts'][0]['claim'].update(received_at='2026-10-31T09:00:00'),
 lambda x: x['facts'][0].update(official_game_id='wrong'),
 lambda x: x['facts'][0].update(canonical_game_id='wrong'),
 lambda x: x['facts'][0].update(player_ids=['unknown']),
 lambda x: x['facts'][0]['after_value'].update(player_id='p-away'),
 lambda x: x['facts'][0].update(current_score='1-0'),
 lambda x: x['facts'][0]['after_value'].update(target_score=5),
 lambda x: x['facts'][0]['official'].update(raw_sha256='fake'),
 lambda x: x['baseline'].update(freeze_hash='fake'),
 lambda x: x['registry']['product'].update(line=-1.5),
 lambda x: x['market']['odds'].pop('away'),
 lambda x: x['market'].update(received_at='2026-10-31T09:31:00+00:00'),
 lambda x: x['facts'][0].update(revision=0),
])
def test_core_errors_fail_closed(mutate):
    x=fixture();mutate(x)
    with pytest.raises(InvalidFact):build(x)


def test_outcome_poisoning_is_rejected_not_used():
    x=fixture();expected=build(x)
    for key in ('result','final_score','current_score','future_label'):
        y=copy.deepcopy(x);y['facts'][0][key]=1
        with pytest.raises(InvalidFact):build(y)
    assert build(x)==expected


def boxscore():
    return {'official_game_id':'past-1','team_id':'H','status':'final','ended_at':'2026-10-30T18:00:00+00:00','received_at':'2026-10-30T19:00:00+00:00','official_url':'https://example.org/box','raw_sha256':'e'*64,'identity_checked':True,'pitch_counts':{'p-new':22}}


def workload_fact():
    x=fixture();f=x['facts'][0];f.update(kind='bullpen_workload_24h',before_value=None,after_value=22,units='pitches',player_ids=['p-new'])
    x['workloads']={'f1':{'records':[boxscore()],'expected_game_ids':['past-1'],'team_id':'H','schedule_complete':True}}
    return x


def test_bullpen_prior_official_boxscore_and_unknown_before():
    x=workload_fact();f=build(x)['features'][0]
    assert f['after_value']==22 and f['before_value'] is None and f['reason']=='earlier_value_unavailable'
    y=copy.deepcopy(x);y['facts'][0]['kind']='bullpen_workload_48h';assert build(y)['features'][0]['after_value']==22


@pytest.mark.parametrize('mutate',[
 lambda x: x['workloads']['f1']['records'][0].update(official_game_id='official-1'),
 lambda x: x['workloads']['f1']['records'][0].update(status='in_progress'),
 lambda x: x['workloads']['f1']['records'][0].update(ended_at='2026-10-31T10:00:00+00:00'),
 lambda x: x['workloads']['f1']['records'][0].update(received_at='2026-10-31T09:31:00+00:00'),
 lambda x: x['workloads']['f1']['records'][0].update(pitch_counts={}),
 lambda x: x['workloads']['f1']['records'][0].update(target_score=2),
 lambda x: x['workloads']['f1'].update(expected_game_ids=None),
 lambda x: x['workloads']['f1'].update(schedule_complete=False),
 lambda x: x['facts'][0].update(after_value=23),
])
def test_bullpen_missing_or_target_data_rejected(mutate):
    x=workload_fact();mutate(x)
    with pytest.raises(InvalidFact):build(x)


def test_feature_copies_mutable_values_and_cli_works():
    x=fixture();feature=extract_feature(x['facts'][0],x['registry'])
    x['facts'][0]['after_value']['player_id']='changed'
    assert feature.after_value['player_id']=='p-new'
    p=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'experiments/fact-delta-research-adapter-20261031/run_fixture.py'),str(FIX)],capture_output=True,text=True,encoding='utf8',check=True)
    assert json.loads(p.stdout)['prediction_changed'] is False


def adapt(x):
    return to_adapter_input(x['facts'],x['registry'],x['baseline'],x['market'],input_id='input-1',workloads=x['workloads'])


def test_dispatch_ready_does_not_mean_prediction_changed():
    x=fixture();out=adapt(x)
    assert out['schema_version']=='fact-delta-inputs-v1'
    assert out['status']=='ready' and out['research_eligible'] is True
    assert out['league_id']=='kbo' and out['sport']=='baseball'
    assert next(e for e in out['source_evidence'] if e['kind']=='baseline_reference')['freeze_hash']==x['baseline']['freeze_hash']
    assert out['source_evidence'][0]['received_at']=='2026-10-31T09:00:00+00:00'
    assert all(not e['external_receipt_verified'] for e in out['source_evidence'])
    assert build(x)['prediction_changed'] is False


def test_semantic_fingerprint_ignores_receipt_and_capture_but_not_fact_or_price():
    x=fixture();first=adapt(x);y=copy.deepcopy(x)
    for e in ('claim','official'): y['facts'][0][e]['received_at']='2026-10-31T09:05:00+00:00'
    y['market']['received_at']='2026-10-31T09:05:00+00:00'
    y['facts'][0]['market']['received_at']='2026-10-31T09:05:00+00:00'
    y['facts'][0]['revision']=2
    second=adapt(y);assert second['semantic_sha256']==first['semantic_sha256']
    assert second['source_evidence']!=first['source_evidence']
    assert second['source_evidence'][0]['revision']==2 and second['features']==first['features']
    assert second['features']==first['features']
    y=copy.deepcopy(x);y['facts'][0]['before_value']=None
    assert adapt(y)['semantic_sha256']!=first['semantic_sha256']
    y=copy.deepcopy(x);y['market']['odds']['home']=1.9;y['facts'][0]['market']['odds']['home']=1.9
    assert adapt(y)['semantic_sha256']!=first['semantic_sha256']


def test_dispatch_rejects_unconfirmed_and_incomplete_but_accepts_empty_as_baseline():
    x=fixture();x['facts'][0]['official']['confirmed']=False
    out=adapt(x);assert out['status']=='rejected' and out['research_eligible'] is False and out['features']=={}
    assert next(e for e in out['source_evidence'] if e['kind']=='official')['confirmed'] is False
    x=fixture();x['market']['odds'].pop('away')
    out=adapt(x);assert out['status']=='rejected' and out['research_eligible'] is False
    x=fixture();x['facts']=[];out=adapt(x)
    assert out['status']=='baseline_only' and out['research_eligible'] is False


def test_cli_writes_dispatch_inputs_and_does_not_override_bundle(tmp_path):
    output=tmp_path/'adapter-inputs.json'
    subprocess.run([sys.executable,str(ROOT/'experiments/fact-delta-research-adapter-20261031/run_fixture.py'),str(FIX),'--input-id','collector-1','--adapter-inputs',str(output)],capture_output=True,check=True)
    result=json.loads(output.read_text(encoding='utf8'));assert result[0]['status']=='ready' and result[0]['input_id']=='collector-1'
    p=subprocess.run([sys.executable,str(ROOT/'experiments/fact-delta-research-adapter-20261031/run_fixture.py'),str(FIX),'--adapter-inputs',str(FIX)],capture_output=True)
    assert p.returncode!=0


def test_workload_without_prior_boxscore_is_unknown():
    x=workload_fact();x['workloads']['f1']['records']=[];x['workloads']['f1']['expected_game_ids']=[];x['facts'][0]['after_value']=0
    with pytest.raises(InvalidFact):build(x)


def test_wrong_sport_or_league_cannot_create_dispatch_ready():
    x=fixture();x['facts'][0]['sport']='soccer'
    assert adapt(x)['status']=='rejected'
    x=fixture();x['registry']['league']='unknown'
    with pytest.raises(InvalidFact):adapt(x)


def test_cli_can_write_rejected_dispatch_record(tmp_path):
    x=fixture();x['facts'][0]['official']['confirmed']=False
    bundle=tmp_path/'raw.json';out=tmp_path/'adapter.json'
    bundle.write_text(json.dumps(x),encoding='utf8')
    subprocess.run([sys.executable,str(ROOT/'experiments/fact-delta-research-adapter-20261031/run_fixture.py'),str(bundle),'--adapter-inputs',str(out)],capture_output=True,check=True)
    record=json.loads(out.read_text(encoding='utf8'))[0]
    assert record['status']=='rejected' and not record['research_eligible']


def test_game_identity_and_player_mapping_affect_semantic_fingerprint():
    x=fixture();original=adapt(x)['semantic_sha256']
    x['registry']['kickoff_at']='2026-10-31T11:00:00+00:00';x['facts'][0]['kickoff_at']='2026-10-31T11:00:00+00:00'
    assert adapt(x)['semantic_sha256']!=original
    x=fixture();x['registry']['player_teams']['p-old']='A';x['registry']['player_teams']['p-new']='A'
    assert adapt(x)['semantic_sha256']!=original
