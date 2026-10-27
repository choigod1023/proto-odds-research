"""Offline regressions for identities, timezones, aliases and official pagination."""

import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.collect_official_international import (
    ALIASES, Cache, collect_epl, collect_kleague, normalize_mlb, result_row,
    save_once, validate, vocabulary,
)


NAMES = {league: set(aliases.values()) for league, aliases in ALIASES.items()}


def mlb(game_id=1, number=1, kickoff="2025-04-24T17:00:00Z"):
    return {
        "gamePk": game_id, "gameType": "R", "gameDate": kickoff,
        "officialDate": "2025-04-24", "gameNumber": number, "doubleHeader": "Y",
        "status": {"detailedState": "Final", "abstractGameState": "Final", "startTimeTBD": False},
        "teams": {
            "home": {"team": {"name": "Chicago Cubs"}, "score": 1},
            "away": {"team": {"name": "Los Angeles Dodgers"}, "score": 4},
        },
    }


def normalize(*games):
    return normalize_mlb({"dates": [{"games": list(games)}]}, "https://example.test", NAMES, 2025)


class Responses:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.requests = []

    def get(self, url, body=None):
        self.requests.append((url, body))
        return next(self.payloads)


class OfficialResultsTest(unittest.TestCase):
    def test_2024_relegated_team_aliases(self):
        r = result_row('EPL', 'old', datetime.fromisoformat('2024-02-10T15:00:00+00:00'),
                       'Luton Town', 'Sheffield United', 1, 3, 'official', NAMES)
        self.assertEqual((r['home'], r['away']), ('루턴타운', '셰필드U'))

    def test_doubleheader_keeps_two_official_ids_even_at_same_time(self):
        rows = normalize(mlb(), mlb(2, 2))
        self.assertEqual(len(rows), 2)
        self.assertEqual(validate(rows, 2025)["mlb_doubleheader_pairs"], 1)
        self.assertEqual({r["game_number"] for r in rows}, {1, 2})

    def test_ambiguous_doubleheader_number_fails(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            validate(normalize(mlb(), mlb(2, 1)), 2025)

    def test_resumed_duplicate_preserves_original_kickoff(self):
        first = mlb()
        first["doubleHeader"] = "N"
        first["resumeDate"] = "2025-04-25T20:00:00Z"
        resumed = copy.deepcopy(first)
        resumed["gameDate"] = first["resumeDate"]
        resumed["resumedFrom"] = first["gameDate"]
        rows = normalize(resumed, first)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kickoff"], "2025-04-25T02:00:00+09:00")

    def test_postponed_abstract_final_is_not_completed(self):
        postponed = mlb()
        postponed["status"]["detailedState"] = "Postponed"
        del postponed["teams"]["home"]["score"]
        self.assertEqual(normalize(postponed), [])

    def test_conflicting_final_scores_fail(self):
        second = mlb()
        second["teams"]["home"]["score"] = 9
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            normalize(mlb(), second)

    def test_final_missing_score_fails(self):
        game = mlb()
        game["teams"]["home"]["score"] = None
        with self.assertRaisesRegex(ValueError, "Invalid score"):
            normalize(game)

    def test_tbd_time_is_explicit_not_silently_precise(self):
        game = mlb(2, 2)
        game["status"]["startTimeTBD"] = True
        self.assertTrue(normalize(game)[0]["kickoff_is_tbd"])

    def test_completed_early_and_postseason_included_spring_excluded(self):
        game = mlb()
        game["status"]["detailedState"] = "Completed Early"
        game["gameType"] = "W"
        self.assertEqual(len(normalize(game)), 1)
        game["gameType"] = "S"
        self.assertEqual(normalize(game), [])

    def test_kst_year_boundaries(self):
        included = mlb(kickoff="2024-12-31T16:00:00Z")
        excluded = mlb(2, kickoff="2025-12-31T16:00:00Z")
        rows = normalize(included, excluded)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2025-01-01")

    def test_london_dst_and_winter(self):
        for raw, expected in [("2025-01-01T17:30:00", "2025-01-02T02:30:00+09:00"),
                              ("2025-08-15T20:00:00", "2025-08-16T04:00:00+09:00")]:
            kickoff = datetime.fromisoformat(raw).replace(tzinfo=ZoneInfo("Europe/London"))
            row = result_row("EPL", "1", kickoff, "Liverpool", "Arsenal", 2, 1, "url", NAMES)
            self.assertEqual(row["kickoff"], expected)

    def test_unverified_alias_fails(self):
        with self.assertRaisesRegex(ValueError, "Unverified"):
            normalize_mlb({"dates": [{"games": [mlb()]}]}, "url", {"MLB": set()}, 2025)

    def test_csv_score_and_handicap_cleanup_and_year_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.csv"
            path.write_text("year,league,home,away\n2025,MLB,시카컵스 -1.5,4 LA다저스\n2024,MLB,old,old\n", encoding="utf-8-sig")
            self.assertEqual(vocabulary(path, 2025)["MLB"], {"시카컵스", "LA다저스"})

    def test_epl_cursor_encoded_and_repeated_cursor_rejected(self):
        cache = Responses([{"data": [], "pagination": {"_next": "a+/="}},
                           {"data": [], "pagination": {"_next": "a+/="}}])
        with self.assertRaisesRegex(ValueError, "Repeated"):
            collect_epl(cache, NAMES, 2025)
        self.assertIn("_next=a%2B%2F%3D", cache.requests[1][0])

    def test_kleague_competition_identity_and_promotion_exclusion(self):
        game = {"year": 2025, "leagueId": 1, "meetSeq": 1, "gameId": 1,
                "endYn": "Y", "gameStatus": "FE", "gameDate": "2025.01.01",
                "gameTime": "14:00", "homeTeamName": "서울", "awayTeamName": "울산",
                "homeGoal": 0, "awayGoal": 0}
        promotion = dict(game, meetSeq=3, homeTeamName="수원")
        pages = [{"resultCode": "200", "data": {"scheduleList": []}} for _ in range(24)]
        pages[0]["data"]["scheduleList"] = [game, promotion]
        pages[12]["data"]["scheduleList"] = [dict(game, leagueId=2, meetSeq=2,
                                                   homeTeamName="수원", awayTeamName="인천")]
        rows = collect_kleague(Responses(pages), NAMES, 2025)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["official_id"], rows[1]["official_id"])
        self.assertEqual(rows[1]["home"], "수원삼성")

    def test_offline_missing_cache_never_requests_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                Cache(directory, offline=True).get("https://invalid.example")

    def test_different_output_requires_explicit_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.json"
            save_once(path, b"[]")
            with self.assertRaises(ValueError):
                save_once(path, b"{}")
            self.assertEqual(json.loads(path.read_bytes()), [])


if __name__ == "__main__":
    unittest.main()
