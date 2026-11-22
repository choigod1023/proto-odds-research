import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from batting_slump_validation import features, predict, simulate


class SlumpTests(unittest.TestCase):
    def rows(self, days=31, won=0):
        return pd.DataFrame([
            dict(date=day, home_team=f"H{i}", away_team=f"A{i}",
                 p=.6, o1=1.7, o0=2.1, y=won)
            for day in pd.date_range("2025-01-01", periods=days) for i in range(3)
        ])

    def test_loss_floor_and_weekly_limit(self):
        for w in (2, 3):
            result = simulate(self.rows(90), "p", w)
            self.assertTrue(all(v >= -50000 for v in result["monthly_net"].values()))
            self.assertEqual(result["worst_month"], -50000)
            self.assertLessEqual(simulate(self.rows(7), "p", 2)["tickets"], 4)

    def test_no_positive_edge_no_bets(self):
        d = self.rows()
        d["p"] = .5
        d["o1"] = d["o0"] = 1.8
        self.assertEqual(simulate(d, "p", 3, True)["tickets"], 0)

    def test_target_stop(self):
        r = simulate(self.rows(won=1), "p", 3, target_stop=True)
        self.assertEqual(r["tickets"], 3)
        self.assertEqual(r["net"], 56700)

    def test_one_game_cannot_form_ticket(self):
        d = self.rows().drop_duplicates("date")
        self.assertEqual(simulate(d, "p", 3)["tickets"], 0)
        self.assertEqual(simulate(pd.concat([d, d]), "p", 3)["tickets"], 0)

    def test_monthly_limit_reserves_full_ticket(self):
        d = self.rows(won=1)
        d["o1"] = 1.6
        # First ticket wins 15,600; further losses must stop at -44,400,
        # not place one more ticket and cross the user's loss budget.
        d.loc[d.date > pd.Timestamp("2025-01-01"), "y"] = 0
        r = simulate(d, "p", 3)
        self.assertEqual(r["tickets"], 7)
        self.assertEqual(r["net"], -44400)

    def test_test_labels_do_not_change_prediction(self):
        tr = self.rows(200).assign(x=lambda d: np.arange(len(d)) % 3)
        tr["y"] = np.arange(len(tr)) % 2
        te = tr.tail(12).copy()
        p1 = predict(tr, te, ["x"])
        te["y"] = 1 - te.y
        np.testing.assert_allclose(p1, predict(tr, te, ["x"]))

    def test_same_date_updates_excluded(self):
        records = []
        for n, date in enumerate(pd.date_range("2025-01-01", periods=22)):
            for home, away in ([("H","A"),("H","B")] if n == 21 else [("H","A")]):
                row = dict(date=date, home_team=home, away_team=away,
                           home_score=3, away_score=2, stadium="P")
                for prefix in ("h_", "a_"):
                    row.update({prefix+k:v for k,v in dict(
                        ab=30,bb=3,kk=6,hit=8,s=6,d=1,t=0,hr_parsed=1,run=3).items()})
                records.append(row)
        frame = pd.DataFrame(records)
        with patch("batting_slump_validation.StarterForm.from_boxscores"):
            a = features(frame, [], [])
            frame.loc[frame.index[-2], "h_run"] = 99
            frame.loc[frame.index[-2], "h_hr_parsed"] = 20
            b = features(frame, [], [])
        np.testing.assert_allclose(a.home_base, b.home_base, equal_nan=True)
        np.testing.assert_allclose(a.home_slump5, b.home_slump5, equal_nan=True)


if __name__ == "__main__":
    unittest.main()
