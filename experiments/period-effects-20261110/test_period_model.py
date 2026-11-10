import unittest
from datetime import date

import numpy as np

from period_model import adjusted, estimates, replay, stats


def fixture(ident, day, y=1, league="test"):
    return {"id": str(ident), "day": day, "sport": "bk", "league": league, "market": "win_loss",
            "period": "full_game", "odds": [1.5, 2.7], "base": [0.6, 0.4], "y": y}


class PeriodTests(unittest.TestCase):
    def test_weekly_embargo_and_league_separation(self):
        past = [fixture(i, "2025-03-03") for i in range(29)]
        same_week = fixture("same-week", "2025-03-11", 0)
        pending = fixture("pending", "2025-03-04")
        different_league = fixture("other", "2025-02-01", league="different")
        friday = fixture("friday", "2025-03-14")
        next_week = fixture("next", "2025-03-17")
        results = {r["id"]: r for r in replay(past + [same_week, pending, different_league, friday, next_week])}
        self.assertEqual(results["friday"]["cutoff"], "2025-03-03")
        self.assertEqual(results["friday"]["support"]["static"], 29)
        self.assertEqual(results["friday"]["predictions"]["static"]["p"], [0.6, 0.4])
        self.assertEqual(results["next"]["support"]["static"], 30)
        self.assertNotEqual(results["next"]["predictions"]["static"]["p"], [0.6, 0.4])

    def test_calendar_wrap_and_excludes_same_year(self):
        dates = [date(2024, 12, 31)] * 30 + [date(2025, 1, 1)] * 31
        p = np.tile([0.6, 0.4], (61, 1))
        y = np.r_[np.ones(30, dtype=int), np.zeros(31, dtype=int)]
        shifts, support = estimates(p, y, np.array([d.toordinal() for d in dates]),
                                    np.array([d.year for d in dates]), np.array([d.month for d in dates]),
                                    date(2025, 1, 20), date(2025, 1, 22))
        self.assertEqual(support["calendar"], 30)
        self.assertGreater(adjusted(p[0], shifts["calendar"])[1], adjusted(p[0], shifts["static"])[1])

    def test_recent_weight_expires_and_combined_falls_back(self):
        old = date(2024, 1, 1)
        p = np.tile([0.6, 0.4], (40, 1))
        shifts, support = estimates(p, np.ones(40, dtype=int), np.full(40, old.toordinal()),
                                    np.full(40, 2024), np.ones(40, dtype=int), date(2025, 1, 20), date(2025, 1, 22))
        self.assertEqual(support["recent"], 0)
        np.testing.assert_array_equal(shifts["recent"], shifts["static"])
        np.testing.assert_array_equal(shifts["combined"], shifts["calendar"])

    def test_reference_settlement_and_duplicate_detection(self):
        r = fixture("one", "2025-03-14", y=0)
        result = stats(list(replay([r])))
        self.assertEqual(result["models"]["reference"]["roi"], 0.5)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            list(replay([r, r]))
