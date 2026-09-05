"""Observed response projections (2026-09-05 probes) plus labelled fault cases.

Only irrelevant UI fields are omitted from observed fixtures. Transport is a
text callback; tests never access the network or write database/artifact state.
"""
import copy
import json
from datetime import date, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from src.sports_sources_results import (
    MLB_MAX_BOXSCORES, NAVER_LEAGUES, PartialResultsError, ResultsSourceError,
    UnsupportedLeagueError, collect_mlb, collect_naver,
)


# Exact scalar values from fields=all, with unused rendering fields projected out.
# All finals below were statusCode RESULT, cancel/suspended/timeTbd false.
OBSERVED_NAVER = {
    "KBO": ("20260904HHLT02026", "2026-09-04T18:30:00", "LT", "롯데", 4,
            "HH", "한화", 14, [4, 10, 2, 4], [14, 19, 0, 6]),
    "MLB": ("20260904SFPI0", "2026-09-04T01:35:00", "PI", "피츠버그", 5,
            "SF", "샌프란시스코", 2, [5, 5, 0, 8], [2, 2, 1, 6]),
    "NPB": ("20260904JLOX0", "2026-09-04T18:00:00", "OX", "오릭스", 3,
            "JL", "지바롯데", 1, [3, 8, 0, 3], [1, 4, 1, 5]),
    "NBA": ("2026013130", "2026-02-01T02:00:00", "CHA", "샬럿", 111,
            "SA", "샌안토니오", 106, None, None),
    "KBL": ("2026020106164701182", "2026-02-01T14:00:00", "06", "수원 KT", 89,
            "16", "원주 DB", 96, None, None),
    "WKBL": ("202602010460160", "2026-02-01T16:00:00", "07", "신한은행", 43,
             "09", "하나은행", 76, None, None),
    "KOVO남": ("20260201022M174", "2026-02-01T14:00:00", "1005", "현대캐피탈", 3,
             "1008", "OK저축은행", 0, None, None),
    "KOVO여": ("20260201022F175", "2026-02-01T16:00:00", "2004", "흥국생명", 3,
             "2007", "페퍼저축은행", 1, None, None),
}


def naver_game(league="KBO"):
    event, kickoff, hid, hn, hs, aid, an, aws, hr, ar = OBSERVED_NAVER[league]
    row = {
        "gameId": event, "categoryId": NAVER_LEAGUES[league][2],
        "gameDate": kickoff[:10], "gameDateTime": kickoff,
        "statusCode": "RESULT", "statusNum": 4, "cancel": False,
        "suspended": False, "timeTbd": False,
        "homeTeamCode": hid, "homeTeamName": hn, "homeTeamScore": hs,
        "awayTeamCode": aid, "awayTeamName": an, "awayTeamScore": aws,
    }
    if hr is not None:
        row.update(homeTeamRheb=hr, awayTeamRheb=ar)
    return row


def naver_payload(games, total=None):
    return {"code": 200, "success": True, "result": {
        "games": games, "gameTotalCount": len(games) if total is None else total,
    }}


# Actual MLB 2026-09-03 schedule/boxscore for gamePk 823337. Rates intentionally
# included here to ensure season averages never leak into per-game metrics.
MLB_GAME = {
    "gamePk": 823337, "gameDate": "2026-09-03T16:35:00Z", "officialDate": "2026-09-03",
    "status": {"abstractGameState": "Final", "codedGameState": "F",
               "detailedState": "Final", "statusCode": "F", "startTimeTBD": False},
    "teams": {
        "home": {"team": {"id": 134, "name": "Pittsburgh Pirates"}, "score": 5},
        "away": {"team": {"id": 137, "name": "San Francisco Giants"}, "score": 2},
    },
}
MLB_BOX = {"teams": {
    "home": {"team": {"id": 134}, "teamStats": {"batting": {
        "runs": 5, "hits": 5, "atBats": 26, "baseOnBalls": 7, "strikeOuts": 6,
        "homeRuns": 0, "avg": ".253", "obp": ".331", "slg": ".403", "ops": ".734",
    }}},
    "away": {"team": {"id": 137}, "teamStats": {"batting": {
        "runs": 2, "hits": 2, "atBats": 27, "baseOnBalls": 5, "strikeOuts": 9,
        "homeRuns": 0, "avg": ".247", "obp": ".308", "slg": ".405", "ops": ".713",
    }}},
}}


