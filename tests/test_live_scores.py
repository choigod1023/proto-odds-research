from datetime import datetime, timezone
from copy import deepcopy

import pytest
import requests

from src import live_scores as ls

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


def test_soccer_regulation_score_excludes_extra_time_and_shootout():
    raw = {
        "gameStatus": "FINAL", "teams": {
            "home": {"periodData": [
                {"period": 2, "score": 1}, {"period": 1, "score": 0},
                {"period": 3, "score": 1}, {"period": 5, "score": 5},
            ]},
            "away": {"periodData": [
                {"period": 1, "score": 1}, {"period": 2, "score": 0},
                {"period": 3, "score": 1}, {"period": 5, "score": 4},
            ]},
        },
    }
    assert normalize_named_game(raw, "soccer")["regular_time_score"] == [1, 1]
    raw["gameStatus"] = "IN_PROGRESS"
    assert "regular_time_score" not in normalize_named_game(raw, "soccer")
    raw["gameStatus"] = "FINAL"
    raw["teams"]["away"]["periodData"][1]["score"] = None
    assert "regular_time_score" not in normalize_named_game(raw, "soccer")
    raw["teams"]["away"]["periodData"] = [{"score": 1}, {"score": 0}]
    assert "regular_time_score" not in normalize_named_game(raw, "soccer")


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


def test_add_proto_aliases_uses_named_short_names_for_final_result_join():
    named = [
        {
            "sport": "sc", "md": "09.02", "start": "2026-09-02T03:45:00+09:00",
            "home": "셰필드 유나이티드", "away": "볼턴 원더러스",
            "home_alias": ["셰필드 유나이티드"], "away_alias": ["볼턴 원더러스"],
        },
        {
            "sport": "sc", "md": "09.02", "start": "2026-09-02T18:30:00+09:00",
            "home": "반라우레", "away": "도치기 시티",
            "home_alias": ["반라우레 하치노헤"], "away_alias": ["도치기 시티"],
        },
    ]
    proto = [
        {"sport": "sc", "date": "09.02(수) 03:45", "home": "셰필드U", "away": "볼턴W"},
        {"sport": "sc", "date": "09.02(수) 18:30", "home": "하치노헤", "away": "도치기시"},
    ]

    assert add_proto_aliases(named, proto) == 2
    assert "셰필드U" in named[0]["home_alias"]
    assert "볼턴W" in named[0]["away_alias"]
    assert "하치노헤" in named[1]["home_alias"]
    assert "도치기시" in named[1]["away_alias"]


def _npb_raw(game_id="20260905JLOX0", status="STARTED"):
    return {"gameId": game_id, "homeTeamName": "오릭스", "awayTeamName": "지바롯데",
            "gameDateTime": "2026-09-05T14:00:00", "statusCode": status,
            "statusInfo": "5회말" if status == "STARTED" else "1회초",
            "homeTeamScore": 2, "awayTeamScore": 4}


def _result(league="NPB", *, error=None, observed_at="2026-09-05T06:30:00+00:00", payload=None):
    return {"source": "naver", "league": league, "day": "2026-09-05", "error": error,
            "observed_at": None if error else observed_at,
            "payload": [_npb_raw()] if payload is None else payload}


def test_npb_status_and_observation_come_from_schedule_not_inning_placeholder():
    timestamp = "2026-09-05T06:30:00+00:00"
    live = ls.normalize_naver_game(_npb_raw(), "NPB", {}, timestamp)
    assert (live["status"], live["home_score"], live["away_score"]) == ("STARTED", 2, 4)
    assert live["observed_at"] == timestamp
    before = ls.normalize_naver_game(_npb_raw(status="BEFORE"), "NPB", {}, timestamp)
    assert before["status"] == "BEFORE" and before["status_text"] == "경기 전"
    assert before["home_score"] is None and before["away_score"] is None


def test_failed_npb_keeps_old_row_timestamp_in_fresh_partial_document():
    old_time = "2026-09-05T04:43:03+00:00"
    old = ls.normalize_naver_game(_npb_raw(status="BEFORE"), "NPB", {}, old_time)
    results = [_result(error="ReadTimeout"), _result("KBO", payload=[])]
    doc = ls.build_document([], [old], datetime(2026, 9, 5, tzinfo=timezone.utc), results)
    assert doc["partial"] is True
    assert doc["games"][0]["observed_at"] == old_time
    assert doc["games"][0]["stale"] is True
    assert doc["generated_at"] != old_time
    assert doc["source_status"][0]["observed_at"] is None
    assert "payload" not in doc["source_status"][0]
    assert old.get("stale") is False  # no mutation of previous snapshot


