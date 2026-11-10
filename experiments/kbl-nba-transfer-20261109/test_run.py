import unittest

import numpy as np

from lineup import expected_pairs
from run import eligible_for, features, metrics, parse_chart, possessions, projected_minutes, score
from stints import reconstruct


class TransferTests(unittest.TestCase):
    def test_chart_normalizes_basket_and_checks_shooter(self):
        players = {"a": ("01", 60, np.array([2, 0, 0, 1, 0, 0, 0, 0]))}
        body = {"shootLog": [{"pcode": "a", "tcode": "01", "logs": [
            {"x": 60, "y": 206, "d": "2", "o": "O"},
            {"x": 666, "y": 206, "d": "1", "o": "X"},
        ]}]}
        counts, errors = parse_chart(body, players)
        self.assertFalse(errors)
        self.assertEqual(counts["a"].tolist(), [2, 2])
        body["shootLog"][0]["tcode"] = "02"
        counts, errors = parse_chart(body, players)
        self.assertEqual(counts, {})
        self.assertEqual(errors["chart_identity"], 1)
        self.assertEqual(errors["chart_missing_shooter"], 1)

    def test_chart_does_not_impute_a_disagreeing_box(self):
        players = {"a": ("01", 60, np.array([1, 0, 0, 1, 0, 0, 0, 0]))}
        body = {"shootLog": [{"pcode": "a", "tcode": "01", "logs": [
            {"x": 60, "y": 206, "d": "2", "o": "X"},
        ]}]}
        counts, errors = parse_chart(body, players)
        self.assertEqual(counts, {})
        self.assertEqual(errors["chart_box_counts"], 1)

    def test_history_never_uses_target_or_recent_box_to_fill_gaps(self):
        schedule = {"01": [
            {"day": "2025-01-01", "id": "older", "box": []},
            *({"day": f"2025-01-{day:02d}", "id": str(day), **({"box": []} if day != 5 else {})}
              for day in range(2, 12)),
            {"day": "2025-01-12", "id": "too_recent", "box": []},
            {"day": "2025-01-15", "id": "target", "box": []},
        ]}
        self.assertIsNone(features({"day": "2025-01-15", "teams": ("01", "02")}, schedule))

    def test_score_preserves_component_totals_for_identical_inputs(self):
        box = np.array([40, 20, 10, 20, 8, 8, 6, 9])
        self.assertAlmostEqual(score(box, box, possessions(box)), 72)

    def test_pair_weights_respect_player_minutes(self):
        minutes = projected_minutes(dict(a=48, b=36, c=34, d=29, e=24, f=14, g=6))
        pairs = expected_pairs(minutes)
        self.assertAlmostEqual(sum(pairs.values()), 10)
        for pid, duration in minutes.items():
            self.assertLessEqual(duration, 40)
            self.assertAlmostEqual(sum(weight for pair, weight in pairs.items() if pid in pair), duration / 10)

    def test_prices_follow_the_selected_side(self):
        rows = [
            {"day": "2025-01-01", "y": 0, "odds": [1.50, 2.30], "prob": {"market": 0.6, "model": 0.4}},
            {"day": "2025-01-02", "y": 1, "odds": [1.40, 2.60], "prob": {"market": 0.7, "model": 0.7}},
        ]
        report = metrics(rows, ("market", "model"))
        self.assertEqual(report["model"]["hits"], 2)
        self.assertAlmostEqual(report["model"]["roi"], 0.85)
        self.assertEqual(report["model"]["vs_market"]["changed"], 1)

    def test_partial_cohort_keeps_strict_rows_and_features_separate(self):
        row = {"chart_ok": False, "partial_ok": True, "x": [1, 2, 3, 4], "partial_x": 5}
        self.assertEqual(eligible_for([row], "spatial"), [])
        self.assertEqual(eligible_for([row], "spatial_player_audit")[0]["x"], [1, 2, 3, 5])
        self.assertEqual(row["x"], [1, 2, 3, 4])

    def test_stints_replay_player_totals_and_reject_missing_quarters(self):
        cast = []
        players = [(team, f"{team}{i}") for team in ("01", "02") for i in range(5)]
        box = [
            {"player": {"tcode": team, "pname": pid, "pcode": pid}, "startFlag": "1",
             "homeAway": str(int(team)), "records": {"playMin": 40, "playSec": 0, "score": 2 if pid == "010" else 0}}
            for team, pid in players
        ]
        for quarter in range(1, 5):
            events = [("101", 10, team, pid) for team, pid in players] + [("001", 10, "", "")]
            if quarter == 1:
                events.append(("201", 9, "01", "010"))
            events += [("009", 0, "", "")] + [("102", 0, team, pid) for team, pid in players]
            for action, minute, team, pid in events:
                cast.append(dict(n=len(cast), q=f"Q{quarter}", a=action, m=minute, s=0, t=team, p=pid))
        stints = reconstruct(cast, box)
        self.assertEqual(sum(seconds for _, _, seconds, _ in stints), 2400)
        self.assertEqual(sum(margin for _, _, _, margin in stints), 2)
        with self.assertRaisesRegex(ValueError, "periods"):
            reconstruct([row for row in cast if row["q"] != "Q1"], box)
