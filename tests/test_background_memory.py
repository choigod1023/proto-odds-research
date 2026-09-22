from types import SimpleNamespace
import threading

import pytest
from deploy import supervisor


@pytest.fixture
def gate(monkeypatch):
    lock = threading.Lock()
    monkeypatch.setattr(supervisor, '_memory_heavy_lock', lock)
    monkeypatch.setattr(supervisor, 'BACKGROUND_MIN_AVAILABLE_MB', 512)
    monkeypatch.setattr(supervisor, 'log', lambda message: None)
    return lock


def test_memory_reader(tmp_path, monkeypatch):
    path = tmp_path/'meminfo'
    path.write_text('MemFree: 123 kB\nMemAvailable: 524288 kB\n')
    monkeypatch.setattr(supervisor, 'Path', lambda *args: path)
    assert supervisor._available_memory_mb() == 512
    path.write_text('MemAvailable: invalid kB\n')
    assert supervisor._available_memory_mb() is None


def test_background_waits_without_holding_lock_or_starting_work(gate, monkeypatch):
    readings = iter([84, None, 511, 512])
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: next(readings))
    sleeps = []
    def sleep(seconds):
        assert not gate.locked()
        sleeps.append(seconds)
    monkeypatch.setattr(supervisor.time, 'sleep', sleep)
    with supervisor._background_slot('test'):
        assert gate.locked()
        assert sleeps == [30, 30, 30]
    assert not gate.locked()


def test_background_releases_slot_after_failure(gate, monkeypatch):
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 600)
    with pytest.raises(RuntimeError):
        with supervisor._background_slot('test'):
            raise RuntimeError('worker failure')
    assert not gate.locked()


@pytest.mark.parametrize('name', ['실시간 배당', '배당 스냅샷'])
def test_realtime_collectors_bypass_background_gate(gate, monkeypatch, name):
    calls = []
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: pytest.fail('realtime gated'))
    monkeypatch.setattr(supervisor.subprocess, 'run',
                        lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0))
    def stop(seconds):
        raise StopIteration
    monkeypatch.setattr(supervisor.time, 'sleep', stop)
    with pytest.raises(StopIteration):
        supervisor.run_looper(name, ['collector'], 60)
    assert len(calls) == 1


def test_migration_waits_before_start(gate, monkeypatch):
    readings = iter([0, 600])
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: next(readings))
    events = []
    monkeypatch.setattr(supervisor.time, 'sleep', lambda n: events.append('wait'))
    def run(*args, **kwargs):
        assert gate.locked()
        events.append('run')
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')
    monkeypatch.setattr(supervisor, 'sh', run)
    supervisor.run_database_migration()
    assert events == ['wait', 'run']


def test_publish_steps_wait_and_preserve_order(gate, monkeypatch):
    readings = iter([80, 600, 100, 600])
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: next(readings))
    calls = []
    monkeypatch.setattr(supervisor.time, 'sleep', lambda n: calls.append('wait'))
    def run(cmd, **kwargs):
        assert gate.locked()
        calls.append(cmd[0])
        return SimpleNamespace(returncode=0, stdout='ok', stderr='')
    monkeypatch.setattr(supervisor.subprocess, 'run', run)
    supervisor._run_steps([('first', ['a'], True, 10), ('second', ['b'], False, 10)])
    assert calls == ['wait', 'a', 'wait', 'b']