def test_legacy_retained_rows_inherit_old_document_timestamp_only(monkeypatch):
    monkeypatch.setattr(ls, "load_artifact", lambda *args: {
        "generated_at": "2026-09-05T04:43:03+00:00",
        "games": [{"game_id": "old"}, {"game_id": "known", "observed_at": "2026-09-05T04:00:00Z"}],
    })
    old, known = ls._previous_games()
    assert old["observed_at"] == "2026-09-05T04:43:03+00:00"
    assert known["observed_at"] == "2026-09-05T04:00:00Z"


def test_fresh_named_score_beats_failed_naver_without_old_relay():
    old = ls.normalize_naver_game(_npb_raw(), "NPB", {}, "2026-09-05T04:43:03Z")
    old.update(stale=True, batter="old batter", bases={"first": {"occupied": True}})
    fresh = {**old, "source": "named", "game_id": "named:123", "home_score": 3,
             "observed_at": "2026-09-05T06:30:00Z", "stale": False}
    del fresh["batter"], fresh["bases"]
    for rows in ([old, fresh], [fresh, old]):
        merged, = ls.deduplicate_games(rows)
        assert merged["source"] == "named" and merged["home_score"] == 3
        assert merged["observed_at"] == fresh["observed_at"] and not merged["stale"]
        assert "batter" not in merged and "bases" not in merged


@pytest.mark.parametrize("status", ["STARTED", "RESULT"])
def test_named_advanced_state_beats_naver_before_regardless_of_order(status):
    before = ls.normalize_naver_game(_npb_raw(status="BEFORE"), "NPB", {}, "2026-09-05T06:31:00Z")
    advanced = {**before, "source": "named", "game_id": "named:123", "status": status,
                "home_score": 3, "away_score": 4, "finished": status == "RESULT",
                "terminal": status == "RESULT", "status_text": "6회초" if status == "STARTED" else "경기 종료",
                "observed_at": "2026-09-05T06:30:00Z"}
    for rows in ([before, advanced], [advanced, before]):
        game, = ls.deduplicate_games(rows)
        assert game["source"] == "named" and game["status"] == status
        assert game["home_score"] == 3 and game["away_score"] == 4
        assert game["observed_at"] == advanced["observed_at"]


def test_equal_live_sources_keep_naver_pitch_situation():
    naver = ls.normalize_naver_game(_npb_raw(), "NPB", {}, "2026-09-05T06:30:00Z")
    naver.update(batter="naver batter", outs=1)
    named = {**naver, "source": "named", "game_id": "named:123"}
    del named["batter"], named["outs"]
    game, = ls.deduplicate_games([named, naver])
    assert game["source"] == "naver" and game["batter"] == "naver batter" and game["outs"] == 1


@pytest.mark.parametrize("division,label,side", [("TOP", "6회초", "away"), ("BOTTOM", "6회말", "home")])
def test_named_baseball_inning_and_current_broadcast_totals(division, label, side):
    raw = {"id": 11747825, "gameStatus": "IN_PROGRESS", "period": 6, "inningDivision": division,
           "broadcast": {"score": {"home": 3, "away": 4}},
           "teams": {"home": {"periodData": [{"score": 1}], "startPitcher": {"name": "starter"}},
                     "away": {"periodData": [{"score": 2}]}}, "league": {"shortName": "NPB"}}
    game = ls.normalize_named_game(raw, "baseball")
    assert game["status_text"] == label and game["batting_side"] == side and game["inning"] == 6
    assert (game["home_score"], game["away_score"]) == (3, 4)
    assert "pitcher" not in game and "outs" not in game and "bases" not in game
    raw["inningDivision"] = "UNKNOWN"
    assert ls.normalize_named_game(raw, "baseball")["status_text"] == "진행 중"
    raw["gameStatus"] = "READY"
    assert ls.normalize_named_game(raw, "baseball")["home_score"] is None


