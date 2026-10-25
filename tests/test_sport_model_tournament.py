import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location('sport_tournament', Path(__file__).resolve().parents[1] / 'scripts/sport_model_tournament.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class SportTournamentTests(unittest.TestCase):
    def test_catalog_is_fixed_unique(self):
        self.assertEqual(len(m.names()), 37)
        self.assertEqual(len(set(m.names())), 37)
        self.assertEqual(len(m.names()) * len(m.RULES) * 2, 296)
        self.assertFalse({'y', 'outcome', 'home_score', 'away_score'} & set(m.FORM))

    def test_selection_does_not_use_test_and_can_reject_all(self):
        scores = {'a': {'accuracy': .6, 'brier': .5}, 'b': {'accuracy': .6, 'brier': .4}}
        papers = {'a': dict(tickets=29, net=100000, profitable_months=8, max_drawdown=10000),
                  'b': dict(tickets=50, net=-1000, profitable_months=8, max_drawdown=10000)}
        self.assertEqual(m.select(scores, papers), dict(accuracy='b', calibration='b', profit=None))
        papers['a']['tickets'] = 30
        self.assertEqual(m.select(scores, papers)['profit'], 'a')

    def test_fit_predict_binary_and_three_way(self):
        for k in (2, 3):
            d = pd.DataFrame({'market': [[1/k]*k]*60, 'y': np.arange(60)%k})
            p = m.fit_predict(d, d.iloc[:5], dict(name='cal', cols=[], offset=True, ridge=.01))
            self.assertEqual(p.shape, (5,k))
            np.testing.assert_allclose(p.sum(1), 1.)
            np.testing.assert_allclose(p, 1/k, atol=1e-6)

    def test_target_labels_do_not_change_predictions(self):
        d = pd.DataFrame({'market': [[.6,.4]]*40, 'y': np.arange(40)%2, 'elo_diff': np.arange(40)})
        target = d.iloc[:10].copy()
        s = dict(name='rating', cols=['elo_diff'], offset=True, ridge=.01)
        p = m.fit_predict(d, target, s)
        target['y'] = 1 - target.y
        np.testing.assert_array_equal(p, m.fit_predict(d, target, s))


if __name__ == '__main__':
    unittest.main()