def mlb_payload(games):
    return {"totalGames": len(games), "dates": [
        {"date": "2026-09-03", "totalGames": len(games), "games": games},
    ] if games else []}


class TextFetch:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        assert self.responses, "Unexpected extra request/retry"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response)


def run_naver(fetch, league="KBO", limit=100):
    day = date.fromisoformat(OBSERVED_NAVER[league][1][:10])
    return collect_naver(fetch, league, day, day, limit)


def run_mlb(fetch, limit=100):
    return collect_mlb(fetch, date(2026, 9, 3), date(2026, 9, 3), limit)


@pytest.mark.parametrize("league", OBSERVED_NAVER)
def test_observed_naver_all_leagues(league):
    raw = naver_game(league)
    fetch = TextFetch(naver_payload([raw]))
    [rec] = run_naver(fetch, league)
    assert rec["provider"] == "naver"
    assert rec["league"] == league
    assert rec["event_id"] == raw["gameId"]
    assert rec["status"] == "final"
    for side in ("home", "away"):
        assert rec[f"{side}_id"] == raw[f"{side}TeamCode"]
        assert rec[f"{side}_name"] == raw[f"{side}TeamName"]
        assert rec[f"{side}_score"] == raw[f"{side}TeamScore"]
        assert "xG" not in rec["metrics"][side]
        assert "baseOnBalls" not in rec["metrics"][side]  # RHEB B isn't walks alone.
    assert rec["sport"] == NAVER_LEAGUES[league][3]
    assert rec["score_unit"] == NAVER_LEAGUES[league][4]
    assert datetime.fromisoformat(rec["kickoff_at"]).utcoffset() == timedelta(0)
    query = parse_qs(urlparse(fetch.urls[0]).query)
    parent_key, parent, category, _, _ = NAVER_LEAGUES[league]
    assert query[parent_key] == [parent]
    assert query["categoryId"] == [category]
    assert query["page"] == ["1"]
    assert rec["source_url"].endswith("/" + raw["gameId"])
    if league in {"KBO", "MLB", "NPB"}:
        assert rec["metric_status"] == "available"
        assert rec["metrics"]["home"]["hits"] == raw["homeTeamRheb"][1]
    else:
        assert rec["metric_status"] == "not_available"
        assert rec["metrics"] == {"home": {}, "away": {}}


def test_observed_naver_cancellation_before_is_not_zero_zero_final():
    raw = naver_game()
    raw.update(gameId="20260830KTSS02026", gameDate="2026-08-30",
               gameDateTime="2026-08-30T18:00:00", homeTeamCode="SS", homeTeamName="삼성",
               awayTeamCode="KT", awayTeamName="KT", homeTeamScore=0, awayTeamScore=0,
               statusCode="BEFORE", statusNum=0, statusInfo="경기취소", cancel=True,
               homeTeamRheb=[], awayTeamRheb=[])
    [rec] = collect_naver(TextFetch(naver_payload([raw])), "KBO",
                          date(2026, 8, 30), date(2026, 8, 30), 10)
    assert rec["status"] == "cancelled"
    assert rec["home_score"] is None and rec["away_score"] is None
    assert rec["metrics"] == {"home": {}, "away": {}}


@pytest.mark.parametrize("code", ["BEFORE", "STARTED", "SUSPENDED", "BREAK"])
def test_naver_live_scores_are_never_final(code):
    raw = naver_game()
    raw["statusCode"] = code  # Synthetic status fault, scores remain populated.
    assert run_naver(TextFetch(naver_payload([raw]))) == []


@pytest.mark.parametrize("code", ["RESULT", "END", "ENDED"])
def test_naver_requires_explicit_terminal_code_and_respects_suspension(code):
    raw = naver_game()
    raw["statusCode"] = code
    assert run_naver(TextFetch(naver_payload([raw])))[0]["status"] == "final"
    raw["suspended"] = True
    assert run_naver(TextFetch(naver_payload([raw]))) == []