def test_known_named_aliases_are_present_before_optional_fuzzy_pass():
    raw = {"id": 11747824, "gameStatus": "IN_PROGRESS", "league": {"shortName": "NPB"},
           "teams": {"home": {"name": "소프트뱅크", "shortName": "FUK"},
                     "away": {"name": "세이부", "shortName": "SAI"}}}
    result = {**_result(), "source": "named", "league": "named", "payload": {"baseball": [raw]}}
    game, = ls.schedule_games([result], {"소프트뱅크": ["소프트뱅"], "세이부": ["세이부"]})
    assert "소프트뱅" in game["home_alias"] and "FUK" in game["home_alias"]


def _lens_game():
    return {
        "source": "named", "game_id": "named:123", "sport": "sc", "league": "리그1",
        "start": "2026-09-06T00:00:00+09:00", "md": "09.06",
        "home": "RC 랑스", "away": "올랭피크 리옹", "home_alias": ["RCL"], "away_alias": [],
        "status": "STARTED", "home_score": 1, "away_score": 0,
        "observed_at": "2026-09-05T15:30:00+00:00", "stale": False,
    }


@pytest.mark.parametrize("partial", [True, False])
@pytest.mark.parametrize("source", ["named", "naver"])
def test_checkpoint_and_full_merge_carry_only_verified_aliases(partial, source):
    current = {**_lens_game(), "source": source, "game_id": f"{source}:123"}
    previous = {**current, "home_alias": ["RC랑스", "RCL", "RC 랑스"],
                "away_alias": ["리옹"], "status": "BEFORE", "status_text": "경기 전",
                "home_score": None, "away_score": None, "stale": True,
                "observed_at": "2026-09-05T14:00:00Z", "clock": {"label": "old"},
                "situation_observed_at": "2026-09-05T14:00:00Z"}
    original_current, original_previous = deepcopy(current), deepcopy(previous)
    doc = ls.build_document([current], [previous], datetime(2026, 9, 6, tzinfo=ls.KST), [],
                            partial=partial)
    game, = doc["games"]
    assert game == {**current, "home_alias": ["RCL", "RC랑스"], "away_alias": ["리옹"]}
    assert doc["partial"] is partial
    assert current == original_current and previous == original_previous
    game["home_alias"].append("output-only")
    assert current == original_current and previous == original_previous


@pytest.mark.parametrize("changes", [
    {"game_id": "named:124"},
    {"source": "naver"},
    {"source": "naver", "game_id": "naver:123"},
    {"start": "2026-09-05T00:00:00+09:00", "md": "09.05"},
    {"start": "2025-09-06T00:00:00+09:00"},
    {"start": "2026-09-06T01:00:00+09:00"},
    {"start": "2026-09-06T00:00:30+09:00"},
    {"start": "2026-09-06T00:00:00+00:00"},
    {"md": "09.05"},
    {"home": "RC ランス"},
    {"away": "파리 생제르맹"},
    {"home": "올랭피크 리옹", "away": "RC 랑스"},
    {"sport": "bs"},
    {"league": "다른 리그"},
])
@pytest.mark.parametrize("partial", [True, False])
def test_alias_carryover_rejects_different_event_day_kickoff_or_side(changes, partial):
    current = _lens_game()
    previous = {**current, "home_alias": ["RC랑스"], "away_alias": ["리옹"], **changes}
    doc = ls.build_document([current], [previous], datetime(2026, 9, 6, tzinfo=ls.KST), [],
                            partial=partial)
    assert doc["games"] == [current]


@pytest.mark.parametrize("missing", [
    {"source": None}, {"game_id": None}, {"game_id": "named:None"},
    {"game_id": "named:"}, {"start": None}, {"start": "invalid"},
    {"start": "2026-09-06"}, {"home": None}, {"away": ""},
])
def test_alias_carryover_requires_complete_event_identity_on_both_rows(missing):
    current = {**_lens_game(), **missing}
    previous = {**current, "home_alias": ["RC랑스"], "away_alias": ["리옹"]}
    games = merge_recent_games([current], [previous], datetime(2026, 9, 6, tzinfo=ls.KST))
    assert all("RC랑스" not in g["home_alias"] and "리옹" not in g["away_alias"] for g in games)


