"""Offline contract tests: all provider payloads here are compact synthetic fixtures."""
import json
from pathlib import Path
import sys
import unittest
from datetime import date
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sports_sources_soccer import (  # noqa: E402
    FOTMOB, STATSBOMB, collect_fotmob, collect_statsbomb, parse_fotmob_fixtures,
)

START, END = date(2026, 8, 1), date(2026, 8, 31)
LEAGUE_URL = f"{FOTMOB}/leagues/9080/matches/k-league-1"
SB_INDEX = f"{STATSBOMB}/matches/43/106.json"


def html(props):
    return "<script type='application/json' id='__NEXT_DATA__'>" + json.dumps(
        {"props": {"pageProps": props}}
    ) + "</script>"


def fixture(event_id=10, utc="2026-08-30T10:30:00Z", **status):
    return {"id": str(event_id), "pageUrl": f"/matches/home-vs-away/code{event_id}#{event_id}",
            "status": {"finished": True, "cancelled": False, "utcTime": utc, **status}}


def fotmob_match(event_id=10):
    return {
        "general": {"matchId": str(event_id), "finished": True,
                    "matchTimeUTCDate": "2026-08-30T10:30:00.000Z",
                    "homeTeam": {"id": 101, "name": "Home"},
                    "awayTeam": {"id": 202, "name": "Away"}},
        "header": {"status": {"finished": True, "cancelled": False},
                   "teams": [{"id": 202, "score": 1}, {"id": 101, "score": 0}]},
        "content": {"stats": {"Periods": {
            "FirstHalf": {"stats": [{"key": "expected_goals", "type": "text", "stats": [9, 9]}]},
            "All": {"stats": [
                {"key": "expected_goals", "type": "title", "stats": [None, None]},
                {"key": "expected_goals", "type": "text", "stats": ["0.81", "0.89"]},
                {"key": "expected_goals_non_penalty", "type": "text", "stats": ["0.81", "0.89"]},
            ]}}}},
    }


class Fetch:
    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if not self.bodies:
            raise AssertionError("unexpected extra fetch")
        result = self.bodies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def sb_match(event_id=10, day="2022-12-18", status="available"):
    return {"match_id": event_id, "match_date": day, "kick_off": "17:00:00.000",
            "match_status": status, "home_score": 3, "away_score": 3,
            "competition": {"competition_id": 43, "competition_name": "FIFA World Cup"},
            "season": {"season_id": 106, "season_name": "2022"},
            "home_team": {"home_team_id": 779, "home_team_name": "Argentina"},
            "away_team": {"away_team_id": 771, "away_team_name": "France"}}


def shot(event_id, team=779, xg=0.2, period=1, penalty=False):
    return {"id": str(event_id), "type": {"id": 16, "name": "Shot"},
            "team": {"id": team}, "period": period,
            "shot": {"statsbomb_xg": xg,
                     "type": {"id": 88 if penalty else 87, "name": "Penalty" if penalty else "Open Play"}}}