@pytest.mark.parametrize("code", ["CANCEL", "CANCELLED", "CANCELED", "POSTPONED"])
def test_naver_cancellation_code_invalidates_scores(code):
    raw = naver_game()
    raw["statusCode"] = code
    rec = run_naver(TextFetch(naver_payload([raw])))[0]
    assert rec["status"] == "cancelled"
    assert rec["home_score"] is None


def test_naver_pages_past_nonfinal_records_and_never_silently_limits():
    live, final = naver_game(), naver_game()
    live.update(gameId="synthetic-live", statusCode="STARTED")
    fetch = TextFetch(naver_payload([live], 2), naver_payload([final], 2))
    assert len(run_naver(fetch, limit=1)) == 1
    assert parse_qs(urlparse(fetch.urls[1]).query)["page"] == ["2"]
    final2 = {**final, "gameId": "synthetic-doubleheader-2"}
    fetch = TextFetch(naver_payload([final], 2), naver_payload([final2], 2))
    with pytest.raises(PartialResultsError) as err:
        run_naver(fetch, limit=1)
    assert err.value.reason == "output_limit"
    assert len(err.value.partial_results) == 1
    assert len(fetch.urls) == 2


@pytest.mark.parametrize("second,reason", [
    (naver_payload([naver_game()], 2), "duplicate_event"),
    (naver_payload([], 2), "pagination"),
    (naver_payload([], 3), "changed_total"),
])
def test_incomplete_naver_is_error_not_success(second, reason):
    fetch = TextFetch(naver_payload([naver_game()], 2), second)
    with pytest.raises(PartialResultsError) as err:
        run_naver(fetch)
    assert err.value.reason == reason
    assert err.value.status == "partial"
    assert len(err.value.partial_results) == 1


def test_naver_maximum_page_budget(monkeypatch):
    monkeypatch.setattr("src.sports_sources_results.NAVER_MAX_PAGES", 1)
    fetch = TextFetch(naver_payload([naver_game()], 2))
    with pytest.raises(PartialResultsError, match="pagination"):
        run_naver(fetch)
    assert len(fetch.urls) == 1


@pytest.mark.parametrize("payload", ["<html>blocked</html>", "[]", {},
    {"code": 429, "success": False}, {"code": 200, "success": True, "result": {}},
    {"code": 200, "success": True, "result": {"games": [], "gameTotalCount": None}},
    {"code": 200, "success": True, "result": {"games": None, "gameTotalCount": 0}},
])
def test_naver_invalid_response_not_no_games(payload):
    with pytest.raises(ResultsSourceError):
        run_naver(TextFetch(payload))


@pytest.mark.parametrize("field,value", [
    ("homeTeamScore", None), ("homeTeamScore", True), ("homeTeamScore", 1.5),
    ("homeTeamScore", -1), ("homeTeamScore", ""), ("homeTeamCode", None),
    ("homeTeamName", ""), ("cancel", "false"), ("timeTbd", True),
    ("gameDateTime", "2026-09-04"), ("gameDateTime", "bad"),
    ("gameDate", "2026-09-05"), ("categoryId", "npb"),
    ("homeTeamRheb", [99, 10, 2, 4]), ("homeTeamRheb", [4, 10]),
])
def test_naver_corrupt_final_raises(field, value):
    raw = naver_game()
    raw[field] = value
    with pytest.raises(ResultsSourceError):
        run_naver(TextFetch(naver_payload([raw])))


def test_kst_date_rollover_and_documented_legacy_format():
    raw = naver_game("NBA")
    raw["gameDateTime"] = "02/01/2026 02:00:00"
    rec = run_naver(TextFetch(naver_payload([raw])), "NBA")[0]
    assert rec["kickoff_at"] == "2026-01-31T17:00:00+00:00"
    raw["gameDateTime"] = "2026-01-31T17:00:00Z"
    assert run_naver(TextFetch(naver_payload([raw])), "NBA")[0] == rec


