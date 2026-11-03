import gzip
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT=Path('experiments/kleague1-official-lineup-20261103')
BASE=ROOT/'season-2025'

def read_json(p):return json.loads(p.read_text(encoding='utf8'))
def sha(b):return hashlib.sha256(b).hexdigest()


def test_official_denominator_and_schedule_bytes():
    plan=read_json(BASE/'plan.json');logs=read_json(BASE/'schedule-requests.json')
    assert (plan['season'] if 'season' in plan else plan['year'],plan['scheduled'],plan['completed'])==(2025,228,228)
    assert len(plan['targets'])==len({x['fixture_id'] for x in plan['targets']})==228
    assert len(logs)==10 and all(x['status']==200 for x in logs)
    for e in logs:
        p=BASE/'raw'/f"schedule-2025-{e['payload']['month']}.json"
        assert sha(p.read_bytes())==e['sha256']


def test_full_season_fixture_and_raw_timeline_integrity():
    latest=read_json(BASE/'latest.json');snapshot=BASE/latest['snapshot'];mb=(snapshot/'manifest.json').read_bytes()
    assert sha(mb)==latest['manifest_sha256'];m=json.loads(mb)
    assert (m['scheduled'],m['lineup_and_result_verified'],m['pending_count'],m['failed_count'])==(228,228,0,0)
    assert m['historical_actual_pregame_receipt_verified']==0
    fb=(snapshot/'fixtures.jsonl').read_bytes();tb=(snapshot/'timeline.jsonl').read_bytes()
    assert sha(fb)==m['fixtures_sha256'] and sha(tb)==m['timeline_sha256']
    fs=[json.loads(x) for x in fb.decode('utf8').splitlines()];ts=[json.loads(x) for x in tb.decode('utf8').splitlines()]
    assert len(fs)==len(ts)==len({f['fixture_id'] for f in fs})==228
    assert all(len(s['starters'])==11 for f in fs for s in f['lineups'])
    plan={r['fixture_id']:r for r in read_json(BASE/'plan.json')['targets']}
    for f in fs:
        t=plan[f['fixture_id']]; sides=f['lineups']; score=f['scores']
        assert (f['day'],sides[0]['team_id'],sides[1]['team_id'],score['home'],score['away'])==(t['day'],t['home_team_id'],t['away_team_id'],t['home_goal'],t['away_goal'])
        assert all(set(player)=={'player_id','name','shirt_number','position'} for side in sides for player in side['starters']+side['bench'])
    assert all(f['announced_at'] is None and f['actual_pregame_receipt'] is None for f in fs)
    goals=[]
    for t in ts:
        source=json.loads((BASE/t['source_evidence_id']).read_bytes())['data']
        assert t['event_fields_are_retrospective_not_pregame_features'] is True
        assert t['periods']=={k:source.get(k) for k in ('firstHalf','secondHalf','EfirstHalf','EsecondHalf')}
        goals.extend(e for period in t['periods'].values() for e in (period or []) if e.get('eventName')=='득점')
    assert goals and all('timeMin' in e and 'timeSec' in e and 'playerId' in e for e in goals)


def test_request_log_source_integrity_and_pilot_reuse():
    logs=read_json(BASE/'requests.json');assert len(logs)==456
    assert sum(x['method']=='GET' for x in logs)==228
    assert sum(x['method']=='POST' for x in logs)==228
    for e in logs:
        body=(BASE/e['raw_file']).read_bytes()
        if e['raw_file'].endswith('.gz'):body=gzip.decompress(body)
        assert e['status']==200 and e['received_at'] and sha(body)==e['sha256']
    originals=read_json(ROOT/'requests.json')
    pilot=[x for x in originals if x.get('raw_file') in {'raw/match-2025-96.html.gz','raw/info-2025-96.json'}]
    assert len(pilot)==2 and all(e in logs for e in pilot)


def test_2026_original_artifact_hashes_preserved():
    subprocess.run(['python',str(ROOT/'verify_artifacts.py')],check=True,capture_output=True)


def test_unlogged_response_blocks_overwrite(tmp_path,monkeypatch):
    import importlib.util
    import pytest
    spec=importlib.util.spec_from_file_location('lineup_2025_runner',BASE/'run.py')
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    monkeypatch.setattr(runner,'BASE',tmp_path)
    (tmp_path/'raw').mkdir();(tmp_path/'requests.json').write_text('[]',encoding='utf8')
    (tmp_path/'raw'/'match-2025-1.html.gz').write_bytes(b'orphan')
    with pytest.raises(ValueError,match='unlogged response'):runner.reject_unlogged_responses()


def test_request_log_replaced_atomically(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('lineup_2025_runner_atomic',BASE/'run.py')
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    p=tmp_path/'requests.json';runner.atomic_log(p,[{'status':200}])
    assert read_json(p)==[{'status':200}] and not (tmp_path/'requests.json.tmp').exists()


def test_completed_season_resume_uses_cache_without_network(monkeypatch):
    import importlib.util
    import pytest
    spec=importlib.util.spec_from_file_location('lineup_2025_resume',BASE/'run.py')
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    def unexpected(*args,**kwargs):pytest.fail('completed fixture must use cache')
    monkeypatch.setattr(runner.collector.requests,'request',unexpected)
    plan=read_json(BASE/'plan.json')
    runner.reject_unlogged_responses()
    runner.collector.capture([(2025,int(r['game_id'])) for r in plan['targets']],228)
