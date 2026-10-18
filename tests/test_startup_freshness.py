from types import SimpleNamespace
import threading
import pytest
from deploy import supervisor
from match_api import MatchViews, PreparedMatchResponses
from runtime_db import RuntimeDatabase


def test_http_binds_before_bootstrap_while_heavy_workers_are_blocked(monkeypatch):
    events = []
    lock = threading.Lock()
    monkeypatch.setattr(supervisor, '_memory_heavy_lock', lock)
    monkeypatch.setattr(supervisor.threading, 'Thread', lambda target, daemon:
                        SimpleNamespace(start=lambda: events.append('http')))
    def wait(timeout):
        assert events == ['http'] and timeout == 30 and lock.locked()
        events.append('bound')
        return True
    monkeypatch.setattr(supervisor, '_http_ready', SimpleNamespace(wait=wait))
    def migrate(*args, **kwargs):
        assert events == ['http', 'bound'] and lock.locked()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(supervisor, 'sh', migrate)
    assert supervisor.bootstrap_with_http().returncode == 0
    assert not lock.locked()


def test_collectors_do_not_start_duplicate_http_after_bootstrap(monkeypatch):
    started = []
    monkeypatch.setattr(supervisor.threading, 'Thread', lambda target, daemon:
                        SimpleNamespace(start=lambda: started.append(target)))
    monkeypatch.setattr(supervisor, '_initial_views_ready', SimpleNamespace(wait=lambda timeout: True))
    supervisor.start_realtime_workers(start_http=False)
    assert started == [supervisor.run_live, supervisor.run_recommendation_refresh]


@pytest.mark.parametrize('ready', [True, False])
def test_initial_api_preparation_precedes_collectors_with_bounded_failure(monkeypatch, ready):
    started = []
    def thread(target, daemon):
        return SimpleNamespace(start=lambda: started.append(target))
    def wait(timeout):
        assert timeout == 90
        assert started == [supervisor.serve_live]
        return ready
    monkeypatch.setattr(supervisor.threading, 'Thread', thread)
    monkeypatch.setattr(supervisor, '_initial_views_ready', SimpleNamespace(wait=wait))
    supervisor.start_realtime_workers()
    assert started == [supervisor.serve_live, supervisor.run_live, supervisor.run_recommendation_refresh]


@pytest.mark.parametrize('name,slot,timeout', [
    ('배당 스냅샷', '_odds_recommendation_lock', 600),
    ('KBO 선발·타순', '_score_refresh_lock', 180),
    ('선발 예고', '_score_refresh_lock', 180),
    ('해외 배당', '_score_refresh_lock', 180),
])
def test_auxiliary_collectors_cannot_overlap_decision(monkeypatch, name, slot, timeout):
    lock = threading.Lock()
    monkeypatch.setattr(supervisor, slot, lock)
    def run(*a, **kw):
        assert lock.locked()
        assert kw['timeout'] == timeout
        return SimpleNamespace(returncode=0)
    class Done(BaseException): pass
    monkeypatch.setattr(supervisor.subprocess, 'run', run)
    monkeypatch.setattr(supervisor.time, 'sleep', lambda _: (_ for _ in ()).throw(Done()))
    with pytest.raises(Done):
        supervisor.run_looper(name, ['fixture'], 60)
    assert not lock.locked()


def test_empty_kbo_poll_does_not_rebuild_released_picks(tmp_path, monkeypatch):
    db = RuntimeDatabase(tmp_path / 'test.db')
    db.store_artifact('picks_v2', {'generated_at': '2026-10-15T00:00:00Z', 'live': [], 'past': []})
    db.put_document('kbo_pregame', {'generated_at': '2026-10-15T00:00:00Z', 'games': []})
    views = MatchViews(db)
    cache = PreparedMatchResponses(views)
    cache.refresh()
    old = cache.get_bytes()
    views.release_working_set()
    db.put_document('kbo_pregame', {'generated_at': '2026-10-15T00:05:00Z', 'games': []})
    monkeypatch.setattr(db, 'get_artifact_json', lambda *_: pytest.fail('unnecessary full decode'))
    cache.refresh()
    assert cache.get_bytes() == old
    assert views.payload is None
