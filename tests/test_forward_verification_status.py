"""Synthetic fixtures exercise status decisions; they are not live evidence."""
from datetime import datetime, timedelta, timezone
import json

from research.kbo_forward import official, verification_status as v
from research.m1_service import observer


def schedule(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    kickoff = now + timedelta(days=1)
    local = kickoff.astimezone(official.KST)
    g = {'G_ID': 'fixture', 'G_DT': local.strftime('%Y%m%d'), 'G_TM': local.strftime('%H:%M'),
         'SEASON_ID': 2026, 'LE_ID': 1, 'GAME_STATE_SC': '1', 'CANCEL_SC_ID': '0'}
    raw = json.dumps({'code': '100', 'game': [g]}).encode()
    digest = official.sha(raw)
    state = tmp_path/'state'; (state/'bodies').mkdir(parents=True)
    (state/'bodies'/f'{digest}.bin').write_bytes(raw)
    receipt = {'url': official.SCHEDULE, 'status': 200, 'sha256': digest,
               'body_file': f'bodies/{digest}.bin', 'finished_at': now.isoformat(),
               'params': {'date': g['G_DT']}}
    path = tmp_path/'schedule.json'
    path.write_text(json.dumps({'future_scheduled': [{'game': g, 'receipt': receipt}]}))
    return path, state, now


def test_schedule_body_and_prospective_boundary(tmp_path):
    path, state, now = schedule(tmp_path)
    args = dict(schedule_check=path, schedule_state=state)
    assert v.inspect('fixture', 2026, (now-timedelta(seconds=1)).isoformat(), **args)['stages']['opportunity']['complete']
    assert not v.inspect('fixture', 2026, (now+timedelta(seconds=1)).isoformat(), **args)['stages']['opportunity']['complete']
    (next((state/'bodies').iterdir())).write_bytes(b'tampered')
    assert not v.inspect('fixture', 2026, (now-timedelta(seconds=1)).isoformat(), **args)['stages']['opportunity']['complete']


def test_missing_and_unconfirmed_common_stay_incomplete(tmp_path):
    common = tmp_path/'common.jsonl'
    observer._append(common, lambda rows: {'type': 'paired_settlement', 'comparison_key': 'x'})
    before = common.read_bytes()
    out = v.inspect('fixture', 2026, '2026-11-04T00:00:00+00:00', common_ledger=common)
    assert out['sources']['common_comparison_ledger'] == 'schema_unconfirmed'
    assert out['common_records_read'] == 1
    assert not out['complete'] and not out['stages']['operating_same_price']['complete']
    assert not out['stages']['official_settled']['complete']
    assert common.read_bytes() == before
    common.write_bytes(before.replace(b'paired_settlement', b'paired_capture'))
    assert v.inspect('fixture', 2026, '2026-11-04T00:00:00+00:00', common_ledger=common)['sources']['common_comparison_ledger'].startswith('invalid:')


def test_m1_requires_actual_and_prospective_verified_inputs(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    event = {'official_game_id': 'fixture', 'season': 2026, 'kickoff_at': (now+timedelta(hours=2)).isoformat()}
    payload = {'event': event, 'row': {}, 'evidence': {}}
    models = {'m0_actual': {'p': 1}}
    metadata = {'version': 1}
    monkeypatch.setattr(v.ledger, 'validate_model_snapshot', lambda *a, **k: None)
    monkeypatch.setattr(observer, '_verify_archives', lambda *a: None)
    monkeypatch.setattr(observer, 'forecast', lambda row: models)
    monkeypatch.setattr(observer, 'metadata', lambda: metadata)
    path = tmp_path/'m1.jsonl'
    record = {'type': 'capture', 'schema': 'm1-pregame-rehearsal-v1', 'captured_at': now.isoformat(),
              'input': payload, 'input_sha256': observer.digest(observer.canonical(payload)),
              'models': models, 'model_metadata': metadata, 'roi_eligible': False}
    observer._append(path, lambda rows: record)
    before = path.read_bytes()
    args = {'m1_ledger': path}
    out = v.inspect('fixture', 2026, (now-timedelta(seconds=1)).isoformat(), **args)
    assert out['stages']['captured']['complete'] and not out['complete']
    assert not v.inspect('fixture', 2026, (now+timedelta(seconds=1)).isoformat(), **args)['stages']['captured']['complete']
    monkeypatch.setattr(observer, '_verify_archives', lambda *a: (_ for _ in ()).throw(ValueError('missing archive')))
    assert not v.inspect('fixture', 2026, (now-timedelta(seconds=1)).isoformat(), **args)['stages']['captured']['complete']
    assert before == path.read_bytes()