def test_partial_to_full_refresh_unions_verified_aliases_without_old_live_state():
    now = datetime(2026, 9, 6, tzinfo=ls.KST)
    current = _lens_game()
    previous = {**current, "home_alias": ["RC랑스"], "away_alias": ["리옹"]}
    checkpoint = ls.build_document([current], [previous], now, [], partial=True)
    fresh = {**current, "home_alias": ["Lens"], "status": "RESULT", "home_score": 3,
             "observed_at": "2026-09-05T17:00:00Z"}
    full = ls.build_document([fresh], checkpoint["games"], now, [])
    game, = full["games"]
    assert game == {**fresh, "home_alias": ["Lens", "RCL", "RC랑스"], "away_alias": ["리옹"]}
    assert ls.build_document([game], full["games"], now, [])["games"] == full["games"]


def test_verified_aliases_survive_current_source_deduplication():
    named = _lens_game()
    previous = {**named, "home_alias": ["RC랑스"], "away_alias": ["리옹"]}
    naver = {**named, "source": "naver", "game_id": "naver:456"}
    game, = merge_recent_games([named, naver], [previous], datetime(2026, 9, 6, tzinfo=ls.KST))
    assert game["game_id"] == "naver:456"
    assert game["home_alias"] == ["RCL", "RC랑스"] and game["away_alias"] == ["리옹"]


@pytest.mark.parametrize("skip_fuzzy", [True, False])
def test_main_preserves_lens_alias_at_every_save_before_optional_fuzzy_matching(monkeypatch, skip_fuzzy):
    order, saved = [], []
    current = _lens_game()
    previous = {**current, "home_alias": ["RC랑스"], "away_alias": ["리옹"]}
    monkeypatch.setattr(ls, "_aliases", lambda: {})
    monkeypatch.setattr(ls, "_previous_games", lambda: [previous])
    monkeypatch.setattr(ls, "_proto_games", lambda: [])
    raw = {"id": 123, "gameStatus": "IN_PROGRESS", "startDatetime": current["start"],
           "league": {"shortName": current["league"]},
           "teams": {"home": {"name": current["home"], "shortName": "RCL", "score": 2},
                     "away": {"name": current["away"], "score": 1}}}
    def schedules(days, deadline):
        order.append("today" if len(days) == 1 else "history")
        return [{"source": "named", "league": "named", "day": "2026-09-06", "error": None,
                 "observed_at": "2026-09-05T16:00:00Z", "payload": {"soccer": [raw]}}]
    monkeypatch.setattr(ls, "collect_schedules", schedules)
    monkeypatch.setattr(ls, "add_proto_aliases", lambda *args: order.append("fuzzy"))
    monkeypatch.setattr(ls, "FETCH_BUDGET_SECONDS", -1 if skip_fuzzy else 120)
    def save(name, document, path, indent=None):
        order.append("save")
        saved.append(deepcopy(document))
        game, = document["games"]
        assert "RC랑스" in game["home_alias"] and "리옹" in game["away_alias"]
        assert (game["home_score"], game["away_score"]) == (2, 1)
        assert game["observed_at"] == "2026-09-05T16:00:00Z"
    monkeypatch.setattr(ls, "persist_artifact", save)
    monkeypatch.setattr(ls, "enrich_situations", lambda *args: order.append("relay"))
    assert ls.main() == 0
    assert order == ["today", "save", "history", *([] if skip_fuzzy else ["fuzzy"]),
                     "save", "relay", "save"]
    assert saved[0]["partial"] is True and saved[-1]["partial"] is False


def test_dedup_compares_only_possible_matches_in_large_history(monkeypatch):
    comparisons = []
    original = ls._same_physical_game
    monkeypatch.setattr(ls, "_same_physical_game",
                        lambda a, b: comparisons.append(1) or original(a, b))
    games = [{"game_id": f"naver:{i}", "league": "NPB", "start": f"2026-08-{i // 24 + 1:02}T{i % 24:02}:00",
              "home": f"home{i}", "away": f"away{i}"} for i in range(1000)]
    games.append({**games[0], "game_id": "named:duplicate"})
    assert len(ls.deduplicate_games(games)) == 1000
    assert len(comparisons) == 1