def test_unsupported_wnba_is_not_an_empty_offseason():
    fetch = TextFetch()
    with pytest.raises(UnsupportedLeagueError):
        collect_naver(fetch, "WNBA", date(2026, 9, 4), date(2026, 9, 4), 10)
    assert fetch.urls == []


def test_empty_schedules_are_success():
    assert run_naver(TextFetch(naver_payload([]))) == []
    assert run_mlb(TextFetch(mlb_payload([]))) == []


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.parametrize("source", ["naver", "mlb", "mlb_boxscore"])
def test_http_errors_propagate_without_retry(status, source):
    error = HTTPError("https://example.test/", status, "blocked", None, None)
    fetch = TextFetch(*([mlb_payload([MLB_GAME])] if source == "mlb_boxscore" else []), error)
    with pytest.raises(HTTPError) as err:
        (run_naver if source == "naver" else run_mlb)(fetch)
    assert err.value is error
    assert len(fetch.urls) == (2 if source == "mlb_boxscore" else 1)


def test_mlb_observed_schedule_and_actual_batting_metrics():
    fetch = TextFetch(mlb_payload([MLB_GAME]), MLB_BOX)
    [rec] = run_mlb(fetch)
    assert rec["provider"] == "mlb" and rec["league"] == "MLB"
    assert rec["sport"] == "야구" and rec["score_unit"] == "runs"
    assert rec["event_id"] == "823337" and rec["home_id"] == "134"
    assert rec["home_score"] == 5 and rec["away_score"] == 2
    assert rec["kickoff_at"] == "2026-09-03T16:35:00+00:00"
    assert rec["metrics"]["home"] == {
        "runs": 5, "hits": 5, "atBats": 26, "baseOnBalls": 7, "strikeOuts": 6, "homeRuns": 0,
    }
    assert rec["metric_status"] == "available"
    assert rec["detail_fetch_status"] == "fetched"
    assert rec["source_url"] == fetch.urls[1]
    assert fetch.urls[1].endswith("/game/823337/boxscore")
    assert parse_qs(urlparse(fetch.urls[0]).query) == {
        "sportId": ["1"], "startDate": ["2026-09-03"], "endDate": ["2026-09-03"],
    }


@pytest.mark.parametrize("code", ["C", "D", "I", "P", "S", "T", "U", "O"])
def test_mlb_does_not_misread_abstract_final(code):
    raw = copy.deepcopy(MLB_GAME)
    raw["status"]["codedGameState"] = code
    fetch = TextFetch(mlb_payload([raw]))
    rows = run_mlb(fetch)
    if code in {"C", "D"}:
        assert rows[0]["status"] == "cancelled"
        assert rows[0]["home_score"] is None
        assert rows[0]["detail_fetch_status"] == "not_applicable"
    else:
        assert rows == []
    assert len(fetch.urls) == 1


def test_mlb_boxscores_have_small_fixed_budget_and_latest_order():
    games = [{**copy.deepcopy(MLB_GAME), "gamePk": 100 + i,
              "gameDate": f"2026-09-03T{10+i:02d}:00:00Z"} for i in range(7)]
    fetch = TextFetch(mlb_payload(games), *[MLB_BOX] * MLB_MAX_BOXSCORES)
    rows = run_mlb(fetch)
    assert len(rows) == 7
    assert [r["event_id"] for r in rows] == [str(i) for i in range(106, 99, -1)]
    assert len(fetch.urls) == 1 + MLB_MAX_BOXSCORES
    assert rows[-1]["metric_status"] == "not_available"
    assert rows[-1]["metrics"] == {"home": {}, "away": {}}
    assert all(row["detail_fetch_status"] == "fetched" for row in rows[:MLB_MAX_BOXSCORES])
    assert all(row["detail_fetch_status"] == "not_requested_budget"
               for row in rows[MLB_MAX_BOXSCORES:])


