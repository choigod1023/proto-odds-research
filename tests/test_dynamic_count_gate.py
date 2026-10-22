from datetime import date, timedelta
import sys
from pathlib import Path
import copy
import unittest
from unittest.mock import patch
import numpy as np
from scipy.optimize._numdiff import approx_derivative

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
import dynamic_count_gate as gate
import process_outcome_gate as outcome


def games():
    return [{"id": str(i), "day": date(2024,1,1)+timedelta(days=i),
             "teams": ("A", "B"), "counts": [[10+i%3, 4], [8+i%2, 3]]} for i in range(80)]


class DynamicTests(unittest.TestCase):
    def test_gradient(self):
        rng = np.random.default_rng(1)
        x = rng.normal(size=(20,6)); y = rng.integers(0,20,size=20)
        b = rng.normal(size=6)*.05; w = np.ones(20)/20
        numerical = approx_derivative(lambda z: gate.objective(z,x,y,w)[0], b).ravel()
        np.testing.assert_allclose(gate.objective(b,x,y,w)[1], numerical, atol=1e-6)

    def test_current_same_day_and_future_outcomes_not_features(self):
        data = games(); day = data[70]["day"]
        first, _ = gate.forecast(data, [data[70]], day, "dynamic")
        changed = copy.deepcopy(data)
        for item in changed[69:]:
            item["counts"] = [[200,100],[100,50]]
        second, _ = gate.forecast(changed,[changed[70]],day,"dynamic")
        np.testing.assert_array_equal(first,second)

    def test_lag_and_window(self):
        data = games(); day = data[-1]["day"]
        selected = gate.eligible(data,day)
        self.assertEqual(max(g["day"] for g in selected),day-timedelta(days=2))
        self.assertTrue(all((day-g["day"]).days <=365 for g in selected))

    def test_unseen_team_neutral_effect(self):
        x = gate.design([{"teams": ("new", "B")}], {"A":0,"B":1})
        self.assertEqual(x[0,2:4].sum(),0)
        self.assertEqual(x[0,4:].sum(),1)

    def test_insufficient_not_empty_success(self):
        with self.assertRaisesRegex(ValueError,"insufficient"):
            gate.forecast(games()[:10],games()[:1],date(2024,2,1),"dynamic")

    def test_output_positive(self):
        data = games()
        for mode in ("dynamic","venue_shrink"):
            values,_ = gate.forecast(data,[data[-1]],data[-1]["day"],mode)
            self.assertTrue(np.all(np.isfinite(values) & (values>0)))

    def test_market_duplicate_fails_closed(self):
        row={"league":"K리그1","event_id":"K리그1|FC서울|대구FC|2024-06-01T19:00:00+09:00",
             "kickoff":"2024-06-01T19:00:00+09:00","odds":[2,3,4]}
        with self.assertRaisesRegex(ValueError,"duplicate"):
            outcome.build_rows({},[row,row])

    def test_favorite_probability_and_test_label_invariance(self):
        rows = [{'features':[2+i%3/10,2.1+i%4/10],'odds':[1.8,3.4,4.1],'target':i%3}
                for i in range(200)]
        test = rows[:10]
        before = outcome.fit_favorite(rows,test)
        after = outcome.fit_favorite(rows,[{**r,'target':(r['target']+1)%3} for r in test])
        np.testing.assert_allclose(before,after)
        np.testing.assert_allclose(before.sum(axis=1),1)
        self.assertTrue(np.all(before>0))

    def test_rolling_refit_respects_two_day_cutoff(self):
        rows = [{'kickoff':str(date(2024,1,1)+timedelta(days=i))+'T19:00:00+09:00',
                 'features':[2,2], 'odds':[2,3,4], 'target':i%3} for i in range(202)]
        seen = []
        def predictor(train,test):
            seen.append((train,test))
            return np.tile([.5,.3,.2],(len(test),1))
        _,_,audit = outcome.rolling_probabilities(rows,rows[-2:],predictor)
        self.assertEqual(len(audit),2)
        for train,test in seen:
            cutoff = datetime_from_day(test[0]['kickoff'])-timedelta(days=2)
            self.assertTrue(all(datetime_from_day(r['kickoff']) <= cutoff for r in train))


def datetime_from_day(stamp):
    return date.fromisoformat(stamp[:10])


if __name__ == "__main__":
    unittest.main()
