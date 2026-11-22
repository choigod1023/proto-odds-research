import unittest

from experiment import recurring, replay, rest_difference, summarize, training_state


def game(ident, day, season="S47", phase="regular", home_rest=1, away_rest=1, y=0):
    return {"id": str(ident), "offer_id": f"G101:{ident}", "day": day, "kickoff": day + "T19:00:00+09:00",
            "season": season, "phase": phase, "home_rest": home_rest, "away_rest": away_rest, "y": y,
            "line": -2.5, "odds": [1.6, 2.1], "team_ids": ["06", "60"], "buy_reject": "0"}


class ExperimentTests(unittest.TestCase):
    def test_calendar_wraps_month_and_excludes_target_season(self):
        target = game("target", "2026-01-09")
        old = game("old", "2024-12-10", season="S45")
        same = game("same", "2025-12-10")
        self.assertEqual(recurring([old, same], target), [old])

    def test_rest_difference_is_capped_and_missing_is_neutral(self):
        self.assertEqual(rest_difference(game(1, "2025-12-01", home_rest=10, away_rest=1)), 0.8)
        self.assertEqual(rest_difference(game(1, "2025-12-01", home_rest=None)), 0)

    def test_replay_does_not_use_embargoed_or_future_outcomes(self):
        old = [game(i, "2025-03-15", season="S45", home_rest=0, away_rest=2) for i in range(30)]
        target = game("target", "2026-03-11")
        pending = game("pending", "2026-03-05")
        future = game("future", "2026-03-18")
        a = next(r for r in replay(old + [pending, target, future]) if r["id"] == "target")
        b = next(r for r in replay(old + [dict(pending, y=1), dict(target, y=1), dict(future, y=1)])
                 if r["id"] == "target")
        self.assertEqual(a["training_cutoff"], "2026-03-02")
        self.assertEqual(a["support"]["train"], 30)
        self.assertEqual(a["probabilities"], b["probabilities"])

    def test_no_playoff_training_falls_back_to_the_same_nonstage_model(self):
        past = [game(i, "2025-03-15", season="S45") for i in range(40)]
        model, _ = training_state(past, game("target", "2026-03-11"))
        self.assertEqual(model["phase"], model["static"])
        self.assertEqual(model["phase_rest"], model["rest"])

    def test_settlement_uses_the_price_for_the_selected_side(self):
        row = game("target", "2025-12-19", y=1)
        report = summarize(list(replay([row])))
        self.assertAlmostEqual(report["models"]["market"]["roi"], 0.6)
        self.assertEqual(report["models"]["market"]["hits"], 1)
        self.assertNotIn("frozen", report["models"])
