from datetime import datetime, timezone

from src.live_scores import baseball_situation, merge_recent_games


def test_baseball_situation_extracts_batter_count_and_runners():
    payload = {
        "result": {
            "textRelayData": {
                "homeOrAway": "0",
                "baseInfo": {
                    "homePitcher": "야나가와",
                    "awayPitcher": "이시카와",
                    "ballCount": {
                        "batter": "야스다",
                        "batterId": "1700092",
                        "s": 2,
                        "b": 3,
                        "o": 1,
                        "base1": "오가와",
                        "base1Id": "2000089",
                        "base2": "",
                        "base2Id": "",
                        "base3": "사토",
                        "base3Id": "1900081",
                    },
                    "nextPlayer": {"player": "오가와", "nextPlayer": "테라치"},
                },
            }
        }
    }

    assert baseball_situation(payload) == {
        "batting_side": "away",
        "batter": "야스다",
        "batter_id": "1700092",
        "pitcher": "야나가와",
        "balls": 3,
        "strikes": 2,
        "outs": 1,
        "bases": {
            "first": {"occupied": True, "runner": "오가와", "runner_id": "2000089"},
            "second": {"occupied": False, "runner": None, "runner_id": None},
            "third": {"occupied": True, "runner": "사토", "runner_id": "1900081"},
        },
        "next_batter": "오가와",
        "on_deck": "테라치",
    }


def test_baseball_situation_returns_empty_when_relay_is_missing():
    assert baseball_situation({"result": {}}) == {}


def test_merge_recent_games_keeps_terminal_history_and_prefers_current():
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    previous = [
        {"game_id": "kept", "start": "2026-08-10T18:00:00+09:00", "status": "RESULT"},
        {"game_id": "updated", "start": "2026-08-29T18:00:00+09:00", "status": "RESULT"},
        {"game_id": "before", "start": "2026-08-29T18:00:00+09:00", "status": "BEFORE"},
        {"game_id": "old", "start": "2026-06-01T18:00:00+09:00", "status": "RESULT"},
    ]
    current = [{"game_id": "updated", "start": "2026-08-29T18:00:00+09:00", "status": "CANCEL"}]
    by_id = {row["game_id"]: row for row in merge_recent_games(current, previous, now)}
    assert set(by_id) == {"kept", "updated"}
    assert by_id["updated"]["status"] == "CANCEL"
