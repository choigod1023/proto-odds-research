"""Completed-period display evidence must survive the live collector coherently."""
from copy import deepcopy
from datetime import datetime

import pytest

from src import live_scores as ls


SPORTS = [("soccer", 1), ("baseball", 5), ("basketball", 2)]


def raw_game(sport, count, status="IN_PROGRESS", period=None):
    return {
        "id": 123, "gameStatus": status, "period": count + 1 if period is None else period,
        "inningDivision": "TOP", "startDatetime": "2026-09-06T08:30:00+09:00",
        "league": {"shortName": "NPB" if sport == "baseball" else sport},
        "teams": {
            "home": {"name": "Home", "score": 90, "periodData": [
                {"period": n, "score": n - 1} for n in range(1, count + 1)]},
            "away": {"name": "Away", "score": 80, "periodData": [
                {"period": n, "score": n} for n in range(1, count + 1)]},
        },
    }


def assert_no_half(game):
    assert "first_half_score" not in game
    assert "first_half_complete" not in game


@pytest.mark.parametrize("sport,count", SPORTS)
@pytest.mark.parametrize("status", ["IN_PROGRESS", "FINAL"])
def test_completed_half_uses_all_required_rows_in_home_away_order(sport, count, status):
    raw = raw_game(sport, count, status)
    # Unsorted rows and extra innings/quarters, ET or shootouts cannot change it.
    for side in ("home", "away"):
        raw["teams"][side]["periodData"].reverse()
        raw["teams"][side]["periodData"].append({"period": count + 1, "score": 7})
    game = ls.normalize_named_game(raw, sport)
    assert game["first_half_score"] == [sum(range(count)), sum(range(1, count + 1))]
    assert game["first_half_complete"] is True
    assert game["home_score"] != game["first_half_score"][0]
    assert game["finished"] is (status == "FINAL")
    if status == "IN_PROGRESS":
        assert "regular_time_score" not in game  # no new settlement result


@pytest.mark.parametrize("period", [None, 1, 2])
def test_soccer_raw_break_time_is_explicit_completion_even_without_clock(period):
    raw = raw_game("soccer", 1, "BREAK_TIME")
    raw["period"] = period
    game = ls.normalize_named_game(raw, "soccer")
    assert game["first_half_score"] == [0, 1]
    assert game["first_half_complete"] is True
    assert game["status"] == "STARTED" and game["finished"] is False


@pytest.mark.parametrize("sport,count", SPORTS)
@pytest.mark.parametrize("status", ["READY", "CANCEL", "CANCELED", "CANCELLED", "CUT",
                                     "POSTPONED", "SUSPENDED", "STARTED", "ENDED", ""])
def test_only_audited_raw_statuses_can_prove_completion(sport, count, status):
    assert_no_half(ls.normalize_named_game(raw_game(sport, count, status), sport))


@pytest.mark.parametrize("sport,count", SPORTS)
@pytest.mark.parametrize("period", [None, 0, 1, True, "6", "unknown", 6.0, -1])
def test_unproven_or_coerced_current_period_cannot_unlock_half(sport, count, period):
    raw = raw_game(sport, count)
    raw.update(period=period, broadcast={"displayTime": "01:30"}, out=3)
    assert_no_half(ls.normalize_named_game(raw, sport))


@pytest.mark.parametrize("sport,count", SPORTS)
def test_current_last_required_period_is_not_completed(sport, count):
    raw = raw_game(sport, count, period=count)
    raw.update(inningDivision="BOTTOM", out=3, broadcast={"displayTime": "00:00"})
    assert_no_half(ls.normalize_named_game(raw, sport))


@pytest.mark.parametrize("sport,count", SPORTS[1:])
def test_non_soccer_break_time_is_not_proof_even_with_next_period(sport, count):
    assert_no_half(ls.normalize_named_game(raw_game(sport, count, "BREAK_TIME"), sport))


@pytest.mark.parametrize("sport,count", SPORTS)
@pytest.mark.parametrize("side", ["home", "away"])
@pytest.mark.parametrize("value", [None, -1, True, False, 1.5, 1.0, "0", "", float("nan"),
                                   float("inf")])
def test_required_scores_are_strict_nonnegative_integers(sport, count, side, value):
    raw = raw_game(sport, count)
    raw["teams"][side]["periodData"][-1]["score"] = value
    assert_no_half(ls.normalize_named_game(raw, sport))


@pytest.mark.parametrize("sport,count", SPORTS)
@pytest.mark.parametrize("side", ["home", "away"])
@pytest.mark.parametrize("defect", ["missing", "duplicate", "string_period", "bool_period", "float_period"])
def test_required_periods_must_be_explicit_unique_and_present_on_both_sides(sport, count, side, defect):
    raw = raw_game(sport, count, "FINAL")
    rows = raw["teams"][side]["periodData"]
    if defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows.append(dict(rows[-1]))
    else:
        rows[0]["period"] = {"string_period": "1", "bool_period": True, "float_period": 1.0}[defect]
    assert_no_half(ls.normalize_named_game(raw, sport))


@pytest.mark.parametrize("sport,count", SPORTS)
def test_all_zero_completed_rows_are_evidence_but_totals_are_not(sport, count):
    raw = raw_game(sport, count, "FINAL")
    raw.pop("period")  # explicit FINAL plus complete rows is sufficient
    for team in raw["teams"].values():
        for row in team["periodData"]:
            row["score"] = 0
    assert ls.normalize_named_game(raw, sport)["first_half_score"] == [0, 0]
    for team in raw["teams"].values():
        team.pop("periodData")
    raw["broadcast"] = {"score": {"home": 10, "away": 11}, "displayTime": "01:30"}
    assert_no_half(ls.normalize_named_game(raw, sport))