def test_mlb_output_limit_raises_before_boxscore():
    fetch = TextFetch(mlb_payload([MLB_GAME, {**MLB_GAME, "gamePk": 123}]))
    with pytest.raises(PartialResultsError) as err:
        run_mlb(fetch, limit=1)
    assert err.value.reason == "output_limit"
    assert len(err.value.partial_results) == 1
    assert len(fetch.urls) == 1
    [row] = err.value.partial_results
    assert row["detail_fetch_status"] == "not_requested_budget"
    assert row["status"] == "final"
    assert row["home_score"] == 5 and row["away_score"] == 2
    assert row["metrics"] == {"home": {}, "away": {}}
    assert row["source_url"] == fetch.urls[0]


@pytest.mark.parametrize("payload", [{}, {"totalGames": 0}, {"totalGames": 0, "dates": None},
    {"messageNumber": 1, "message": "error"}, "not json"])
def test_mlb_malformed_response_is_not_empty(payload):
    with pytest.raises(ResultsSourceError):
        run_mlb(TextFetch(payload))


def test_mlb_missing_games_and_conflicting_duplicates_are_partial():
    payload = mlb_payload([MLB_GAME])
    payload["totalGames"] = 2
    with pytest.raises(PartialResultsError, match="pagination"):
        run_mlb(TextFetch(payload))
    other = copy.deepcopy(MLB_GAME)
    other["teams"]["home"]["score"] = 8
    with pytest.raises(PartialResultsError, match="duplicate_event"):
        run_mlb(TextFetch(mlb_payload([MLB_GAME, other])))
    assert len(run_mlb(TextFetch(mlb_payload([MLB_GAME, MLB_GAME]), MLB_BOX))) == 1


@pytest.mark.parametrize("field,value", [("gameDate", "2026-09-03T16:35:00"),
    ("officialDate", "2026-09-04"), ("teams", {}), ("gamePk", None)])
def test_mlb_bad_final_raises(field, value):
    raw = {**copy.deepcopy(MLB_GAME), field: value}
    with pytest.raises(ResultsSourceError):
        run_mlb(TextFetch(mlb_payload([raw])))


def test_mlb_bad_boxscore_not_masked_as_missing_metrics():
    for box in ({}, {"teams": {}}, {"message": "blocked"}):
        with pytest.raises(ResultsSourceError):
            run_mlb(TextFetch(mlb_payload([MLB_GAME]), box))
    box = copy.deepcopy(MLB_BOX)
    box["teams"]["home"]["team"]["id"] = 137
    with pytest.raises(ResultsSourceError):
        run_mlb(TextFetch(mlb_payload([MLB_GAME]), box))


def test_mlb_absent_batting_stats_are_not_invented():
    box = copy.deepcopy(MLB_BOX)
    for entry in box["teams"].values():
        entry["teamStats"] = {}
    [rec] = run_mlb(TextFetch(mlb_payload([MLB_GAME]), box))
    assert rec["metrics"] == {"home": {}, "away": {}}
    assert rec["metric_status"] == "not_available"
    assert rec["detail_fetch_status"] == "fetched"
    assert rec["source_url"].endswith("/game/823337/boxscore")


def test_mlb_repeat_poll_distinguishes_budget_skip_from_vanished_stats():
    [first] = run_mlb(TextFetch(mlb_payload([MLB_GAME]), MLB_BOX))
    newer = [{**copy.deepcopy(MLB_GAME), "gamePk": 900000 + i,
              "gameDate": f"2026-09-03T{18+i:02d}:00:00Z"} for i in range(5)]
    later = run_mlb(TextFetch(mlb_payload([MLB_GAME, *newer]), *[MLB_BOX] * 5))
    skipped = next(row for row in later if row["event_id"] == first["event_id"])
    for key in ("provider", "league", "event_id", "home_id", "away_id", "home_score", "away_score"):
        assert skipped[key] == first[key]
    assert first["metrics"]["home"]["hits"] == 5
    assert first["detail_fetch_status"] == "fetched"
    assert skipped["metrics"] == {"home": {}, "away": {}}
    assert skipped["detail_fetch_status"] == "not_requested_budget"
    # Stateless adapter does not pretend to preserve the prior metrics/provenance.
    assert first["source_url"].endswith("/boxscore")
    assert "/schedule?" in skipped["source_url"]