class FotmobTests(unittest.TestCase):
    def collect(self, fetch, limit=1):
        return collect_fotmob(fetch, since=START, until=END, limit=limit)

    def test_common_record_uses_all_period_provider_metrics_and_native_ids(self):
        fetch = Fetch(html({"matches": [fixture()]}), html(fotmob_match()))
        record, = self.collect(fetch)
        self.assertEqual(fetch.urls, [LEAGUE_URL, f"{FOTMOB}/matches/home-vs-away/code10"])
        self.assertEqual(record["metrics"], {"home": {"xg": .81, "npxg": .81}, "away": {"xg": .89, "npxg": .89}})
        self.assertEqual((record["home_id"], record["away_id"]), ("101", "202"))
        self.assertEqual((record["home_score"], record["away_score"]), (0, 1))
        self.assertEqual(record["kickoff_at"], "2026-08-30T10:30:00+00:00")
        self.assertEqual(record["provider"], "fotmob")
        self.assertEqual(record["league"], "kleague1")
        self.assertEqual(record["sport"], "축구")
        self.assertEqual(record["status"], "final")
        self.assertEqual(record["score_unit"], "goals")
        self.assertNotIn("observed_at", record)

    def test_fixture_sort_utc_filter_and_deduplicate_before_limit(self):
        older = fixture(11, "2026-08-29T20:00:00Z")
        latest = fixture(12, "2026-08-30T10:30:00Z")
        # Local September 1 is August 31 UTC and must be included.
        boundary = fixture(13, "2026-09-01T01:00:00+09:00")
        source = html({"matches": [older, latest, latest, boundary,
                                   fixture(14, "2026-09-01T00:00:00Z")]})
        self.assertEqual([m["id"] for m in parse_fotmob_fixtures(source, START, END)], ["13", "12", "11"])
        match = fotmob_match(13)
        match["general"]["matchTimeUTCDate"] = "2026-09-01T01:00:00+09:00"
        fetch = Fetch(source, html(match))
        record, = self.collect(fetch)
        self.assertEqual(record["game_date"], "2026-08-31")
        self.assertEqual(len(fetch.urls), 2)

    def test_nonfinal_cancelled_awarded_abandoned_not_fetched(self):
        matches = [fixture(1, finished=False), fixture(2, cancelled=True),
                   fixture(3, awarded=True), fixture(4, abandoned=True),
                   fixture(5, reason={"short": "Abandoned"}), fixture(6, finished="true")]
        fetch = Fetch(html({"matches": matches}))
        self.assertEqual(self.collect(fetch, limit=10), [])
        self.assertEqual(fetch.urls, [LEAGUE_URL])

    def test_detail_rechecks_final_and_never_backfills_request_budget(self):
        for status in ({"finished": False}, {"finished": True, "cancelled": True}):
            with self.subTest(status=status):
                match = fotmob_match(12)
                match["header"]["status"] = status
                fetch = Fetch(html({"matches": [fixture(11), fixture(12)]}), html(match))
                self.assertEqual(self.collect(fetch), [])
                self.assertEqual(len(fetch.urls), 2)

    def test_detail_date_is_filtered_again(self):
        match = fotmob_match()
        match["general"]["matchTimeUTCDate"] = "2026-09-01T00:00:00Z"
        self.assertEqual(self.collect(Fetch(html({"matches": [fixture()]}), html(match))), [])

    def test_no_xg_does_not_discard_score_or_fabricate_zero(self):
        for content in (None, {}, {"stats": None}, {"stats": {"Periods": {"All": None}}}):
            with self.subTest(content=content):
                match = fotmob_match()
                match["content"] = content
                record, = self.collect(Fetch(html({"matches": [fixture()]}), html(match)))
                self.assertEqual(record["metrics"], {})
                self.assertEqual(record["metric_status"], "not_available")
                self.assertEqual(record["home_score"], 0)

    def test_missing_xg_and_npxg_remain_missing_but_measured_zero_survives(self):
        match = fotmob_match()
        stats = match["content"]["stats"]["Periods"]["All"]["stats"]
        stats[1]["stats"] = ["0.00", None]
        stats[2]["stats"] = [None, None]
        record, = self.collect(Fetch(html({"matches": [fixture()]}), html(match)))
        self.assertEqual(record["metrics"], {"home": {"xg": 0.0}})
        self.assertEqual(record["metric_status"], "partial")

    def test_mismatched_public_matchup_url_raises(self):
        with self.assertRaisesRegex(ValueError, "ID differs"):
            self.collect(Fetch(html({"matches": [fixture()]}), html(fotmob_match(99))))

    def test_wrong_league_or_cancelled_general_cannot_become_final(self):
        match = fotmob_match()
        match["general"]["parentLeagueId"] = 9074
        with self.assertRaisesRegex(ValueError, "league differs"):
            self.collect(Fetch(html({"matches": [fixture()]}), html(match)))
        match = fotmob_match()
        match["general"]["cancelled"] = True
        self.assertEqual(self.collect(Fetch(html({"matches": [fixture()]}), html(match))), [])

    def test_missing_metrics_consume_budget_and_records_stay_latest_first(self):
        latest = fotmob_match(12)
        latest["content"] = None
        fetch = Fetch(html({"matches": [fixture(10), fixture(12), fixture(11, "2026-08-29T10:30:00Z")]}),
                      html(latest), html(fotmob_match(10)))
        records = self.collect(fetch, limit=2)
        self.assertEqual([r["event_id"] for r in records], ["12", "10"])
        self.assertEqual(records[0]["metrics"], {})
        self.assertEqual(len(fetch.urls), 3)

    def test_unknown_timezone_rejected(self):
        fetch = Fetch(html({"matches": [fixture(utc="2026-08-30T10:30:00")]}))
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.collect(fetch)
        self.assertEqual(len(fetch.urls), 1)

    def test_unsafe_match_paths_never_fetched(self):
        for path in ("/matches/../api/secret", "/matches/a/b?redirect=/api/x", "/matches/a/%2e%2e", "/matches/a/b\\api"):
            with self.subTest(path=path):
                item = fixture()
                item["pageUrl"] = path
                fetch = Fetch(html({"matches": [item]}))
                with self.assertRaises(ValueError):
                    self.collect(fetch)
                self.assertEqual(len(fetch.urls), 1)

    def test_invalid_scores_or_metrics_raise(self):
        for value in (True, -1, "NaN", "Infinity", "not a number"):
            with self.subTest(value=value):
                match = fotmob_match()
                match["content"]["stats"]["Periods"]["All"]["stats"][1]["stats"][0] = value
                with self.assertRaises((ValueError, TypeError)):
                    self.collect(Fetch(html({"matches": [fixture()]}), html(match)))
        match = fotmob_match()
        match["header"]["teams"][0]["score"] = None
        with self.assertRaises(ValueError):
            self.collect(Fetch(html({"matches": [fixture()]}), html(match)))

    def test_errors_and_challenges_propagate_without_retry(self):
        error = HTTPError(LEAGUE_URL, 403, "Forbidden", {}, None)
        for bodies in ((error,), ("<html>captcha</html>",),
                       (html({"matches": [fixture()]}), error)):
            fetch = Fetch(*bodies)
            with self.assertRaises((HTTPError, ValueError)):
                self.collect(fetch)
            self.assertEqual(len(fetch.urls), len(bodies))


