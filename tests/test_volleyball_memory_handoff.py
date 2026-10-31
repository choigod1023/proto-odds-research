import threading
from types import SimpleNamespace

from deploy import supervisor


def test_saved_decision_waits_for_active_warmer_and_never_recollects(monkeypatch):
    heavy = threading.Lock()
    pending = threading.Event()
    waiting = threading.Event()
    finished = threading.Event()
    outcomes, events = [], []
    monkeypatch.setattr(supervisor, '_memory_heavy_lock', heavy)
    monkeypatch.setattr(supervisor, '_market_cycle_pending', pending)
    monkeypatch.setattr(supervisor, '_odds_recommendation_lock', threading.Lock())
    monkeypatch.setattr(supervisor, 'MARKET_HANDOFF_WAIT_SECONDS', 2)
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 700)
    monkeypatch.setattr(supervisor, '_run_market_refresh', lambda: events.append('decision') or SimpleNamespace(returncode=0))
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('recollection')))
    monkeypatch.setattr(supervisor, 'log', lambda message: waiting.set() if '슬롯 대기' in message else None)
    heavy.acquire()
    def run():
        outcomes.append(supervisor._run_odds_cycle(['unused'], market_retry=True))
        finished.set()
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert waiting.wait(1) and pending.is_set()
        assert events == [] and not finished.is_set()
    finally:
        heavy.release()
        worker.join(3)
    assert not worker.is_alive() and outcomes == [(0, False)]
    assert events == ['decision'] and not pending.is_set() and not heavy.locked()


def test_wait_timeout_retries_and_preserves_reservation_without_interrupting_worker(monkeypatch):
    heavy = threading.Lock()
    pending = threading.Event()
    monkeypatch.setattr(supervisor, '_memory_heavy_lock', heavy)
    monkeypatch.setattr(supervisor, '_market_cycle_pending', pending)
    monkeypatch.setattr(supervisor, '_odds_recommendation_lock', threading.Lock())
    monkeypatch.setattr(supervisor, 'MARKET_HANDOFF_WAIT_SECONDS', 0.01)
    monkeypatch.setattr(supervisor, '_run_market_refresh', lambda: (_ for _ in ()).throw(AssertionError('decision ran')))
    with heavy:
        assert supervisor._run_odds_cycle(['unused'], market_retry=True) == (1, True)
        assert heavy.locked() and pending.is_set()
    assert not heavy.locked()


def test_memory_gate_still_refuses_after_lock_becomes_available(monkeypatch):
    heavy = threading.Lock()
    waiting = threading.Event()
    result = []
    monkeypatch.setattr(supervisor, '_memory_heavy_lock', heavy)
    monkeypatch.setattr(supervisor, 'MARKET_HANDOFF_WAIT_SECONDS', 2)
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 470)
    monkeypatch.setattr(supervisor, '_release_match_working_set', lambda: None)
    monkeypatch.setattr(supervisor, 'release_unused_heap', lambda: None)
    monkeypatch.setattr(supervisor, 'log', lambda message: waiting.set() if '슬롯 대기' in message else None)
    monkeypatch.setattr(supervisor.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unsafe child')))
    heavy.acquire()
    worker = threading.Thread(target=lambda: result.append(supervisor._refresh_market_after_collection()))
    worker.start()
    try:
        assert waiting.wait(1)
    finally:
        heavy.release()
        worker.join(3)
    assert not worker.is_alive() and result == [1] and not heavy.locked()
