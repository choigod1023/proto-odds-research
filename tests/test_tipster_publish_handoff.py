from types import SimpleNamespace
import threading
import pytest
from deploy import supervisor as s


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(s, '_memory_heavy_lock', threading.Lock())
    monkeypatch.setattr(s, '_odds_recommendation_lock', threading.Lock())
    monkeypatch.setattr(s, '_market_cycle_pending', threading.Event())
    monkeypatch.setattr(s, '_tipster_publish_pending', threading.Event())
    monkeypatch.setattr(s, '_tipster_wait_started', None)
    monkeypatch.setattr(s, '_odds_yielded_tipster_waits', {})
    monkeypatch.setattr(s, '_tailslips_wait_started', None)
    monkeypatch.setattr(s, '_available_memory_mb', lambda: 700)


@pytest.mark.parametrize('name', ['공개 픽스터', '다종목 픽스터'])
def test_changed_evidence_publishes_inside_same_admission(monkeypatch, name):
    revisions = iter([None, ('new', '2026-10-28T07:00:00Z', 'first_observed')])
    monkeypatch.setattr(s, '_tipster_revision', lambda n: next(revisions))
    order = []
    def collect(cmd, **kw):
        assert s._memory_heavy_lock.locked()
        assert kw['timeout'] == 600
        order.append('collector_exit')
        return SimpleNamespace(returncode=0)
    def publish():
        assert order == ['collector_exit']
        assert s._memory_heavy_lock.locked()
        assert s._tipster_publish_pending.is_set()
        order.append('publish')
        s._tipster_publish_pending.clear()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(s.subprocess, 'run', collect)
    monkeypatch.setattr(s, '_run_market_refresh', publish)
    with s._memory_heavy_lock:
        assert s._collect_tipster_and_publish(name, ['collector']) == 0
    assert order == ['collector_exit', 'publish']


