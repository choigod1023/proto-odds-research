"""Synthetic fixtures exercise status decisions; they are not live evidence."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import pytest

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
    assert out['sources']['common_comparison_ledger'] == 'read'
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


# Synthetic lifecycle: bridge is a deterministic stand-in; production uses its
# independently implemented archive/parser/price checks, never this fixture.
def _lifecycle(tmp_path, monkeypatch):
    import hashlib
    import sys
    from types import SimpleNamespace
    from research.kbo_forward.operating_vote_bridge import _receipt_matches
    now = datetime.now(timezone.utc).replace(microsecond=0)
    frozen = now-timedelta(hours=5)
    kickoff = now-timedelta(hours=3)
    local = kickoff.astimezone(official.KST)
    event = {'official_game_id': 'fixture', 'season': 2026, 'home_id': 'SS', 'away_id': 'KT', 'kickoff_at': kickoff.isoformat()}
    market = {'kind': 'full_game_2way', 'settlement_rule': 'draw_void_refund', 'market_id': 'G101:260130:100', 'odds': {'home': '1.8', 'away': '1.9'}}
    payload = {'event': event, 'row': {}, 'market': market, 'evidence': {}}
    models = {'m0_actual': {'p': 1}}
    monkeypatch.setattr(v.ledger, 'validate_model_snapshot', lambda *a, **k: None)
    monkeypatch.setattr(observer, '_verify_archives', lambda *a: None)
    monkeypatch.setattr(observer, '_verify_market_bytes', lambda *a: None)
    monkeypatch.setattr(observer, 'forecast', lambda row: models)
    monkeypatch.setattr(observer, 'metadata', lambda: {'version': 1})
    m1 = tmp_path/'m1.jsonl'; common = tmp_path/'common.jsonl'
    m0 = observer._append(m1, lambda rows: {'type': 'capture', 'schema': 'm1-pregame-rehearsal-v1', 'captured_at': frozen.isoformat(),
        'input': payload, 'input_sha256': observer.digest(observer.canonical(payload)), 'models': models, 'model_metadata': {'version': 1}, 'roi_eligible': True})
    selections = {'m0_actual': 'home', 'operating_vote': 'away', 'market': 'home'}
    raw = json.dumps({'selections': selections, 'odds': market['odds']}).encode()
    export_hash = hashlib.sha256(raw).hexdigest()
    (tmp_path/'export.json').write_bytes(raw)
    receipt = {'transport': 'flyctl_machine_exec', 'exit_code': 0, 'remote_exit_code': 0,
               'machine_id': '12345678abcdef00',
               'received_at': (frozen+timedelta(minutes=10)).isoformat(), 'export_sha256': export_hash}
    (tmp_path/'receipt.json').write_text(json.dumps(receipt))
    def load(config, events, timestamp):
        b = Path(config['export_path']).read_bytes()
        return {'export_sha256': hashlib.sha256(b).hexdigest(), 'data': json.loads(b)}
    def match(cap, export, timestamp):
        return {'status': 'eligible', 'official_game_id': 'fixture', 'market_id': market['market_id'],
                'operating_record_hash': 'b'*64, 'operating_as_of': (frozen+timedelta(minutes=5)).isoformat(),
                'shared_prices': export['data']['odds'], 'three_selections': export['data']['selections'],
                'official_raw_price_verified': False, 'comparison_kind': 'operating_price_shadow'}
    monkeypatch.setitem(sys.modules, 'research.kbo_forward.operating_vote_bridge', SimpleNamespace(
        load_operating_export=load, match_operating=match, _receipt_matches=_receipt_matches))
    # Explicit import handles namespace packages in both branch versions.
    from research import kbo_forward
    monkeypatch.setattr(kbo_forward, 'operating_vote_bridge', sys.modules['research.kbo_forward.operating_vote_bridge'], raising=False)
    key = observer.digest(observer.canonical({'official_game_id': 'fixture', 'season': 2026}))
    cap = observer._append(common, lambda rows: {'type': 'paired_capture', 'schema': v.SCHEMA, 'comparison_key': key,
        'captured_at': (frozen+timedelta(minutes=15)).isoformat(), 'event': event, 'm0_capture_hash': m0['hash'],
        'operating_record_hash': 'b'*64, 'market': market, 'selections': selections,
        'export_sha256': export_hash, 'export_path': str(tmp_path/'export.json'), 'source_receipt_path': str(tmp_path/'receipt.json'), 'source_receipt': receipt})
    stamp = kickoff+timedelta(hours=1)
    game = {'G_ID': 'fixture', 'HOME_ID': 'SS', 'AWAY_ID': 'KT', 'SEASON_ID': 2026, 'G_DT': local.strftime('%Y%m%d'),
            'GAME_STATE_SC': '3', 'GAME_RESULT_CK': '1', 'CANCEL_SC_ID': '0', 'B_SCORE_CN': 3, 'T_SCORE_CN': 1}
    body = {'game': [game]}; ev = {'uri': official.SCHEDULE, 'received_at': stamp.isoformat(), 'body': body, 'sha256': observer.digest(observer.canonical(body))}
    parent = observer._append(m1, lambda rows: {'type': 'settlement', 'capture_hash': m0['hash'], 'evidence': ev,
                                                  'economics': {'status': 'final', 'home_score': 3, 'away_score': 1}})
    profits = {'m0_actual': {'selection': 'home', 'net_units': '0.8', 'refund': False, 'hit': True},
               'operating_vote': {'selection': 'away', 'net_units': '-1', 'refund': False, 'hit': False},
               'market': {'selection': 'home', 'net_units': '0.8', 'refund': False, 'hit': True}}
    settlement = {'type': 'paired_settlement', 'schema': v.SCHEMA, 'comparison_key': key, 'capture_hash': cap['hash'],
        'parent_m0_settlement_hash': parent['hash'], 'recorded_at': stamp.isoformat(), 'evidence': ev, 'supersedes_hash': None,
        'economics': {'status': 'final', 'winner': 'home', 'home_score': 3, 'away_score': 1, 'profits': profits}}
    observer._append(common, lambda rows: settlement)
    args = {'m1_ledger': m1, 'common_ledger': common}
    return args, (frozen-timedelta(seconds=1)).isoformat(), cap, settlement


def test_replayed_common_and_settlement_are_first_shadow_only(tmp_path, monkeypatch):
    args, since, cap, settlement = _lifecycle(tmp_path, monkeypatch)
    result = v.inspect('fixture', 2026, since, **args)
    assert result['stages']['operating_same_price']['complete']
    assert result['stages']['official_settled']['complete']
    assert result['first_common_shadow']['price_source']['official_raw_operating_price_verified'] is False
    assert not result['final_operating_performance_verified'] and not result['formal_promotion_eligible']
    assert not result['complete']  # independent schedule evidence not supplied


def test_missing_or_modified_export_cannot_be_replaced_by_flags(tmp_path, monkeypatch):
    args, since, cap, settlement = _lifecycle(tmp_path, monkeypatch)
    (tmp_path/'export.json').write_bytes(b'{}')
    result = v.inspect('fixture', 2026, since, **args)
    assert not result['stages']['operating_same_price']['complete']
    assert not result['stages']['official_settled']['complete']


def test_economics_and_result_evidence_cannot_be_forged(tmp_path, monkeypatch):
    args, since, cap, settlement = _lifecycle(tmp_path, monkeypatch)
    m1 = observer.read(args['m1_ledger'])
    settlement['economics']['profits']['market']['net_units'] = '9'
    with pytest.raises(ValueError, match='profit'):
        v._common_settlement(settlement, cap, m1, datetime.now(timezone.utc))
    settlement['economics']['profits']['market']['net_units'] = '0.8'
    settlement['evidence']['body']['game'][0]['B_SCORE_CN'] = 8
    with pytest.raises(ValueError, match='evidence'):
        v._common_settlement(settlement, cap, m1, datetime.now(timezone.utc))


def test_capture_rejects_unlinked_m0_and_wrong_selection(tmp_path, monkeypatch):
    args, since, cap, settlement = _lifecycle(tmp_path, monkeypatch)
    m0 = observer.read(args['m1_ledger'])[0]
    cap['selections']['market'] = 'away'
    with pytest.raises(ValueError, match='bridge differs'):
        v._common_capture(cap, m0, tmp_path, None, None, datetime.now(timezone.utc))
    cap['m0_capture_hash'] = 'f'*64
    with pytest.raises(ValueError, match='M0 or event'):
        v._common_capture(cap, m0, tmp_path, None, None, datetime.now(timezone.utc))


@pytest.mark.parametrize('kind', ['draw', 'cancelled'])
def test_official_draw_and_cancellation_refund(tmp_path, monkeypatch, kind):
    args, since, cap, settlement = _lifecycle(tmp_path, monkeypatch)
    from copy import deepcopy
    m1 = deepcopy(observer.read(args['m1_ledger']))
    settlement = deepcopy(settlement)
    ev = settlement['evidence']; game = ev['body']['game'][0]
    if kind == 'draw':
        game['B_SCORE_CN'] = game['T_SCORE_CN'] = 2
        status, winner, home, away = 'final', 'draw', 2, 2
    else:
        game['CANCEL_SC_ID'] = '1'; game['CANCEL_SC_NM'] = '우천취소'
        status, winner, home, away = 'cancelled', None, None, None
    ev['sha256'] = observer.digest(observer.canonical(ev['body']))
    m1[-1]['evidence'] = deepcopy(ev)
    m1[-1]['economics'] = {'status': status, 'home_score': home, 'away_score': away}
    settlement['economics'].update(status=status, winner=winner, home_score=home, away_score=away)
    for profit in settlement['economics']['profits'].values():
        profit.update(net_units='0', refund=True, hit=None)
    assert v._common_settlement(settlement, cap, m1, datetime.now(timezone.utc))


def test_archive_receipt_mismatch_and_path_escape_fail(tmp_path, monkeypatch):
    args, since, cap, settlement = _lifecycle(tmp_path, monkeypatch)
    m0 = observer.read(args['m1_ledger'])[0]
    cap['source_receipt']['transport'] = 'forged'
    with pytest.raises(ValueError, match='receipt differ'):
        v._common_capture(cap, m0, tmp_path, None, None, datetime.now(timezone.utc))
    cap['export_path'] = str(tmp_path.parent/'outside')
    with pytest.raises(ValueError, match='outside state'):
        v._common_capture(cap, m0, tmp_path, None, None, datetime.now(timezone.utc))



def test_latest_invalid_correction_blocks_earlier_settlement(tmp_path, monkeypatch):
    from copy import deepcopy
    args, since, cap, settlement = _lifecycle(tmp_path, monkeypatch)
    prior = observer.read(args['common_ledger'])[-1]
    bad = deepcopy(settlement)
    bad.pop('hash'); bad.pop('previous_hash')
    bad['supersedes_hash'] = prior['hash']
    bad['economics']['profits']['market']['net_units'] = '999'
    observer._append(args['common_ledger'], lambda rows: bad)
    result = v.inspect('fixture', 2026, since, **args)
    assert result['stages']['operating_same_price']['complete']
    assert not result['stages']['official_settled']['complete']


def test_duplicate_common_candidate_blocks_even_if_other_one_valid(tmp_path, monkeypatch):
    from copy import deepcopy
    args, since, cap, settlement = _lifecycle(tmp_path, monkeypatch)
    duplicate = deepcopy(cap)
    duplicate.pop('hash'); duplicate.pop('previous_hash')
    duplicate['m0_capture_hash'] = 'f'*64
    observer._append(args['common_ledger'], lambda rows: duplicate)
    result = v.inspect('fixture', 2026, since, **args)
    assert not result['stages']['operating_same_price']['complete']
