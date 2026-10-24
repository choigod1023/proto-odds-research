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


def test_retry_window_deadline_backoff_and_reset():
    window = supervisor.MarketRetryWindow()
    assert not window.expired(9999)
    window.failed(0)
    assert window.delay == 60
    assert not window.expired(599)
    assert window.expired(600)
    window.failed(10)
    assert window.delay == 120
    window.failed(20)
    assert window.delay == 180
    assert window.expired(20)
    window.reset()
    assert not window.expired(10000)
    window.failed(10000)
    assert window.delay == 60
    assert not window.expired(10001)


@pytest.mark.parametrize('elapsed, expected_sleeps', [
    (0, [60, 120, 180, 60]),  # bounded attempts even when the clock is fixed
    (600, [60, 60, 60, 60]),  # TTL independently forces a fresh collection
])
def test_persistent_judgement_failure_cannot_starve_collection(
        monkeypatch, elapsed, expected_sleeps):
    collected, sleeps = [], []
    clock = [0]
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(supervisor.subprocess, 'run',
        lambda *a, **kw: collected.append(a) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(supervisor, '_refresh_market_after_collection', lambda: 1)
    def step(seconds):
        sleeps.append(seconds)
        clock[0] += elapsed
        assert not supervisor._odds_recommendation_lock.locked()
        if len(sleeps) == 4:
            raise StopIteration
    monkeypatch.setattr(supervisor.time, 'sleep', step)
    with pytest.raises(StopIteration):
        supervisor.run_looper('실시간 배당', ['odds'], 60)
    assert sleeps == expected_sleeps
    assert len(collected) == (2 if elapsed == 0 else 4)


def test_success_resets_retry_backoff(monkeypatch):
    outcomes = iter([1, 0, 1])
    sleeps, collected = [], []
    monkeypatch.setattr(supervisor.subprocess, 'run',
        lambda *a, **kw: collected.append(a) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(supervisor, '_refresh_market_after_collection', lambda: next(outcomes))
    monkeypatch.setattr(supervisor, '_refresh_recommendation_after_odds', lambda **kw: True)
    def step(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise StopIteration
    monkeypatch.setattr(supervisor.time, 'sleep', step)
    with pytest.raises(StopIteration):
        supervisor.run_looper('실시간 배당', ['odds'], 60)
    assert sleeps == [60, 60, 60]
    assert len(collected) == 2