class StatsbombTests(unittest.TestCase):
    def collect(self, matches=None, events=None, **kwargs):
        fetch = Fetch(json.dumps(matches if matches is not None else [sb_match()]),
                      json.dumps(events if events is not None else [shot(1), shot(2, 771)]))
        return collect_statsbomb(fetch, 43, 106, **{"limit": 1, **kwargs}), fetch

    def test_xg_and_npxg_sum_include_extra_time_exclude_shootout(self):
        events = [shot(1, xg=.2), shot(2, xg=.8, penalty=True),
                  shot(3, xg=.3, period=4), shot(4, team=771, xg=.4),
                  shot(5, xg=.9, period=5, penalty=True), shot(6, team=771, xg=.9, period=5)]
        records, fetch = self.collect(events=events)
        record, = records
        self.assertEqual(record["metrics"], {"home": {"xg": 1.3, "npxg": .5}, "away": {"xg": .4, "npxg": .4}})
        self.assertEqual(fetch.urls, [SB_INDEX, f"{STATSBOMB}/events/10.json"])
        self.assertEqual(record["sample_scope"], "historical_open_data")
        self.assertEqual((record["competition"], record["season"], record["league"]), (43, 106, "43"))
        self.assertEqual((record["home_id"], record["away_id"]), ("779", "771"))
        self.assertEqual((record["home_score"], record["away_score"]), (3, 3))
        self.assertEqual(record["metric_scope"], "shot_statsbomb_xg_periods_1_to_4")
        self.assertEqual(record["status"], "final")

    def test_timezone_is_never_fabricated_and_source_time_preserved(self):
        records, _ = self.collect()
        record, = records
        self.assertIsNone(record["kickoff_at"])
        self.assertEqual(record["game_date"], "2022-12-18")
        self.assertEqual(record["time_precision"], "date")
        self.assertEqual(record["source_kickoff"], "17:00:00.000")
        self.assertNotIn("observed_at", record)

    def test_final_only_sort_filter_deduplicate_and_limit_requests(self):
        records, fetch = self.collect(matches=[sb_match(2, "2022-12-01"), sb_match(3), sb_match(3),
                                              sb_match(4, status="scheduled"), sb_match(5, status="deleted"),
                                              sb_match(6, "2022-12-19")],
                                      since=date(2022, 12, 18), until=date(2022, 12, 18))
        self.assertEqual([r["event_id"] for r in records], ["3"])
        self.assertEqual(len(fetch.urls), 2)

    def test_empty_or_missing_xg_is_not_zero_or_partial_sum(self):
        for events in ([], [shot(1, xg=None)], [shot(1), shot(2, xg=None)],
                       [shot(1, period=5)]):
            with self.subTest(events=events):
                records, _ = self.collect(events=events)
                self.assertEqual(records[0]["metrics"], {})
                self.assertEqual(records[0]["metric_status"], "not_available")

    def test_measured_zero_and_partial_coverage(self):
        records, _ = self.collect(events=[shot(1, xg=0), shot(2, team=771, xg=None)])
        self.assertEqual(records[0]["metrics"], {"home": {"xg": 0.0, "npxg": 0.0}})
        self.assertEqual(records[0]["metric_status"], "partial")

    def test_unknown_shot_type_does_not_invent_npxg(self):
        event = shot(1)
        del event["shot"]["type"]
        records, _ = self.collect(events=[event])
        self.assertEqual(records[0]["metrics"], {"home": {"xg": .2}})

    def test_wrong_ids_duplicate_shots_invalid_metrics_raise(self):
        for events in ([shot(1, team=999)], [shot(1), shot(1)], [shot(1, period=6)],
                       [shot(1, xg=float("nan"))], [shot(1, xg=-.1)]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                self.collect(events=events)
        match = sb_match()
        match["competition"]["competition_id"] = 99
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.collect(matches=[match])

    def test_fetch_and_json_errors_raise_without_retry(self):
        error = HTTPError(SB_INDEX, 403, "Forbidden", {}, None)
        for bodies in ((error,), ("invalid json",), ("{}",),
                       (json.dumps([sb_match()]), error), (json.dumps([sb_match()]), "{}")):
            fetch = Fetch(*bodies)
            with self.assertRaises((HTTPError, ValueError)):
                collect_statsbomb(fetch, 43, 106, 1)
            self.assertEqual(len(fetch.urls), len(bodies))


class BoundsTests(unittest.TestCase):
    def test_zero_does_no_io_and_bad_bounds_fail_before_io(self):
        for collect in (lambda f, n: collect_fotmob(f, since=START, until=END, limit=n),
                        lambda f, n: collect_statsbomb(f, 43, 106, n)):
            fetch = Fetch()
            self.assertEqual(collect(fetch, 0), [])
            for n in (-1, True, None, 1.5):
                with self.subTest(n=n), self.assertRaises(ValueError):
                    collect(fetch, n)
            self.assertEqual(fetch.urls, [])
        with self.assertRaises(ValueError):
            collect_fotmob(Fetch(), since=END, until=START, limit=1)
        with self.assertRaises(ValueError):
            collect_statsbomb(Fetch(), 43, 106, 1, END, START)
        with self.assertRaises(ValueError):
            collect_statsbomb(Fetch(), "43", 106, 1)


if __name__ == "__main__":
    unittest.main()
