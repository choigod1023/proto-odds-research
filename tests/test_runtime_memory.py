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
