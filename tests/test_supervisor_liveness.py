"""2026-11-19 13:46Z 이후 배당·점수는 갱신되는데 picks_v2만 멈춘 장애의 회귀 테스트."""
import subprocess
import threading
from types import SimpleNamespace

import pytest
from deploy import supervisor


class StopLoop(Exception):
    pass


@pytest.fixture
def quiet(monkeypatch):
    messages = []
    monkeypatch.setattr(supervisor, 'log', messages.append)
    monkeypatch.setattr(supervisor, '_memory_heavy_lock', threading.Lock())
    monkeypatch.setattr(supervisor, '_market_cycle_pending', threading.Event())
    monkeypatch.setattr(supervisor, '_tipster_wait_started', None)
    monkeypatch.setattr(supervisor, '_tailslips_wait_started', None)
    monkeypatch.setattr(supervisor, '_m0_wait_started', None)
    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 900)
    monkeypatch.setattr(supervisor, '_market_refresh_ok', None)
    monkeypatch.setattr(supervisor, '_odds_collection_ok', None)
    return messages


@pytest.mark.parametrize('name', ['선수·팀 정보', '무료 날씨', '무료 야구 컨텍스트'])
def test_hung_heavy_collector_cannot_hold_decision_slot_forever(quiet, monkeypatch, name):
    # 실제 실패 형태: 응답을 끝내지 않는 원천 때문에 자식이 끝나지 않으면
    # 공용 판정 슬롯이 영구히 잡혀 시장 판정이 게시되지 않았다.
    calls = []

    def hang(cmd, **kwargs):
        calls.append(kwargs)
        assert supervisor._memory_heavy_lock.locked()
        if kwargs.get('timeout') is None:
            raise AssertionError('heavy collector child has no overall deadline')
        raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])

    def stop(seconds):
        raise StopLoop

    monkeypatch.setattr(supervisor.subprocess, 'run', hang)
    monkeypatch.setattr(supervisor.time, 'sleep', stop)
    with pytest.raises(StopLoop):
        supervisor.run_looper(name, ['collector'], 1800)
    assert calls[0]['timeout'] == supervisor.BACKGROUND_CHILD_TIMEOUT
    assert supervisor.BACKGROUND_CHILD_TIMEOUT < 1800
    assert not supervisor._memory_heavy_lock.locked()
    assert any(f'{name} 종료(rc=-1)' in message for message in quiet)


def test_daily_timeout_releases_slot_and_continues_with_next_league(quiet, monkeypatch):
    attempted = []

    def run(cmd, **kwargs):
        attempted.append(cmd[-1])
        assert kwargs['timeout'] == supervisor.DAILY_CHILD_TIMEOUT
        if len(attempted) == 1:
            raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])
        return SimpleNamespace(returncode=0, stdout='ok\n')

    def sleep(seconds):
        assert not supervisor._memory_heavy_lock.locked()
        if seconds == supervisor.DAILY_EVERY:
            raise StopLoop

    monkeypatch.setattr(supervisor.subprocess, 'run', run)
    monkeypatch.setattr(supervisor.time, 'sleep', sleep)
    with pytest.raises(StopLoop):
        supervisor.run_daily()
    assert attempted == ['kleague1', 'kleague2', 'j1', 'j2']
    assert any('타임아웃' in message for message in quiet)