def test_unchanged_evidence_does_not_add_expensive_refresh(monkeypatch):
    monkeypatch.setattr(s, '_tipster_revision', lambda n: ('same', 'time', 'first_observed'))
    monkeypatch.setattr(s.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(s, '_run_market_refresh', lambda: pytest.fail('no changed input'))
    assert s._collect_tipster_and_publish('공개 픽스터', ['collector']) == 0


def test_failed_handoff_retains_signal_and_bypasses_old_cooldown(monkeypatch):
    revisions = iter([None, ('new', 'time', 'first_observed')])
    monkeypatch.setattr(s, '_tipster_revision', lambda n: next(revisions))
    monkeypatch.setattr(s.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(s, '_run_market_refresh', lambda: SimpleNamespace(returncode=1))
    assert s._collect_tipster_and_publish('공개 픽스터', ['collector']) == 0
    assert s._tipster_publish_pending.is_set()
    monkeypatch.setattr(s, '_next_recommendation_refresh', float('inf'))
    calls = []
    monkeypatch.setattr(s, '_refresh_recommendation_in_slot', lambda **kw: calls.append(kw) or True)
    assert s._refresh_recommendation_after_odds()
    assert calls == [{'repair': True}]


def test_oldest_collector_wins_and_new_odds_yields_but_retry_does_not(monkeypatch):
    monkeypatch.setattr(s.time, 'monotonic', lambda: 200.)
    monkeypatch.setattr(s, '_tailslips_wait_started', 0.)
    monkeypatch.setattr(s, '_tipster_wait_started', 30.)
    assert s._preferred_tipster() == '공개 픽스터'
    # A retry window may have expired immediately before yielding a new poll.
    s._market_cycle_pending.set()
    monkeypatch.setattr(s.subprocess, 'run', lambda *a, **kw: pytest.fail('new collection must yield'))
    assert s._run_odds_cycle(['odds']) == (0, False)
    assert not s._market_cycle_pending.is_set()
    monkeypatch.setattr(s, '_refresh_market_after_collection', lambda: 0)
    assert s._run_odds_cycle(['odds'], market_retry=True) == (0, False)


def test_tailslips_gets_the_reserved_slot_and_clears_it_on_failure(monkeypatch):
    monkeypatch.setattr(s.time, 'monotonic', lambda: 200.)
    monkeypatch.setattr(s, '_tailslips_wait_started', 0.)
    with s._background_slot('공개 픽스터'):
        assert s._memory_heavy_lock.locked()
    monkeypatch.setattr(s, '_collect_tipster_and_publish', lambda *a: (_ for _ in ()).throw(TimeoutError()))
    def stop(seconds):
        assert seconds == 900
        assert s._tailslips_wait_started is None
        raise StopIteration
    monkeypatch.setattr(s.time, 'sleep', stop)
    with pytest.raises(StopIteration):
        s.run_looper('공개 픽스터', ['collector'], 900)


def test_only_successful_publication_acknowledges_changed_inputs(monkeypatch):
    monkeypatch.setattr(s, '_score_refresh_lock', threading.Lock())
    monkeypatch.setattr(s, 'release_unused_heap', lambda: True)
    monkeypatch.setattr(s, '_release_match_working_set', lambda: None)
    monkeypatch.setattr(s.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    s._tipster_publish_pending.set()
    def fail(): raise RuntimeError('publication failed')
    monkeypatch.setattr(s, '_publish_match_responses', fail)
    assert s._run_market_refresh().returncode == 1
    assert s._tipster_publish_pending.is_set()
    monkeypatch.setattr(s, '_publish_match_responses', lambda: None)
    assert s._run_market_refresh().returncode == 0
    assert not s._tipster_publish_pending.is_set()


def test_low_memory_wait_yields_once_then_odds_continue_and_next_wait_gets_a_turn(monkeypatch):
    monkeypatch.setattr(s.time, 'monotonic', lambda: 500.)
    monkeypatch.setattr(s, '_available_memory_mb', lambda: 300)
    monkeypatch.setattr(s, '_tipster_wait_started', 100.)
    calls = []
    monkeypatch.setattr(s.subprocess, 'run', lambda *a, **kw: calls.append('odds') or SimpleNamespace(returncode=0))
    monkeypatch.setattr(s, '_refresh_market_after_collection', lambda: 0)
    assert s._run_odds_cycle(['odds']) == (0, False)
    assert calls == []
    assert s._run_odds_cycle(['odds']) == (0, False)
    assert s._run_odds_cycle(['odds']) == (0, False)
    assert calls == ['odds', 'odds']
    # A new waiting episode is entitled to one opportunity again.
    s._tipster_wait_started = 200.
    assert s._run_odds_cycle(['odds']) == (0, False)
    assert calls == ['odds', 'odds']
    assert s._run_odds_cycle(['odds']) == (0, False)
    assert calls == ['odds', 'odds', 'odds']


def test_persistent_wait_gets_bounded_repeat_opportunity_without_interrupting_retry(monkeypatch):
    now=[500.]
    monkeypatch.setattr(s.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(s,'_tipster_wait_started',100.)
    calls=[]
    monkeypatch.setattr(s.subprocess,'run',lambda *a,**k: calls.append('odds') or SimpleNamespace(returncode=0))
    monkeypatch.setattr(s,'_refresh_market_after_collection',lambda:0)
    assert s._run_odds_cycle(['odds'])==(0,False) and not calls
    now[0]+=899
    assert s._run_odds_cycle(['odds'])==(0,False) and calls==['odds']
    now[0]+=1
    assert s._run_odds_cycle(['odds'],market_retry=True)==(0,False) and calls==['odds']
    assert s._run_odds_cycle(['odds'])==(0,False) and calls==['odds']
    assert s._run_odds_cycle(['odds'])==(0,False) and calls==['odds','odds']

def test_low_headroom_reclaims_only_when_preferred_and_still_requires_gate(monkeypatch):
    now=[200.]; readings=iter([300,400,511,512]); events=[]
    monkeypatch.setattr(s.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(s,'_tipster_wait_started',0.)
    monkeypatch.setattr(s,'_available_memory_mb',lambda:next(readings))
    monkeypatch.setattr(s,'_release_match_working_set',lambda:events.append('drop'))
    monkeypatch.setattr(s,'release_unused_heap',lambda:events.append('trim'))
    def sleep(seconds):
        assert not s._memory_heavy_lock.locked()
        now[0]+=seconds
    monkeypatch.setattr(s.time,'sleep',sleep)
    with s._background_slot('다종목 픽스터'):
        assert events==['drop','trim'] and s._memory_heavy_lock.locked()
    assert now[0]==260.

def test_reclaim_failure_does_not_bypass_headroom_or_hold_lock(monkeypatch):
    now=[200.]; readings=iter([300,600]); sleeps=[]
    monkeypatch.setattr(s.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(s,'_tipster_wait_started',0.)
    monkeypatch.setattr(s,'_available_memory_mb',lambda:next(readings))
    def failure(): raise RuntimeError('temporary failure')
    monkeypatch.setattr(s,'_release_match_working_set',failure)
    def wait(seconds):
        assert not s._memory_heavy_lock.locked()
        sleeps.append(seconds);now[0]+=seconds
    monkeypatch.setattr(s.time,'sleep',wait)
    with s._background_slot('다종목 픽스터'):
        assert sleeps==[30] and s._memory_heavy_lock.locked()


def test_pending_market_retry_prevents_tipster_reclaim(monkeypatch):
    now=[200.];monkeypatch.setattr(s.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(s,'_tipster_wait_started',0.)
    monkeypatch.setattr(s,'_available_memory_mb',lambda:600 if not s._market_cycle_pending.is_set() else 300)
    monkeypatch.setattr(s,'_release_match_working_set',lambda:pytest.fail('market reservation must win'))
    s._market_cycle_pending.set()
    def wait(seconds):
        assert not s._memory_heavy_lock.locked();s._market_cycle_pending.clear();now[0]+=seconds
    monkeypatch.setattr(s.time,'sleep',wait)
    with s._background_slot('다종목 픽스터'): assert s._memory_heavy_lock.locked()
