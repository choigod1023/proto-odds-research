import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("rd_soccer", Path(__file__).resolve().parents[1] / "scripts/rd_soccer_features.py")
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)


def game(day, players=range(11), key=None):
    return {"date": day, "team": "A", "home": "A", "away": "B",
            "xi": list(map(str, players)), "source_id": key or day}


class SoccerFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.history = [game("2026-03-01"), game("2026-03-05", range(1, 12))]
        self.decision = "2026-03-07T14:00:00+09:00"

    def test_future_season_append_invariance(self):
        before = rd.prior_features(self.history, "A", self.decision)
        future = [game("2026-12-01", range(100, 111)), game("2027-01-01")]
        self.assertEqual(before, rd.prior_features(self.history + future, "A", self.decision))

    def test_target_and_same_day_not_used(self):
        f, h, _ = rd.prior_features(self.history + [game("2026-03-07", range(100, 111))], "A", self.decision)
        self.assertEqual(f["observed_games"], 2)
        self.assertEqual(f["previous_churn"], 1)
        self.assertAlmostEqual(f["previous_xi_mean_starts14"], 21 / 11)
        self.assertTrue(all(g["date"] < "2026-03-07" for g in h))

    def test_missing_not_zero_and_stale_not_false_zero_workload(self):
        self.assertEqual(rd.prior_features([], "A", self.decision)[0], {})
        f, _, reason = rd.prior_features(self.history, "A", "2026-06-01T00:00:00+09:00")
        self.assertNotIn("previous_xi_mean_starts14", f)
        self.assertIn("stale", reason)

    def test_no_prior_season_regulars(self):
        f, _, _ = rd.prior_features([game("2025-12-31")], "A", self.decision)
        self.assertEqual(f, {})

    def test_diagnostic_separation_and_no_invented_capture(self):
        roster = {"event_id": "kleague:2026:1:1", "cohort": "test", "league": "K1",
                  "kickoff": "2026-03-08T14:00:00+09:00", "available_at": self.decision,
                  "home": "A", "away": "B", "target": 1, "odds": [1, 2, 3]}
        rows, diagnostic, _ = rd.extract([roster], self.history + [game("2026-03-08")], "abc")
        self.assertTrue(diagnostic)
        self.assertTrue(all("current_xi" not in k for k in rows[0]["features"]))
        for e in rows[0]["feature_evidence"].values():
            self.assertIsNone(e["observed_at"])
            self.assertIsNone(e["max_fact_time"])
            self.assertEqual(e["availability_tier"], "retrospective_lagged")
            self.assertEqual(e["max_fact_date"], "2026-03-05")
            self.assertEqual(e["latest_source_game_date"], "2026-03-05")
        self.assertEqual(rows[0]["source_meta"]["fact_dates"], ["2026-03-01", "2026-03-05"])
        self.assertEqual(rows[0]["source_meta"]["source_game_dates"]["2026-03-05"], "2026-03-05")
        # Parent's separate date-only view: March 5 + 3 days misses March 7.
        latest = date.fromisoformat(next(iter(rows[0]["feature_evidence"].values()))["max_fact_date"])
        self.assertGreater(latest + timedelta(days=3), date(2026, 3, 7))
        self.assertEqual(latest + timedelta(days=3), date(2026, 3, 8))
        self.assertEqual(next(iter(diagnostic[0]["feature_evidence"].values()))["max_fact_date"], "2026-03-08")
        self.assertNotIn("target", rows[0])
        self.assertNotIn("odds", rows[0])

    def test_reject_naive_and_past_forward_intake(self):
        with self.assertRaises(ValueError):
            rd.stamp("2026-03-01T12:00:00")
        with self.assertRaises(ValueError):
            rd.capture("https://www.kleague.com/", Path("."), kickoff="2020-01-01T00:00:00Z")
        with self.assertRaises(ValueError):
            rd.capture("https://example.com/", Path("."))

    def test_ambiguous_history_rejected(self):
        f, _, reason = rd.prior_features(self.history + [game("2026-03-01", key="duplicate")], "A", self.decision)
        self.assertEqual(f, {})
        self.assertIn("ambiguous", reason)

    def test_archive_exact_identity_and_explicit_alias(self):
        h = [{**game("2026-03-01"), "team": "울산"},
             {**game("2026-03-01"), "team": "전북"}]
        r = {"event_id": "opaque-original-archive-id", "cohort": "historical_market_proxy",
             "league": "K1", "home": "울산HDFC", "away": "전북현대",
             "kickoff": "2026-03-08T14:00:00+09:00", "available_at": self.decision}
        rows, _, _ = rd.extract([r], h, "sha", archive_league="K1")
        self.assertEqual(rows[0]["event_id"], r["event_id"])
        self.assertEqual(rows[0]["cohort"], "historical_market_proxy")
        self.assertIn("soccer_prior_home_previous_xi_regular_share", rows[0]["features"])

    def test_adapter_drops_outcomes_and_duplicate_player(self):
        players = [{"playerId": str(i), "goal": 5} for i in range(11)]
        raw = {"id": {"date": "2026-03-01", "home": "A", "away": "B", "home_score": 99,
                      "data": {"home": {"players": players}, "away": {"players": players}}}}
        h, _ = rd.adapt(raw)
        raw["id"]["home_score"] = 0
        raw["id"]["data"]["home"]["players"][0]["goal"] = 0
        self.assertEqual(h, rd.adapt(raw)[0])
        raw["id"]["data"]["home"]["players"] = [players[0]] * 11
        self.assertTrue(rd.adapt(raw)[1])

    def intake(self, change=None):
        expected = {"event_id": "kleague:2026:1:200", "source_fixture_id": "200",
                    "league": "K1", "kickoff": "2026-10-31T15:00:00+09:00", "home": "A", "away": "B"}
        body = {"fixture": dict(expected), "starters": {"home": [str(i) for i in range(11)],
                "away": [str(i) for i in range(11, 22)]}}
        if change:
            change(body)
        payload = json.dumps(body).encode()
        clock = {"url": "https://www.kleague.com/match.do?gameId=200",
                 "request_started_at": "2026-10-31T13:59:59+09:00",
                 "observed_at": "2026-10-31T14:00:00+09:00", "source_sha256": rd.sha(payload)}
        return payload, clock, expected

    def test_typed_intake_validates_without_inferred_publication(self):
        payload, clock, expected = self.intake()
        r = rd.parse_xi_intake(payload, clock, expected, "2026-10-31T14:30:00+09:00")
        self.assertEqual(r["availability_tier"], "unverified")
        self.assertIsNone(r["publication_at"])
        self.assertIsNone(r["max_fact_time"])
        self.assertEqual(r["observed_at"], clock["observed_at"])

    def test_typed_intake_rejects_identity_and_xi_errors(self):
        changes = [lambda b: b["fixture"].update(event_id="wrong"),
                   lambda b: b["starters"]["home"].pop(),
                   lambda b: b["starters"]["home"].__setitem__(1, "0"),
                   lambda b: b["starters"].pop("away"),
                   lambda b: b["starters"]["away"].__setitem__(0, "0")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                rd.parse_xi_intake(*self.intake(change), "2026-10-31T14:30:00+09:00")

    def test_typed_intake_rejects_tampered_hash_and_late_clock(self):
        payload, clock, expected = self.intake()
        with self.assertRaises(ValueError):
            rd.parse_xi_intake(payload + b" ", clock, expected, "2026-10-31T14:30:00+09:00")
        clock["observed_at"] = "2026-10-31T14:31:00+09:00"
        with self.assertRaises(ValueError):
            rd.parse_xi_intake(payload, clock, expected, "2026-10-31T14:30:00+09:00")


if __name__ == "__main__":
    unittest.main()
