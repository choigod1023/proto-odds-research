import importlib.util,json
from pathlib import Path

P=Path(__file__).resolve().parents[1]/'experiments/fact-pipeline-independent-review-20261031'
s=importlib.util.spec_from_file_location('facts_fixture_runner',P/'run_fixtures.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

def scenarios():return json.loads((P/'conformance-scenarios.json').read_text(encoding='utf8'))['cases']

def test_scenarios_have_unique_id_and_cover_user_boundaries():
    c=scenarios(); assert len(c)==37 and len({x['id'] for x in c})==len(c)
    ids={x['id'] for x in c}
    assert {'manifest_generated_at_only','manifest_existing_445','capture_received_after_decision','queue_retry','queue_injection','adapter_result_feature','capture_raw_hash_unwitnessed'}<=ids

def test_boolean_does_not_equal_integer_and_missing_is_failure():
    c={'expected':{'enqueue_count':1,'eligible':False}}
    assert m.check(c,{'enqueue_count':1,'eligible':False})==[]
    assert len(m.check(c,{'enqueue_count':True}))==2

def test_runner_uses_stdin_no_shell_and_rejects_false_observation(tmp_path):
    driver=tmp_path/'driver.py';driver.write_text("import json,sys\nx=json.load(sys.stdin)\nprint(json.dumps({'planned_count':1,'messages_sent':0,'enqueue_count':0,'reason':'invalid_identifier'}))\n",encoding='utf8')
    scenario=next(x for x in scenarios() if x['id']=='queue_injection')
    result=m.invoke(driver,scenario,5)
    assert result['status']=='fail' and any('planned_count' in x for x in result['differences'])

def test_invalid_json_or_driver_failure_does_not_count_as_pass(tmp_path):
    driver=tmp_path/'driver.py';driver.write_text("print('not json')\n",encoding='utf8')
    assert m.invoke(driver,scenarios()[0],5)['status']=='invalid_json'
    driver.write_text('raise SystemExit(7)\n',encoding='utf8')
    assert m.invoke(driver,scenarios()[0],5)['status']=='driver_error'
