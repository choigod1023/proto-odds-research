import unittest

from run import choose, prepare, replay


def offer(gid, day="2025-01-10", line=2.5, y=1, league="KBL", **other):
    return {
        "id": str(gid), "day": day, "sport": "bk", "league": league, "market": "total",
        "period": "full_including_extra", "line": line, "odds": [1.5, 3.0],
        "y": y, "prob": {"market": [2 / 3, 1 / 3], "candidate": [0.25, 0.75]}, **other,
    }


class RoutingTest(unittest.TestCase):
    def test_month_freezes_and_ignores_unreleased_results(self):
        past = [offer(i) for i in range(39)]
        pending = offer("pending", "2025-01-26")
        feb = offer("feb", "2025-02-10")
        mar = offer("mar", "2025-03-10")
        result = {r["id"]: r for r in replay(past + [pending, feb, mar])}
        self.assertEqual(result["feb"]["cutoff"], "2025-01-25")
        self.assertEqual(result["feb"]["policies"]["accuracy"]["model"], "market")
        self.assertEqual(result["mar"]["policies"]["accuracy"]["model"], "candidate")
        self.assertEqual(result["mar"]["policies"]["accuracy"]["net"], 2.0)

    def test_same_line_requires_own_history_then_backs_off(self):
        past = list(prepare([offer(i) for i in range(40)]))
        target = next(prepare([offer("next", line=3.5)]))
        decision = choose(past, target, False)
        self.assertEqual(decision["scope"], "league_product")
        target = next(prepare([offer("next")]))
        self.assertEqual(choose(past, target, False)["scope"], "same_line")

    def test_guard_uses_dates_not_repeated_matches_in_one_day(self):
        past = list(prepare([offer(i) for i in range(40)]))
        target = next(prepare([offer("next")]))
        self.assertEqual(choose(past, target, True)["model"], "market")
        past = list(prepare([offer(i, f"2025-01-{i % 20 + 1:02d}") for i in range(40)]))
        self.assertEqual(choose(past, target, True)["model"], "candidate")

    def test_reference_can_be_a_frozen_model_and_candidate_is_paired(self):
        p = {"market": [2 / 3, 1 / 3], "frozen": [0.2, 0.8]}
        data = [offer(i, prob=p, reference="frozen") for i in range(40)]
        p2 = dict(p, candidate=[0.1, 0.9])
        target = offer("next", "2025-02-01", prob=p2, reference="frozen")
        r = next(r for r in replay(data + [target]) if r["id"] == "next")
        self.assertEqual(r["policies"]["accuracy"]["model"], "frozen")
        self.assertEqual(r["policies"]["market"]["hit"], 0)

    def test_separate_league_and_duplicate_rejection(self):
        past = [offer(i, league="NBA") for i in range(40)]
        r = next(r for r in replay(past + [offer("next", "2025-03-01")]) if r["id"] == "next")
        self.assertEqual(r["policies"]["accuracy"]["model"], "market")
        with self.assertRaisesRegex(ValueError, "Duplicate offer"):
            list(prepare([offer(1), offer(1, line=3.5)]))
