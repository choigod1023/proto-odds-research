from types import SimpleNamespace
import ctypes
import pytest
from deploy import supervisor


def test_heap_release_only_unused_pages(monkeypatch):
    monkeypatch.setattr(supervisor.sys, 'platform', 'linux')
    calls = []
    def trim(pad):
        calls.append(pad)
        return 1
    monkeypatch.setattr(ctypes, 'CDLL', lambda name: SimpleNamespace(malloc_trim=trim))
    assert supervisor.release_unused_heap()
    assert calls == [0]
    assert trim.argtypes == [ctypes.c_size_t]


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
def test_unsupported_heap_release_is_optional(monkeypatch, platform):
    monkeypatch.setattr(supervisor.sys, 'platform', platform)
    monkeypatch.setattr(ctypes, 'CDLL', lambda *a: pytest.fail('not supported'))
    assert supervisor.release_unused_heap() is False


def test_non_glibc_linux_is_safe(monkeypatch):
    monkeypatch.setattr(supervisor.sys, 'platform', 'linux')
    monkeypatch.setattr(ctypes, 'CDLL', lambda name: SimpleNamespace())
    assert supervisor.release_unused_heap() is False


def test_release_precedes_admission(monkeypatch):
    calls = []
    monkeypatch.setattr(supervisor, '_release_match_working_set', lambda: calls.append('drop'))
    monkeypatch.setattr(supervisor, 'release_unused_heap', lambda: calls.append('release'))
    def available():
        assert calls == ['drop', 'release']
        calls.append('measure')
        return 511
    monkeypatch.setattr(supervisor, '_available_memory_mb', available)
    assert supervisor._run_market_refresh().returncode == 1
    assert calls == ['drop', 'release', 'measure']


def test_publish_finishes_before_score_and_background_slots_are_released(monkeypatch):
    import threading
    score = threading.Lock()
    heavy = threading.Lock()
    calls = []
    monkeypatch.setattr(supervisor, '_score_refresh_lock', score)
    monkeypatch.setattr(supervisor, '_memory_heavy_lock', heavy)
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 600)
    monkeypatch.setattr(supervisor, 'release_unused_heap', lambda: None)
    monkeypatch.setattr(supervisor, '_release_match_working_set', lambda: None)
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **k:
                        calls.append('prediction') or SimpleNamespace(returncode=0))
    def publish():
        assert score.locked() and heavy.locked()
        assert calls == ['prediction']
        calls.append('published')
    monkeypatch.setattr(supervisor, '_publish_match_responses', publish)
    assert supervisor._refresh_market_after_collection() == 0
    assert calls == ['prediction', 'published']
    assert not score.locked() and not heavy.locked()


def test_failed_prediction_does_not_publish_and_failed_publish_retries(monkeypatch):
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 600)
    monkeypatch.setattr(supervisor, 'release_unused_heap', lambda: None)
    monkeypatch.setattr(supervisor, '_publish_match_responses', lambda: pytest.fail('must not publish'))
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    assert supervisor._run_market_refresh().returncode == 1
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))
    def failed_publish():
        raise RuntimeError('rebuild failed')
    monkeypatch.setattr(supervisor, '_publish_match_responses', failed_publish)
    assert supervisor._run_market_refresh().returncode == 1
    assert not supervisor._score_refresh_lock.locked()
