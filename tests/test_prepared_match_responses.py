import gzip
import json
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import match_api
from runtime_db import RuntimeDatabase


def test_http_reads_do_not_wait_for_blocked_refresh():
    entered, release = threading.Event(), threading.Event()
    old = gzip.compress(b'{"generated_at":"original","live":[]}')
    new = gzip.compress(b'{"generated_at":"next","live":[]}')
    views = SimpleNamespace(get_bytes=lambda scope: old)
    cache = match_api.PreparedMatchResponses(views)
    cache.refresh()

    def blocked(scope):
        entered.set()
        assert release.wait(3)
        return new

    views.get_bytes = blocked
    worker = threading.Thread(target=cache.refresh)
    worker.start()
    try:
        assert entered.wait(1)
        # This is the production failure: a refresh holds the expensive view's
        # lock/DB, but all simultaneous HTTP reads must still return the old body.
        for _ in range(20):
            assert cache.get_bytes() is old
        assert json.loads(cache.get_bytes(compressed=False))['generated_at'] == 'original'
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert cache.get_bytes() is new


def test_cold_scope_failure_and_midnight_never_relabel_old_response(monkeypatch):
    class Clock(datetime):
        offset = 0
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 22, tzinfo=match_api.KST) + timedelta(days=cls.offset)
    monkeypatch.setattr(match_api, 'datetime', Clock)
    calls = []
    body = gzip.compress(b'{"generated_at":"original"}')
    views = SimpleNamespace(get_bytes=lambda scope: calls.append(scope) or body)
    cache = match_api.PreparedMatchResponses(views)
    with pytest.raises(KeyError): cache.get_bytes()
    assert calls == []  # cold requests do not trigger synchronous work
    cache.refresh()
    with pytest.raises(KeyError): cache.get_bytes('all')
    cache.refresh()
    assert calls == ['recent', 'recent', 'all']
    assert cache.get_bytes('all') is body
    def fail(scope): raise OSError('DB busy')
    views.get_bytes = fail
    with pytest.raises(OSError): cache.refresh()
    assert cache.get_bytes() is body
    Clock.offset = 1
    with pytest.raises(KeyError): cache.get_bytes()
    with pytest.raises(ValueError): cache.get_bytes('invalid')


def test_details_use_published_revision_without_database_read(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'views.sqlite3')
    game = {'home': 'A', 'away': 'B', 'options': [], 'prediction_record': {'result': 'hit'}}
    db.store_artifact('picks_v2', {'live': [game]})
    views = match_api.MatchViews(db)
    cache = match_api.PreparedMatchResponses(views)
    cache.refresh()
    monkeypatch.setattr(db, 'connect', lambda: pytest.fail('HTTP path touched DB'))
    result = json.loads(cache.get_bytes('detail', match_api.game_key(game), views.revision, False))
    assert result['game'] == game
    with pytest.raises(ValueError): cache.get_bytes('detail', match_api.game_key(game), 'old')
    with pytest.raises(KeyError): cache.get_bytes('detail', 'missing', views.revision)
    entered, release = threading.Event(), threading.Event()
    def lock_view():
        with views.lock:
            entered.set()
            release.wait(3)
    worker = threading.Thread(target=lock_view)
    worker.start()
    try:
        assert entered.wait(1)
        with pytest.raises(KeyError): cache.get_bytes('detail', match_api.game_key(game), views.revision)
        assert cache.get_bytes()
    finally:
        release.set()
        worker.join(3)
