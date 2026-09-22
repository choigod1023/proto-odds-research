import gzip
import json
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
import threading

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import match_api
from runtime_db import RuntimeDatabase
from deploy import supervisor


def test_wire_cache_coalesces_requests_and_preserves_plain_response(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path/'test.sqlite3')
    db.store_artifact('picks_v2', {'live': [], 'past': [], 'generated_at': 'original'})
    views = match_api.MatchViews(db)
    original = gzip.compress
    calls = []
    def compress(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(match_api.gzip, 'compress', compress)
    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(lambda _: views.get_bytes(), range(16)))
    assert len(calls) == 1
    assert all(body is bodies[0] for body in bodies)
    assert views.get_bytes(compressed=False) == gzip.decompress(bodies[0])
    assert json.loads(gzip.decompress(bodies[0])) == views.get()
    monkeypatch.setattr(db, 'get_artifact_json', lambda *a: pytest.fail('full reread on hit'))
    assert views.get_bytes() is bodies[0]


def test_wire_cache_revision_day_and_detail_validation(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path/'test.sqlite3')
    game = {'year': 2026, 'date': '09.22(화) 18:00', 'home': 'A', 'away': 'B', 'options': []}
    db.store_artifact('picks_v2', {'live': [game]})
    views = match_api.MatchViews(db)
    class Clock(datetime):
        offset = 0
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 22, tzinfo=match_api.KST) + timedelta(days=cls.offset)
    monkeypatch.setattr(match_api, 'datetime', Clock)
    initial = json.loads(views.get_bytes(compressed=False))
    views.get_bytes('all')
    assert len(views.wire_cache) == 2
    Clock.offset = 3
    assert json.loads(views.get_bytes(compressed=False))['live'] == []
    assert len(views.wire_cache) == 1
    revision = initial['view']['revision']
    key = match_api.game_key(game)
    assert json.loads(views.get_bytes('detail', key, revision, False))['game'] == game
    db.store_artifact('picks_v2', {'live': [], 'generated_at': 'changed'})
    with db.connect() as connection:
        connection.execute("UPDATE artifacts SET stored_at='next' WHERE name='picks_v2'")
    with pytest.raises(ValueError): views.get_bytes('detail', key, revision)
    updated = json.loads(views.get_bytes(compressed=False))
    assert updated['generated_at'] == 'changed'
    assert updated['view']['revision'] == 'next'


def test_metadata_can_skip_payload_length(tmp_path):
    db = RuntimeDatabase(tmp_path/'test.sqlite3')
    db.store_artifact('picks_v2', {'live': []})
    full = db.artifact_metadata('picks_v2')
    small = db.artifact_metadata('picks_v2', include_size=False)
    assert small == {k: v for k, v in full.items() if k != 'payload_bytes'}
    assert db.artifact_metadata('missing', include_size=False) is None


@pytest.mark.parametrize('memory,called', [(100, False), (None, False), (256, True)])
def test_prewarming_skips_memory_pressure(monkeypatch, memory, called):
    stop = threading.Event()
    calls = []
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: memory)
    monkeypatch.setattr(stop, 'wait', lambda seconds: stop.set())
    supervisor.warm_match_views(SimpleNamespace(refresh=lambda: calls.append('recent')), stop)
    assert calls == (['recent'] if called else [])


def test_prewarming_survives_refresh_failure(monkeypatch):
    stop = threading.Event()
    messages = []
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 600)
    monkeypatch.setattr(supervisor, 'log', messages.append)
    monkeypatch.setattr(stop, 'wait', lambda seconds: stop.set())
    def fail():
        raise OSError('unavailable')
    supervisor.warm_match_views(SimpleNamespace(refresh=fail), stop)
    assert len(messages) == 1
