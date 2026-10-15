from datetime import datetime, timezone

from src import odds_live


def test_future_archive_cannot_skip_current_sales(monkeypatch):
    monkeypatch.setattr(odds_live, '_session', lambda: object())
    monkeypatch.setattr(odds_live, '_start_hint', lambda _: 172)
    hints, fetched = [], []
    monkeypatch.setattr(odds_live, 'find_live_rounds',
                        lambda s, y, hint: hints.append(hint) or [])
    monkeypatch.setattr(odds_live, '_fetch',
                        lambda s, y, rnd: fetched.append(rnd) or [])
    odds_live.collect({'rounds': list(range(101, 123))})
    assert hints == [119]
    assert fetched == [120, 121, 122, 123, 124]


def test_fresh_poll_does_not_refetch_historical_rounds(monkeypatch):
    monkeypatch.setattr(odds_live, '_session', lambda: object())
    monkeypatch.setattr(odds_live, '_start_hint', lambda _: 172)
    monkeypatch.setattr(odds_live, 'find_live_rounds',
                        lambda *a: (_ for _ in ()).throw(AssertionError('scan')))
    fetched = []
    monkeypatch.setattr(odds_live, '_fetch',
                        lambda s, y, rnd: fetched.append(rnd) or [])
    previous = {'generated_at': datetime.now(timezone.utc).isoformat(),
                'rounds': [118, 122, 123]}
    odds_live.collect({'rounds': list(range(101, 123))}, previous)
    assert set(fetched) == {118, 120, 121, 122, 123, 124}
    assert len(fetched) == 6


def test_no_persisted_rounds_keeps_archive_discovery(monkeypatch):
    monkeypatch.setattr(odds_live, '_session', lambda: object())
    monkeypatch.setattr(odds_live, '_start_hint', lambda _: 119)
    hints = []
    monkeypatch.setattr(odds_live, 'find_live_rounds',
                        lambda s, y, hint: hints.append(hint) or [])
    assert odds_live.collect({})['rounds'] == []
    assert hints == [119]
