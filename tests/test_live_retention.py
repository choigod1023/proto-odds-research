from datetime import UTC, datetime

from src.live_market_refresh import LIVE_RETENTION_DAYS, _drop_expired_live_games


def _game(date, round_no=130):
    return {"year": 2026, "round": round_no, "date": date, "home": "A", "away": "B"}


def test_expired_live_games_are_dropped_and_recent_ones_kept():
    # 2026-11-21 13:00 KST
    clock = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)
    old = _game("08.29(금) 19:00")
    edge_out = _game("11.14(금) 12:59")
    edge_in = _game("11.14(금) 13:00")
    upcoming = _game("11.22(토) 14:00", 138)
    unparsed = _game("미정")
    document = {"live": [old, edge_out, edge_in, upcoming, unparsed]}

    assert LIVE_RETENTION_DAYS == 7
    assert _drop_expired_live_games(document, clock) == 2
    assert document["live"] == [edge_in, upcoming, unparsed]


def test_no_clock_keeps_everything():
    document = {"live": [_game("08.29(금) 19:00")]}
    assert _drop_expired_live_games(document, None) == 0
    assert len(document["live"]) == 1
