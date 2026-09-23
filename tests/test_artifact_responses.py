import gzip
import json
import threading
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from src.artifact_responses import ArtifactResponses
from src.runtime_db import RuntimeDatabase


def test_compressed_only_and_revision_refresh(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    db.store_artifact('sample', {'generated_at': 'old', 'history': ['abc'] * 10000})
    cache = ArtifactResponses(db)
    first = cache.get_bytes('sample')
    assert len(cache.ready['sample']) == 2
    assert len(first) < len(gzip.decompress(first)) / 10
    assert cache.get_bytes('sample', compressed=False) == gzip.decompress(first)
    original = db.get_artifact_json
    monkeypatch.setattr(db, 'get_artifact_json', lambda _: (_ for _ in ()).throw(AssertionError('reread')))
    assert cache.get_bytes('sample') == first
    monkeypatch.setattr(db, 'get_artifact_json', original)
    db.store_artifact('sample', {'generated_at': 'new'})
    with db.connect() as c:
        c.execute("UPDATE artifacts SET stored_at='next' WHERE name='sample'")
    assert json.loads(gzip.decompress(cache.get_bytes('sample'))) == {'generated_at': 'new'}


def test_single_flight_stale_and_cold_requests(tmp_path, monkeypatch):
    import pytest
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    db.store_artifact('sample', {'generated_at': 'old'})
    cache = ArtifactResponses(db)
    first = cache.get_bytes('sample')
    with db.connect() as c:
        c.execute("UPDATE artifacts SET stored_at='next' WHERE name='sample'")
    entered, release = threading.Event(), threading.Event()
    calls = []
    def blocked(name):
        calls.append(name)
        entered.set()
        assert release.wait(5)
        raise OSError('read failure')
    monkeypatch.setattr(db, 'get_artifact_json', blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(cache.get_bytes, 'sample')
        try:
            assert entered.wait(5)
            for _ in range(20):
                assert cache.get_bytes('sample') == first
            with pytest.raises(KeyError): cache.get_bytes('cold')
            assert calls == ['sample']
        finally:
            release.set()
        assert future.result() == first
    assert cache.ready['sample'][0] != 'next'


def test_proto_labels_skip_prediction_objects(tmp_path, monkeypatch):
    from src import live_scores
    db = RuntimeDatabase(tmp_path / 'runtime.db')
    monkeypatch.setenv('PROODD_DB_PATH', str(db.path))
    expected = {'sport': 'bs', 'date': '09.22(화) 18:00', 'home': '홈', 'away': '원정'}
    db.store_artifact('picks_v2', {'generated_at': 'old',
        'live': [dict(expected, options=[{'nested': ['x'] * 10000}])],
        'past': [expected], 'prediction_performance': {'records': ['y'] * 10000}})
    monkeypatch.setattr(live_scores, 'load_artifact', lambda *a: (_ for _ in ()).throw(AssertionError('full load')))
    assert live_scores._proto_games() == [expected, expected]
    assert RuntimeDatabase(tmp_path / 'empty.db').proto_team_labels() == []
