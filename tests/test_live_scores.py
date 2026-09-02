from datetime import datetime, timezone

from src.live_scores import (add_proto_aliases, baseball_situation,
                             deduplicate_games, merge_recent_games, named_soccer_clock,
                             normalize_named_game, RESULT_STATUSES,
                             TERMINAL_STATUSES)


def test_naver_ended_status_is_treated_as_finished():
    assert "ENDED" in RESULT_STATUSES
    assert "ENDED" in TERMINAL_STATUSES


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


def test_deduplicate_games_merges_source_ids_but_keeps_doubleheader():
    games = [
        {"source": "naver", "game_id": "naver:20260830AZSF1", "league": "MLB",
         "start": "2026-08-30T05:05:00", "home": "샌프란시스코", "away": "애리조나",
         "home_alias": ["샌프자이"], "away_alias": ["애리다이"], "batter": "이정후"},
        {"game_id": "20260830AZSF1", "league": "MLB",
         "start": "2026-08-30T05:05:00", "home": "샌프란시스코", "away": "애리조나"},
        {"source": "named", "game_id": "named:11775275", "league": "MLB",
         "start": "2026-08-30T05:05:00+09:00", "home": "샌프란시스코", "away": "애리조나"},
        {"source": "naver", "game_id": "naver:20260830AZSF2", "league": "MLB",
         "start": "2026-08-30T11:05:00", "home": "샌프란시스코", "away": "애리조나"},
    ]

    result = deduplicate_games(games)

    assert len(result) == 2
    assert result[0]["game_id"] == "naver:20260830AZSF1"
    assert result[0]["batter"] == "이정후"


def test_normalize_named_final_game_includes_score_and_kst():
    raw = {
        "id": 123, "gameStatus": "FINAL", "result": "WIN",
        "startDatetime": "2026-08-30T19:00:00",
        "league": {"shortName": "EPL"},
        "teams": {
            "home": {"name": "아스널", "shortName": "ARS",
                     "periodData": [{"score": 1}, {"score": 2}]},
            "away": {"name": "리버풀", "periodData": [{"score": 1}, {"score": 0}]},
        },
    }
    game = normalize_named_game(raw, "soccer")
    assert game["game_id"] == "named:123"
    assert game["status"] == "RESULT"
    assert game["finished"] is True
    assert game["home_score"] == 3
    assert game["away_score"] == 1
    assert game["start"].endswith("+09:00")


def test_normalize_named_ready_hides_zero_score():
    raw = {
        "id": 124, "gameStatus": "READY", "startDatetime": "2026-08-31T19:00:00",
        "league": {"name": "K리그 1"},
        "teams": {"home": {"name": "FC 서울", "score": 0},
                  "away": {"name": "울산 HD", "score": 0}},
    }
    game = normalize_named_game(raw, "soccer")
    assert game["status"] == "BEFORE"
    assert game["home_score"] is None
    assert game["away_score"] is None


def test_normalize_named_break_time_is_still_live():
    raw = {
        "id": 125, "gameStatus": "BREAK_TIME", "startDatetime": "2026-08-30T19:00:00",
        "league": {"name": "EPL"},
        "teams": {"home": {"name": "아스널", "periodData": [{"score": 1}]},
                  "away": {"name": "리버풀", "periodData": [{"score": 0}]}},
    }
    game = normalize_named_game(raw, "soccer")
    assert game["status"] == "STARTED"
    assert game["finished"] is False
    assert game["status_text"] == "하프타임"


def test_named_soccer_clock_converts_cumulative_minute_to_half_minute():
    first = {"gameStatus": "IN_PROGRESS", "period": 1,
             "broadcast": {"displayTime": "00:34"}}
    second = {"gameStatus": "IN_PROGRESS", "period": 2,
              "broadcast": {"displayTime": "01:23"}}
    assert named_soccer_clock(first) == {
        "period": 1, "elapsed_minute": 34, "phase": "first_half", "label": "전반 34분",
    }
    assert named_soccer_clock(second) == {
        "period": 2, "elapsed_minute": 83, "phase": "second_half", "label": "후반 38분",
    }


def test_named_soccer_clock_keeps_halftime_label():
    assert named_soccer_clock({"gameStatus": "BREAK_TIME", "period": 2})["label"] == "하프타임"


def test_add_proto_aliases_matches_abbreviated_names_by_sport_and_date():
    named = [{
        "sport": "bs", "md": "08.30", "home": "밀워키 브루어스",
        "away": "애틀랜타 브레이브스", "home_alias": [], "away_alias": [],
    }]
    proto = [{
        "sport": "bs", "date": "08.30(일) 08:10",
        "home": "밀워브루", "away": "애틀브레",
    }]
    assert add_proto_aliases(named, proto) == 1
    assert named[0]["home_alias"] == ["밀워브루"]
    assert named[0]["away_alias"] == ["애틀브레"]
