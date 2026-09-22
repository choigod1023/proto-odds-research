import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import runtime_db
import pickster_eval
import baseball_live_features


def test_incremental_events_and_latest_keep_order(tmp_path, monkeypatch):
    db = runtime_db.RuntimeDatabase(tmp_path/'events.sqlite3')
    records = [{'observed_at': stamp, 'value': value} for stamp, value in
               [(None, 0), ('2026-09-22T01:00:00Z', 1),
                ('2026-09-22T01:00:00Z', 2), ('2026-09-21T01:00:00Z', 3)]]
    db.append_events('test', records)
    expected = db.events('test')
    assert [x['value'] for x in expected] == [0, 3, 1, 2]
    assert db.latest_event('test') == expected[-1]
    assert db.latest_event('missing') is None
    assert list(db.iter_events('test', through='2026-09-21T01:00:00Z')) == [records[3]]
    loads = json.loads
    decoded = []
    def counted(value):
        decoded.append(value)
        return loads(value)
    monkeypatch.setattr(runtime_db.json, 'loads', counted)
    rows = db.iter_events('test')
    assert decoded == []
    assert next(rows) == expected[0]
    assert len(decoded) == 1
    rows.close()


def test_consumers_avoid_bulk_history_and_preserve_eligibility(tmp_path, monkeypatch):
    db = runtime_db.RuntimeDatabase(tmp_path/'events.sqlite3')
    for module in (pickster_eval, baseball_live_features):
        monkeypatch.setattr(module, 'database_enabled', lambda: True)
        monkeypatch.setattr(module, 'RuntimeDatabase', lambda: db)
    monkeypatch.setattr(db, 'events', lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('bulk history must not be used')))
    db.append_events('pickster_leaderboard', [
        {'observed_at': '2026-09-21', 'rows': [{'invalid': 'old'}]},
        {'observed_at': '2026-09-22', 'rows': []}])
    assert pickster_eval._leaderboard()['reason'] == '유효 행 없음'
    db.append_events('pickster_crowd', [
        {'observed_at': '2026-09-21', 'games': [{'old': True}]},
        {'observed_at': '2026-09-22', 'games': [{'new': True}]}])
    assert baseball_live_features._latest_crowd() == [{'new': True}]
    db.append_events('pickster_pick_events', [
        {'observed_at': '2026-09-21', 'identity_version': 2, 'pick_id': 'p',
         'event_type': 'first_observed', 'eligible_pre_event': True},
        {'observed_at': '2026-09-22', 'identity_version': 2, 'pick_id': 'p',
         'event_type': 'result', 'eligible_pre_event': False, 'result': 'W'}])
    row = pickster_eval._latest_picks()['p']
    assert row['eligible_pre_event'] is True and row['result'] == 'W'
    db.append_events('baseball_context_events', [
        {'observed_at': '2026-09-21', 'game_id': 'g', 'n': 1},
        {'observed_at': '2026-09-22', 'game_id': 'g', 'n': 2}])
    assert baseball_live_features._latest_context()[0]['n'] == 2


def test_file_stream_skips_invalid_lines(tmp_path, monkeypatch):
    path = tmp_path/'events.jsonl'
    path.write_text('{"n":1}\ninvalid\n\n{"n":2}\n', encoding='utf-8')
    monkeypatch.setattr(pickster_eval, 'database_enabled', lambda: False)
    monkeypatch.setattr(baseball_live_features, 'database_enabled', lambda: False)
    assert list(pickster_eval._jsonl(path)) == [{'n': 1}, {'n': 2}]
    assert list(baseball_live_features._jsonl(path, 'unused')) == [{'n': 1}, {'n': 2}]