def test_indexed_dedup_preserves_original_matching_with_alias_changes():
    rows = [
        {"source": "named", "game_id": "named:a", "league": "NPB", "start": "2026-09-05T14:00:00",
         "home": "オリックス", "away": "千葉", "home_alias": ["오릭스"], "away_alias": ["지바롯데"]},
        ls.normalize_naver_game(_npb_raw(), "NPB", {}, "2026-09-05T06:30:00Z"),
        {"source": "named", "game_id": "named:c", "league": "NPB", "start": "2026-09-05T14:00:00",
         "home": "오릭스", "away": "지바롯데"},
        {"source": "naver", "game_id": "naver:later", "league": "NPB", "start": "2026-09-05T18:00:00",
         "home": "오릭스", "away": "지바롯데"},
    ]
    expected = []
    for row in rows:
        pos = next((i for i, saved in enumerate(expected) if ls._same_physical_game(saved, row)), None)
        if pos is None:
            expected.append(row)
        else:
            expected[pos] = ls._merge_duplicate(expected[pos], row)
    assert ls.deduplicate_games(rows) == expected


def test_schedule_worker_reports_failure_and_does_not_retry(monkeypatch):
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(ls, "_session", Session)
    calls = []
    def fail(*args):
        calls.append(args)
        raise requests.ReadTimeout("diagnostic timeout")
    monkeypatch.setattr(ls, "fetch", fail)
    result = ls._schedule_job(("NPB", "2026-09-05"))
    assert len(calls) == 1 and result["error"].startswith("ReadTimeout")
    assert result["observed_at"] is None
    skipped = ls._schedule_job(("NPB", "2026-09-05"), deadline=0)
    assert skipped["error"] == "budget_exhausted" and len(calls) == 1


def test_schedule_pool_bounds_concurrency_and_fetches_today_first(monkeypatch):
    from threading import Barrier, Lock
    barrier = Barrier(ls.FETCH_WORKERS)
    lock = Lock()
    active = peak = 0
    def worker(job, deadline):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        return job
    monkeypatch.setattr(ls, "_schedule_job", worker)
    result = ls.collect_schedules(["2026-09-05", "2026-09-04"])
    assert len(result) == 10 and peak == ls.FETCH_WORKERS
    assert all(day == "2026-09-05" for league, day in result[:5])


def test_main_checkpoints_today_before_history_or_relay_and_keeps_row_times(monkeypatch):
    order, saved = [], []
    monkeypatch.setattr(ls, "_aliases", lambda: {})
    monkeypatch.setattr(ls, "_previous_games", lambda: [])
    monkeypatch.setattr(ls, "_proto_games", lambda: [])
    def schedules(days, deadline):
        order.append("today" if len(days) == 1 else "history")
        return [_result()] if len(days) == 1 else []
    monkeypatch.setattr(ls, "collect_schedules", schedules)
    def save(name, document, path, indent=None):
        order.append("save")
        saved.append(deepcopy(document))
    monkeypatch.setattr(ls, "persist_artifact", save)
    monkeypatch.setattr(ls, "enrich_situations", lambda games, deadline: order.append("relay"))
    assert ls.main() == 0
    assert order == ["today", "save", "history", "save", "relay", "save"]
    assert all(doc["games"][0]["observed_at"] == "2026-09-05T06:30:00+00:00" for doc in saved)
    assert saved[0]["partial"] is True


def test_main_all_today_sources_failed_does_not_republish_old_data(monkeypatch):
    monkeypatch.setattr(ls, "_aliases", lambda: {})
    monkeypatch.setattr(ls, "_previous_games", lambda: [])
    monkeypatch.setattr(ls, "collect_schedules", lambda *args: [_result(error="budget_exhausted")])
    saved = []
    monkeypatch.setattr(ls, "persist_artifact", lambda *args, **kwargs: saved.append(args))
    with pytest.raises(RuntimeError, match="all current-day"):
        ls.main()
    assert saved == []


def test_relay_limit_and_timestamp_does_not_refresh_score_timestamp(monkeypatch):
    calls = []
    def situation(game_id, deadline):
        calls.append(game_id)
        return {"batter": "new batter", "situation_observed_at": "2026-09-05T06:31:00Z"}
    monkeypatch.setattr(ls, "_situation_job", situation)
    games = [ls.normalize_naver_game(_npb_raw(str(i)), "NPB", {}, "2026-09-05T06:30:00Z") for i in range(20)]
    ls.enrich_situations(games)
    assert len(calls) == ls.SITUATION_LIMIT
    assert games[0]["situation_observed_at"] == "2026-09-05T06:31:00Z"
    assert all(g["observed_at"] == "2026-09-05T06:30:00Z" for g in games)
