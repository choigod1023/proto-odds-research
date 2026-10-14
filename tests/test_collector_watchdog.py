from datetime import datetime, timezone
from io import BytesIO
import json
import threading
from types import SimpleNamespace

import pytest
from src.collector_watchdog import check, diagnose
from deploy import supervisor

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def healthy():
    return dict(status='ok', disk_free_mb=1000, live_stored_at=NOW.isoformat(),
                odds_generated_at=NOW.isoformat(), picks_generated_at=NOW.isoformat())


def test_metadata_freshness_not_git_or_storage_clock():
    value = healthy()
    assert diagnose(value, NOW) == []
    value['picks_stored_at'] = NOW.isoformat()
    value['picks_generated_at'] = '2026-10-14T07:18:18+09:00'
    assert 'picks_generated_at' in diagnose(value, NOW)[0]


@pytest.mark.parametrize('bad', [None, '', 'bad', '2026-10-15T00:00:00', '2026-10-16T00:00:00Z'])
def test_missing_invalid_future_time_is_not_healthy(bad):
    value = healthy()
    value['picks_generated_at'] = bad
    assert diagnose(value, NOW)


def test_timeout_is_reported_instead_of_crashing():
    def fail(*a, **kw):
        raise TimeoutError()
    assert check('https://example.invalid', opener=fail) == ['health 조회 실패: TimeoutError']


def test_only_one_bounded_health_request():
    calls = []
    def get(url, timeout):
        calls.append((url, timeout))
        return BytesIO(json.dumps(healthy()).encode())
    assert check('https://example.invalid', opener=get, now=NOW) == []
    assert calls == [('https://example.invalid/health', 25)]
    assert check('x', opener=lambda *a, **k: BytesIO(b'x' * 65537))


def test_prediction_waits_for_score_exit_and_rechecks_memory(monkeypatch):
    slot = threading.Lock()
    started = threading.Event()
    launched = threading.Event()
    monkeypatch.setattr(supervisor, '_score_refresh_lock', slot)
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 600)
    def run(*a, **kw):
        assert slot.locked()
        assert kw['timeout'] == 300
        launched.set()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(supervisor.subprocess, 'run', run)
    def worker():
        started.set()
        supervisor._run_market_refresh()
    with slot:
        thread = threading.Thread(target=worker)
        thread.start()
        assert started.wait(1)
        assert not launched.wait(.05)
    thread.join(2)
    assert not thread.is_alive() and launched.is_set()
    assert not slot.locked()


def test_prediction_failure_releases_score_slot(monkeypatch):
    slot = threading.Lock()
    monkeypatch.setattr(supervisor, '_score_refresh_lock', slot)
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 511)
    assert supervisor._run_market_refresh().returncode == 1
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 600)
    def fail(*a, **kw):
        raise TimeoutError()
    monkeypatch.setattr(supervisor.subprocess, 'run', fail)
    with pytest.raises(TimeoutError):
        supervisor._run_market_refresh()
    assert not slot.locked()
