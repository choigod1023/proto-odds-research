import copy
import importlib.util
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]; P=ROOT/'experiments/live-source-audit-20261031/inspect_snapshot.py'
spec=importlib.util.spec_from_file_location('local_snapshot_audit',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
NOW='2026-10-31T00:00:00+00:00'
def source():
    row={'game_no':'1','date':'10.31 12:00','sport':'bs','league':'KBO','home':'A','away':'B','market':'승패','label':'','n_way':2,'odds':[1.5,2.4],'result':'secret outcome'}
    return {'generated_at':'2026-10-30T23:59:30+00:00','markets':{'5':{'1':row}},'odds':{'5':{'1':[1.5,2.4]}}}
def pack(x):return json.dumps(x,ensure_ascii=False).encode()
def test_local_snapshot_does_not_claim_live_or_leak_results():
    result=m.inspect_snapshot(pack(source()),local_read_at=NOW); assert len(result['records'])==1 and not result['stale'] and not result['live_eligible']
    assert not result['records'][0]['quote_eligible'] and result['records'][0]['provider_received_at'] is None
    assert 'result' not in result['records'][0] and 'secret outcome' not in json.dumps(result)
    altered=source();altered['markets']['5']['1']['result']='different';assert m.inspect_snapshot(pack(altered),local_read_at=NOW)['records']==result['records']
def test_identity_prices_timestamps_and_missing_data_fail_closed():
    x=source();x['odds']['5']['1']=[1.5,2.5];assert not m.inspect_snapshot(pack(x),local_read_at=NOW)['records']
    x=source();x['markets']['5']['1']['home']='B';assert not m.inspect_snapshot(pack(x),local_read_at=NOW)['records']
    x=source();x['markets']['5']['1']['odds']=[1.5,float('nan')];assert not m.inspect_snapshot(pack(x),local_read_at=NOW)['records']
    x=source();x['generated_at']='2026-10-31T00:00:01+00:00'
    with pytest.raises(ValueError,match='future_snapshot'):m.inspect_snapshot(pack(x),local_read_at=NOW)
    x['generated_at']='2026-10-31T00:00:00'
    with pytest.raises(ValueError,match='timezone_required'):m.inspect_snapshot(pack(x),local_read_at=NOW)
def test_last_good_is_preserved_for_failures_staleness_and_conflicts():
    raw=pack(source());prev=m.inspect_snapshot(raw,local_read_at=NOW)
    for other,now,reason in [(b'invalid',NOW,'invalid_json'),(raw,'2026-10-31T01:00:00+00:00','stale_snapshot')]:
        got,status=m.protect_previous(prev,other,local_read_at=now);assert got is prev and status['reason']==reason
    altered=source();altered['markets']['5']['1']['result']='different'
    got,status=m.protect_previous(prev,pack(altered),local_read_at=NOW);assert got is prev and status['reason']=='same_time_conflicting_snapshot'
def test_permission_rate_limit_and_backoff():
    assert m.safe_poll_decision(authorized=False,now=NOW)['reason']=='source_permission_unverified'
    assert m.safe_poll_decision(authorized=True,now=NOW,last_status='rate_limited')['reason']=='manual_review_required'
    assert m.safe_poll_decision(authorized=True,now=NOW,last_attempt='2026-10-30T23:59:00+00:00',last_status='timeout')['reason']=='backoff'
    assert m.safe_poll_decision(authorized=True,now=NOW,last_attempt='2026-10-30T23:50:00+00:00',last_status='timeout')['allow']
    with pytest.raises(ValueError):m.safe_poll_decision(authorized=True,now=NOW,minimum_interval=1)
def test_saved_snapshot_replay_metrics():
    output=json.loads((ROOT/'experiments/live-source-audit-20261031/local-inspection-summary.json').read_text(encoding='utf8'))
    assert output['record_count']==172 and output['stale'] and output['live_eligible'] is False
    assert output['official_product_id_verified'] is False and output['source_raw_http_available'] is False