def test_market_success_is_recorded_only_after_publication(quiet, monkeypatch):
    monkeypatch.setattr(supervisor, 'release_unused_heap', lambda: None)
    monkeypatch.setattr(supervisor, '_release_match_working_set', lambda: None)
    monkeypatch.setattr(supervisor, '_publish_match_responses', lambda: None)
    monkeypatch.setattr(supervisor.subprocess, 'run',
                        lambda *a, **kw: subprocess.CompletedProcess(a, 1))
    supervisor._run_market_refresh()
    assert supervisor._market_refresh_ok is None

    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 300)
    assert supervisor._run_market_refresh().returncode == 1
    assert supervisor._market_refresh_ok is None

    monkeypatch.setattr(supervisor, '_available_memory_mb', lambda: 900)
    monkeypatch.setattr(supervisor.subprocess, 'run',
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    supervisor._run_market_refresh()
    assert supervisor._market_refresh_ok is not None
    assert supervisor._market_refresh_ok[1].endswith('+00:00')


def test_odds_success_is_recorded_before_market_handoff(quiet, monkeypatch):
    monkeypatch.setattr(supervisor, '_odds_recommendation_lock', threading.Lock())
    monkeypatch.setattr(supervisor, '_refresh_market_after_collection', lambda: 1)
    monkeypatch.setattr(supervisor.subprocess, 'run',
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    assert supervisor._run_odds_cycle(['odds']) == (1, True)
    assert supervisor._odds_collection_ok is not None
    assert supervisor._market_refresh_ok is None


def _at(monkeypatch, *, started, odds, market):
    monkeypatch.setattr(supervisor, '_supervisor_started', started)
    monkeypatch.setattr(supervisor, '_odds_collection_ok',
                        None if odds is None else (odds, 'odds'))
    monkeypatch.setattr(supervisor, '_market_refresh_ok',
                        None if market is None else (market, 'market'))


def test_stall_requires_fresh_odds_and_old_market_success(quiet, monkeypatch):
    limit = supervisor.MARKET_STALL_EXIT_SECONDS
    now = 100_000.0
    # 배당은 방금 성공했는데 판정은 기동 후 한 번도 성공하지 못했다.
    _at(monkeypatch, started=now - limit - 1, odds=now - 60, market=None)
    assert supervisor._market_stall_seconds(now) >= limit
    # 판정이 최근에 성공했다(변경 없음으로 picks를 다시 쓰지 않은 경우 포함).
    _at(monkeypatch, started=0.0, odds=now - 60, market=now - 600)
    assert supervisor._market_stall_seconds(now) is None
    # 원천 장애로 배당 수집이 실패 중이면 재시작해도 회복되지 않는다.
    _at(monkeypatch, started=0.0, odds=now - supervisor.MARKET_STALL_ODDS_FRESH_SECONDS - 1,
        market=None)
    assert supervisor._market_stall_seconds(now) is None
    _at(monkeypatch, started=0.0, odds=None, market=None)
    assert supervisor._market_stall_seconds(now) is None
    # 재시작 직후에는 이전 프로세스의 마지막 성공이 아니라 기동 시각부터 다시 잰다.
    _at(monkeypatch, started=now - 60, odds=now - 30, market=None)
    assert supervisor._market_stall_seconds(now) is None


def test_stall_monitor_exits_for_restart_policy(quiet, monkeypatch):
    now = 100_000.0
    _at(monkeypatch, started=now - supervisor.MARKET_STALL_EXIT_SECONDS - 5,
        odds=now - 60, market=None)
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: now)
    stop = threading.Event()
    stop.wait = lambda seconds: False
    exits = []
    supervisor.run_market_stall_monitor(stop, exit_process=exits.append)
    assert exits == [supervisor.MARKET_STALL_EXIT_CODE]
    assert any('감독자 종료' in message for message in quiet)


def test_stall_monitor_keeps_running_while_healthy(quiet, monkeypatch):
    now = 100_000.0
    _at(monkeypatch, started=0.0, odds=now - 60, market=now - 60)
    monkeypatch.setattr(supervisor.time, 'monotonic', lambda: now)
    checks = iter([False, False, True])
    stop = threading.Event()
    stop.wait = lambda seconds: next(checks)
    exits = []
    supervisor.run_market_stall_monitor(stop, exit_process=exits.append)
    assert exits == []


def test_main_starts_stall_monitor():
    import inspect
    assert 'run_market_stall_monitor' in inspect.getsource(supervisor.main)


def test_fly_restarts_machine_on_any_exit():
    import tomllib
    from pathlib import Path
    config = tomllib.loads((Path(supervisor.__file__).parent / 'fly.toml').read_text())
    assert config['restart'] == [{'policy': 'always'}]
    assert config['http_service']['min_machines_running'] == 1
    assert config['http_service']['auto_stop_machines'] is False
    assert config['http_service']['checks'][0]['path'] == '/health'
