from datetime import datetime
import json
import subprocess
import threading
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from isolated_match_responses import IsolatedMatchResponses
from match_api import KST, MatchViews, PreparedMatchResponses, game_key
from runtime_db import RuntimeDatabase


def fixture(tmp_path):
    database = RuntimeDatabase(tmp_path / 'runtime.sqlite3')
    today = datetime.now(KST).strftime('%m.%d') + ' 23:50'
    games = [dict(year=datetime.now(KST).year, round=125, date=today,
                  sport=sport, home=sport + '-home', away='away',
                  options=[{'배당': 1.9, '확률': .55}],
                  prediction_record={'prediction_snapshot_id': 'frozen-' + sport},
                  선발={'home_detail': {'name': 'pitcher', 'history': ['x'] * 200}})
             for sport in ('sc', 'bs', 'bk', 'vl')]
    document = {'generated_at': '2026-10-18T11:00:00Z', 'live': games, 'past': []}
    database.store_artifact('picks_v2', document)
    return database, document


def test_real_child_matches_old_wire_and_preserves_frozen_details(tmp_path, monkeypatch):
    database, document = fixture(tmp_path)
    baseline = PreparedMatchResponses(MatchViews(database))
    baseline.requested.add('all')
    baseline.refresh()
    isolated = IsolatedMatchResponses(MatchViews(database))
    isolated.requested.add('all')
    monkeypatch.setattr(database, 'get_artifact_json', lambda *a:
                        pytest.fail('parent must not expand picks'))
    isolated.refresh()
    assert isolated.views.payload is None
    assert isolated.views.cache == isolated.views.wire_cache == {}
    for scope in ('recent', 'all'):
        assert isolated.get_bytes(scope, compressed=False) == baseline.get_bytes(scope, compressed=False)
    assert {g['sport'] for g in json.loads(isolated.get_bytes(compressed=False))['live']} == {'sc', 'bs', 'bk', 'vl'}
    for game in document['live']:
        detail = json.loads(isolated.get_bytes('detail', key=game_key(game),
                            revision=isolated.views.revision, compressed=False))
        assert detail['game'] == game
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('unchanged revision'))
    isolated.refresh()


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'incomplete'])
def test_failure_keeps_last_good_list_and_detail(tmp_path, monkeypatch, failure):
    database, document = fixture(tmp_path)
    prepared = IsolatedMatchResponses(MatchViews(database))
    prepared.refresh()
    old = prepared.get_bytes()
    revision = prepared.views.revision
    document['generated_at'] = '2026-10-18T11:03:00Z'
    database.store_artifact('picks_v2', document)
    def fail(*args, **kwargs):
        assert kwargs == {'check': True, 'timeout': 180}
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('worker', 180)
        if failure == 'exit':
            raise subprocess.CalledProcessError(1, 'worker')
        return None
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'run', fail)
        with pytest.raises((subprocess.SubprocessError, FileNotFoundError)):
            prepared.refresh()
    assert prepared.get_bytes() == old and prepared.views.revision == revision
    prepared.get_bytes('detail', key=game_key(document['live'][0]), revision=revision)
    prepared.refresh()
    assert json.loads(prepared.get_bytes(compressed=False))['generated_at'] == document['generated_at']


def test_scope_requested_during_build_is_not_lost(tmp_path, monkeypatch):
    database, document = fixture(tmp_path)
    prepared = IsolatedMatchResponses(MatchViews(database))
    run = subprocess.run
    def request_during_build(*args, **kwargs):
        with pytest.raises(KeyError):
            prepared.get_bytes('all')
        return run(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'run', request_during_build)
        prepared.refresh()
    assert 'all' in prepared.requested and 'all' not in prepared.ready
    prepared.refresh()
    assert json.loads(prepared.get_bytes('all', compressed=False))['live']


def test_single_child_for_concurrent_refreshes(tmp_path, monkeypatch):
    database, _ = fixture(tmp_path)
    prepared = IsolatedMatchResponses(MatchViews(database))
    run = subprocess.run
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return run(*args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', counted)
    threads = [threading.Thread(target=prepared.refresh) for _ in range(3)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(30)
    assert all(not thread.is_alive() for thread in threads)
    assert calls == [1]
    assert prepared.ready


def test_worker_rejects_mixed_scope_revisions(tmp_path, monkeypatch):
    from match_response_worker import build
    database, _ = fixture(tmp_path)
    def mixed(self):
        self.published_revisions = {'recent': 'old', 'all': 'new'}
        self.views.revision = 'new'
    monkeypatch.setattr(PreparedMatchResponses, 'refresh', mixed)
    output = tmp_path / 'output.pickle'
    with pytest.raises(RuntimeError, match='revision changed'):
        build(database.path, output, ['recent', 'all'])
    assert not output.exists()


def test_restart_serves_original_list_and_details_without_db_or_child(tmp_path, monkeypatch):
    database, document = fixture(tmp_path)
    path = tmp_path / 'responses.zip'
    before = IsolatedMatchResponses(MatchViews(database), path)
    before.requested.add('all')
    before.refresh()
    monkeypatch.setattr(database, 'artifact_metadata', lambda *a, **k: pytest.fail('DB not ready'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('no child during restore'))
    after = IsolatedMatchResponses(MatchViews(database), path)
    for scope in ('recent', 'all'):
        assert before.get_bytes(scope) == after.get_bytes(scope)
    key = game_key(document['live'][0])
    assert before.get_bytes('detail', key=key, revision=before.views.revision) == after.get_bytes(
        'detail', key=key, revision=after.views.revision)
    assert after.views.payload is None


@pytest.mark.parametrize('invalid', ['corrupt', 'yesterday', 'version', 'mixed'])
def test_invalid_restart_cache_is_not_served(tmp_path, invalid):
    from match_response_snapshot import save
    database, _ = fixture(tmp_path)
    path = tmp_path / 'responses.zip'
    before = IsolatedMatchResponses(MatchViews(database), path)
    before.refresh()
    if invalid == 'corrupt':
        path.write_bytes(b'broken zip')
    elif invalid == 'yesterday':
        ready = {k: ('2000-01-01', v[1]) for k, v in before.ready.items()}
        save(path, ready, before.published_revisions, before.views.revision, before.views.games)
    elif invalid == 'mixed':
        save(path, before.ready, {'recent': 'other'}, before.views.revision, before.views.games)
    else:
        import match_response_snapshot
        old = match_response_snapshot.VERSION
        try:
            match_response_snapshot.VERSION = -1
            save(path, before.ready, before.published_revisions, before.views.revision, before.views.games)
        finally:
            match_response_snapshot.VERSION = old
    restored = IsolatedMatchResponses(MatchViews(database), path)
    with pytest.raises(KeyError):
        restored.get_bytes()
    restored.refresh()
    assert restored.get_bytes()


def test_disk_failure_does_not_break_live_publish_or_last_snapshot(tmp_path, monkeypatch):
    import match_response_snapshot
    database, document = fixture(tmp_path)
    path = tmp_path / 'responses.zip'
    prepared = IsolatedMatchResponses(MatchViews(database), path)
    prepared.refresh()
    previous = path.read_bytes()
    document['generated_at'] = '2026-10-18T11:04:00Z'
    database.store_artifact('picks_v2', document)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(match_response_snapshot.os, 'replace', fail)
    prepared.refresh()
    assert json.loads(prepared.get_bytes(compressed=False))['generated_at'] == document['generated_at']
    assert path.read_bytes() == previous
    assert list(tmp_path.glob('responses.zip*')) == [path]