@pytest.mark.parametrize("rows", [None, {}, "bad", [None], [{"score": 0}], [{"period": 0, "score": 0}]])
def test_evidence_reader_rejects_malformed_period_tables(rows):
    raw = raw_game("soccer", 1)
    raw["teams"]["away"]["periodData"] = rows
    assert_no_half(ls._named_first_half_evidence(raw, "soccer"))


def test_volleyball_does_not_claim_a_supported_first_half():
    assert_no_half(ls.normalize_named_game(raw_game("volleyball", 2), "volleyball"))


def test_audited_named_basketball_final_sample():
    # NAMED 11871873, 2026-09-06: periodData are quarter scores, not totals.
    raw = raw_game("basketball", 2, "FINAL", 4)
    for side, values in (("home", [28, 6, 24, 16]), ("away", [11, 18, 10, 19])):
        raw["teams"][side]["periodData"] = [
            {"period": n, "score": score, "foul": 0} for n, score in enumerate(values, 1)]
    assert ls.normalize_named_game(raw, "basketball")["first_half_score"] == [34, 29]


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("status", ["STARTED", "RESULT", "CANCEL", "POSTPONED"])
def test_dedup_does_not_donate_old_half_or_phase_evidence_to_new_scores(stale, status):
    old = ls.normalize_named_game(raw_game("soccer", 1), "soccer")
    old.update(stale=stale, observed_at="2026-09-06T00:30:00Z", inning=6)
    fresh = {key: value for key, value in old.items() if key not in (
        "first_half_score", "first_half_complete", "clock", "inning", "period_scores",
        "current_period", "timeline", "timeline_scope")}
    fresh.update(source="naver", game_id="naver:456", home_score=4, away_score=2,
                 stale=False, status=status, observed_at="2026-09-06T01:00:00Z")
    for rows in ([old, fresh], [fresh, old]):
        merged, = ls.deduplicate_games(rows)
        assert merged["game_id"] == fresh["game_id"]
        assert (merged["home_score"], merged["away_score"]) == (4, 2)
        assert merged["observed_at"] == fresh["observed_at"]
        assert_no_half(merged)
        for key in ("clock", "inning", "period_scores", "current_period", "timeline", "timeline_scope"):
            assert key not in merged


def test_dedup_keeps_preferred_evidence_pair_and_never_combines_partial_pairs():
    preferred = ls.normalize_named_game(raw_game("baseball", 5), "baseball")
    preferred["stale"] = False
    other = {**preferred, "source": "naver", "game_id": "naver:456", "stale": True,
             "first_half_score": [99, 99]}
    original = deepcopy(preferred)
    for rows in ([other, preferred], [preferred, other]):
        merged, = ls.deduplicate_games(rows)
        assert merged["first_half_score"] == [10, 15]
        assert merged["first_half_complete"] is True
    assert preferred == original
    for missing in ("first_half_score", "first_half_complete"):
        partial = {key: value for key, value in preferred.items() if key != missing}
        assert_no_half(ls.deduplicate_games([other, partial])[0])


def test_latest_overlapping_schedule_and_checkpoint_do_not_restore_old_evidence():
    old_raw = raw_game("soccer", 1)
    old = ls.normalize_named_game(old_raw, "soccer")
    current_raw = deepcopy(old_raw)
    current_raw["teams"]["away"]["periodData"] = []
    results = [{"source": "named", "league": "named", "day": "2026-09-06", "error": None,
                "observed_at": timestamp, "payload": {"soccer": [raw]}}
               for timestamp, raw in (("2026-09-06T00:30:00Z", old_raw),
                                      ("2026-09-06T01:00:00Z", current_raw))]
    current = ls.schedule_games(list(reversed(results)), {})
    for partial in (True, False):
        game, = ls.build_document(current, [old], datetime(2026, 9, 6, tzinfo=ls.KST),
                                  results, partial=partial)["games"]
        assert_no_half(game)
        assert game["observed_at"] == "2026-09-06T01:00:00Z"


@pytest.mark.parametrize("period", [5, 6])
def test_detail_enrichment_never_recomputes_half_from_a_different_observation(monkeypatch, period):
    game = ls.normalize_named_game(raw_game("baseball", 5, period=period), "baseball")
    game["observed_at"] = "2026-09-06T00:30:00Z"
    original = deepcopy(game)
    detail = raw_game("baseball", 5, period=6 if period == 5 else 5)
    detail.update(out=3, currentBatter={"name": "current batter"})
    monkeypatch.setattr(ls, "_named_situation_job", lambda *args: ls.named_baseball_situation(detail))
    ls.enrich_situations([game])
    assert game["situation_inning"] == detail["period"]
    assert game["current_period"] == original["current_period"]
    assert game["observed_at"] == original["observed_at"]
    if period == 5:
        assert_no_half(game)
    else:
        assert game["first_half_score"] == original["first_half_score"]
        assert game["first_half_complete"] is True


def test_naver_schedule_and_relay_have_no_audited_half_evidence():
    raw = {"gameId": "123", "statusCode": "STARTED", "statusInfo": "6회초",
           "homeTeamScore": 10, "awayTeamScore": 15}
    assert_no_half(ls.normalize_naver_game(raw, "NPB", {}, "2026-09-06T00:30:00Z"))
    assert_no_half(ls.baseball_situation({"result": {"textRelayData": {
        "homeOrAway": "1", "baseInfo": {"ballCount": {"o": 3}}}}}))
