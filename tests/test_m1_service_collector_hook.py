"""Synthetic collector integration; no HTTP or real pregame performance claims."""
import importlib
from pathlib import Path

from research.m1_service.observer import read


def helpers():
    h = importlib.import_module('research.kbo_forward.test_runner')
    assert Path(h.runner.__file__).resolve() == Path(__file__).resolve().parents[1]/'research/kbo_forward/runner.py'
    return h


def test_same_verified_payload_goes_to_separate_model_ledger_and_restart_is_idempotent(tmp_path, monkeypatch):
    from research.m1_service import observer
    # This test's upstream Official is synthetic. Raw archive reconstruction
    # has separate tests; the price parser still uses real fixture bytes here.
    monkeypatch.setattr(observer, '_verify_archives', lambda *a: None)
    h = helpers(); x = h.payload('full_game_2way')
    x['event'].update(home_id='SS', away_id='HT'); x['row'].update(home_id='SS', away_id='HT')
    now = h.ledger.moment(x['event']['kickoff_at'])-h.timedelta(hours=2)
    source = h.Official(x)
    prices = lambda *args: {'quotes': [h.quote(x, tmp_path, now)], 'excluded': [], 'requests': []}
    state = tmp_path/'m1-separate'
    first = h.runner.run_once(tmp_path, source, prices, clock=lambda: now, infer=h.old.fake_infer, m1_state=state)
    assert first['captured_this_run'] == 1 and first['m1_status'] == 'recorded'
    assert first['m1_comparison']['captured'] == 1
    old_bytes = (tmp_path/'paired-ledger.jsonl').read_bytes()
    m1_bytes = (state/'predictions.jsonl').read_bytes()
    record = read(state/'predictions.jsonl')[0]
    assert set(record['models']) == {'m0_actual', 'm0_projected', 'm1_projected'}
    assert record['input']['event']['official_game_id'] == x['event']['official_game_id']
    assert record['production_policy_changed'] is False
    h.runner.run_once(tmp_path, source, prices, clock=lambda: now+h.timedelta(minutes=5), infer=h.old.fake_infer, m1_state=state)
    assert (tmp_path/'paired-ledger.jsonl').read_bytes() == old_bytes
    assert (state/'predictions.jsonl').read_bytes() == m1_bytes
    assert source.snapshot_calls == 1


def test_m1_failure_is_reported_without_losing_legacy_capture(tmp_path, monkeypatch):
    h = helpers(); x = h.payload('full_game_2way')
    x['event'].update(home_id='SS', away_id='HT'); x['row'].update(home_id='SS', away_id='HT')
    now = h.ledger.moment(x['event']['kickoff_at'])-h.timedelta(hours=2)
    prices = lambda *args: {'quotes': [h.quote(x, tmp_path, now)], 'excluded': [], 'requests': []}
    from research.m1_service.observer import Recorder
    def fail(*args, **kwargs):
        raise ValueError('M1 model unavailable')
    monkeypatch.setattr(Recorder, 'capture', fail)
    result = h.runner.run_once(tmp_path, h.Official(x), prices, clock=lambda: now, infer=h.old.fake_infer, m1_state=tmp_path/'m1')
    assert result['captured_this_run'] == 1 and result['status'] == 'ok'
    assert result['m1_status'] == 'blocked'
    assert result['m1_candidate_excluded'][0]['reason'] == 'M1 model unavailable'


def test_m1_import_failure_is_isolated_before_capture(tmp_path, monkeypatch):
    import builtins
    h = helpers(); x = h.payload('full_game_2way')
    x['event'].update(home_id='SS', away_id='HT'); x['row'].update(home_id='SS', away_id='HT')
    now = h.ledger.moment(x['event']['kickoff_at'])-h.timedelta(hours=2)
    prices = lambda *args: {'quotes': [h.quote(x, tmp_path, now)], 'excluded': [], 'requests': []}
    original_import = builtins.__import__
    def fail(name, *args, **kwargs):
        if name == 'research.m1_service.observer':
            raise ImportError('M1 import failed')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', fail)
    result = h.runner.run_once(tmp_path, h.Official(x), prices, clock=lambda: now, infer=h.old.fake_infer, m1_state=tmp_path/'m1')
    assert result['status'] == 'ok' and result['captured_this_run'] == 1
    assert result['m1_status'] == 'blocked'
    assert 'M1 import failed' in result['m1_candidate_excluded'][0]['reason']


def test_raw_price_body_prevents_edited_market_terms(tmp_path):
    from research.m1_service.observer import _verify_market_bytes
    import pytest
    h = helpers(); x = h.payload('full_game_2way')
    x['event'].update(home_id='SS', away_id='HT'); x['row'].update(home_id='SS', away_id='HT')
    now = h.ledger.moment(x['event']['kickoff_at'])-h.timedelta(hours=2)
    quote = h.quote(x, tmp_path, now)
    x['market'], x['evidence']['odds'] = h.runner.price_packet(quote, tmp_path, x['event'])
    root = tmp_path/'prices/price-evidence'
    _verify_market_bytes(x, root)
    x['market']['odds']['home'] = 1.20
    with pytest.raises(ValueError, match='market differs'):
        _verify_market_bytes(x, root)


def test_alternative_hook_failure_preserves_both_captures(tmp_path, monkeypatch):
    from research.m1_service import observer, alternative_observer
    monkeypatch.setattr(observer, '_verify_archives', lambda *a: None)
    h=helpers();x=h.payload('full_game_2way')
    x['event'].update(home_id='SS',away_id='HT');x['row'].update(home_id='SS',away_id='HT')
    now=h.ledger.moment(x['event']['kickoff_at'])-h.timedelta(hours=2)
    calls=[]
    def fail(recorder,capture,records,clock):
        calls.append(capture['hash'])
        raise ValueError('alternative source unavailable')
    monkeypatch.setattr(alternative_observer,'attach',fail)
    prices=lambda *args:{'quotes':[h.quote(x,tmp_path,now)],'excluded':[],'requests':[]}
    result=h.runner.run_once(tmp_path,h.Official(x),prices,clock=lambda:now,infer=h.old.fake_infer,m1_state=tmp_path/'candidate')
    assert result['status']=='ok' and result['captured_this_run']==1
    assert result['m1_status']=='recorded' and result['m1_comparison']['captured']==1
    assert len(calls)==1
    assert result['m1_alternative_reviews']==[{'recorded':False,'reason':'alternative source unavailable'}]
