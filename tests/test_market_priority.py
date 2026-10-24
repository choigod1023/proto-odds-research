from types import SimpleNamespace
import subprocess
import sys
import threading
import pytest
from deploy import supervisor


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(supervisor, '_market_cycle_pending', threading.Event())
    monkeypatch.setattr(supervisor, '_memory_heavy_lock', threading.Lock())
    monkeypatch.setattr(supervisor, '_odds_recommendation_lock', threading.Lock())
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 700)


@pytest.mark.parametrize('exception', [False, True])
def test_saved_prices_retry_without_scraping_or_losing_priority(monkeypatch, exception):
    collected = []
    monkeypatch.setattr(supervisor.subprocess, 'run',
        lambda *a, **kw: collected.append(a) or SimpleNamespace(returncode=0))
    def fail():
        assert supervisor._market_cycle_pending.is_set()
        if exception:
            raise TimeoutError()
        return 1
    monkeypatch.setattr(supervisor, '_refresh_market_after_collection', fail)
    assert supervisor._run_odds_cycle(['odds']) == (1, True)
    assert supervisor._market_cycle_pending.is_set()
    monkeypatch.setattr(supervisor, '_refresh_market_after_collection', lambda: 0)
    assert supervisor._run_odds_cycle(['odds'], True) == (0, False)
    assert len(collected) == 1
    assert not supervisor._market_cycle_pending.is_set()


def test_optional_work_yields_even_with_enough_memory(monkeypatch):
    supervisor._market_cycle_pending.set()
    waits = []
    def release(_):
        assert not supervisor._memory_heavy_lock.locked()
        waits.append(1)
        supervisor._market_cycle_pending.clear()
    monkeypatch.setattr(supervisor.time, 'sleep', release)
    with supervisor._background_slot('optional'):
        assert waits == [1]
        assert supervisor._memory_heavy_lock.locked()


@pytest.mark.parametrize('raises', [False, True])
def test_failed_collection_does_not_reserve_optional_jobs_forever(monkeypatch, raises):
    def run(*a, **kw):
        if raises:
            raise OSError()
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(supervisor.subprocess, 'run', run)
    if raises:
        with pytest.raises(OSError):
            supervisor._run_odds_cycle(['odds'])
    else:
        assert supervisor._run_odds_cycle(['odds']) == (1, False)
    assert not supervisor._market_cycle_pending.is_set()


def test_priority_reserved_before_waiting_for_snapshot_lock(monkeypatch):
    class Slot:
        def __enter__(self):
            assert supervisor._market_cycle_pending.is_set()
        def __exit__(self, *args): pass
    monkeypatch.setattr(supervisor, '_odds_recommendation_lock', Slot())
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(supervisor, '_refresh_market_after_collection', lambda: 0)
    assert supervisor._run_odds_cycle(['odds']) == (0, False)


def test_hung_collection_is_reaped_and_releases_decision_lock(monkeypatch, tmp_path):
    # Exercise an actual child: a mocked TimeoutExpired would not prove the
    # caller supplied a deadline or that subprocess.run reaped the child.
    monkeypatch.setattr(supervisor, 'ODDS_COLLECTION_TIMEOUT', 0.2, raising=False)
    monkeypatch.setattr(supervisor, 'REPO', tmp_path)
    monkeypatch.setattr(supervisor, '_refresh_market_after_collection',
                        lambda: pytest.fail('failed collection must not publish'))
    with pytest.raises(subprocess.TimeoutExpired):
        supervisor._run_odds_cycle([sys.executable, '-c', 'import time; time.sleep(1)'])
    assert not supervisor._market_cycle_pending.is_set()
    assert supervisor._odds_recommendation_lock.acquire(blocking=False)
    supervisor._odds_recommendation_lock.release()


def test_collection_timeout_retries_instead_of_ending_looper(monkeypatch):
    deadlines = []
    def timed_out(*args, **kwargs):
        deadlines.append(kwargs.get('timeout'))
        raise subprocess.TimeoutExpired(args[0], kwargs.get('timeout'))
    monkeypatch.setattr(supervisor.subprocess, 'run', timed_out)
    sleeps = []
    def stop(seconds):
        sleeps.append(seconds)
        raise StopIteration
    monkeypatch.setattr(supervisor.time, 'sleep', stop)
    with pytest.raises(StopIteration):
        supervisor.run_looper('실시간 배당', ['odds'], 60)
    assert deadlines == [900]
    assert sleeps == [60]
    assert not supervisor._market_cycle_pending.is_set()
    assert not supervisor._odds_recommendation_lock.locked()