@pytest.mark.parametrize("batting", [{}, {"hits": None, "runs": None}, {"avg": ".253"}])
def test_fetched_boxscore_with_absent_actual_counts_is_not_budget_skip(batting):
    box = copy.deepcopy(MLB_BOX)
    for entry in box["teams"].values():
        entry["teamStats"]["batting"] = batting
    [row] = run_mlb(TextFetch(mlb_payload([MLB_GAME]), box))
    assert row["detail_fetch_status"] == "fetched"
    assert row["metric_status"] == "not_available"
    assert row["metrics"] == {"home": {}, "away": {}}
    assert row["source_url"].endswith("/boxscore")


def test_fetched_partial_actual_metrics_do_not_invent_other_side():
    box = copy.deepcopy(MLB_BOX)
    box["teams"]["away"]["teamStats"]["batting"] = {}
    [row] = run_mlb(TextFetch(mlb_payload([MLB_GAME]), box))
    assert row["detail_fetch_status"] == "fetched"
    assert row["metric_status"] == "available"
    assert row["metrics"]["home"]["hits"] == 5
    assert row["metrics"]["away"] == {}


@pytest.mark.parametrize("source", ["mlb", "naver"])
def test_output_limit_keeps_valid_current_date_cancellation(source):
    # Cancelled kickoff can be later than receipt on the same provider date.
    # Core owns comparison with observed_at; the adapter keeps the invalidation.
    if source == "mlb":
        cancelled = copy.deepcopy(MLB_GAME)
        cancelled.update(gamePk=900001, gameDate="2026-09-03T23:00:00Z")
        cancelled["status"]["codedGameState"] = "D"
        fetch = TextFetch(mlb_payload([MLB_GAME, cancelled]))
        collect = run_mlb
    else:
        cancelled = naver_game()
        cancelled.update(gameId="synthetic-cancelled", cancel=True, statusCode="BEFORE",
                         gameDateTime="2026-09-04T23:00:00")
        fetch = TextFetch(naver_payload([naver_game(), cancelled]))
        collect = run_naver
    with pytest.raises(PartialResultsError) as err:
        collect(fetch, limit=1)
    assert err.value.reason == "output_limit"
    [row] = err.value.partial_results
    assert row["status"] == "cancelled"
    assert row["home_score"] is None and row["away_score"] is None
    assert not any(row["metrics"].values())
    assert row.get("detail_fetch_status") != "not_requested_budget"
    assert datetime.fromisoformat(row["kickoff_at"]).utcoffset() == timedelta(0)
    assert row["home_id"] != row["away_id"]
    assert len(fetch.urls) == 1


@pytest.mark.parametrize("source", ["mlb", "naver"])
def test_malformed_tail_is_not_persistable_output_limit(source):
    if source == "mlb":
        tail = copy.deepcopy(MLB_GAME)
        tail["gamePk"] = 900001
        tail["teams"]["home"]["score"] = None
        fetch = TextFetch(mlb_payload([MLB_GAME, tail]))
        collect = run_mlb
    else:
        tail = {**naver_game(), "gameId": "synthetic-broken", "homeTeamScore": None}
        fetch = TextFetch(naver_payload([naver_game(), tail]))
        collect = run_naver
    with pytest.raises(ResultsSourceError) as err:
        collect(fetch, limit=1)
    assert not isinstance(err.value, PartialResultsError)


@pytest.mark.parametrize("since,until,limit", [
    (date(2026, 9, 4), date(2026, 9, 3), 1),
    (date(2026, 1, 1), date(2026, 2, 1), 1),
    (date(2026, 9, 3), date(2026, 9, 3), 0),
    (date(2026, 9, 3), date(2026, 9, 3), 1001),
    (date(2026, 9, 3), date(2026, 9, 3), True),
    (datetime(2026, 9, 3), date(2026, 9, 3), 1),
])
def test_invalid_bounds_never_fetch(since, until, limit):
    fetch = TextFetch()
    with pytest.raises(ValueError):
        collect_naver(fetch, "KBO", since, until, limit)
    with pytest.raises(ValueError):
        collect_mlb(fetch, since, until, limit)
    assert fetch.urls == []
