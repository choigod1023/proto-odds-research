import copy
from datetime import date, timedelta
import unittest

import numpy as np

from scripts.evaluate_calibration_underdog import (
    POLICIES, choose_tickets, settle, summarize, paired_budget_delta,
    cluster_interval, train_models, predict, select_model, split_data, ev_diagnostics,
)
from scripts.evaluate_external_temporal_roi import features


def event(i, day='2025-08-15', y=0, prices=None, season='2025-26'):
    return {'match_id': str(i), 'date': day, 'day': date.fromisoformat(day),
            'y': y, 'prices': prices or [1.5, 4., 6.], 'season': season,
            'x': [1., .2, .1, .9, -.2], 'market': np.array([.65, .2, .15]),
            'home_team': 'A', 'away_team': 'B', 'goals': (2, 0)}


class CalibrationUnderdogTests(unittest.TestCase):
    def test_selection_never_needs_outcomes(self):
        rows = [event(1), event(2)]
        blind = [{k: v for k, v in r.items() if k not in ('y', 'goals')} for r in rows]
        for policy in POLICIES:
            self.assertEqual(choose_tickets(rows, [[.7, .2, .1]]*2, policy),
                             choose_tickets(blind, [[.7, .2, .1]]*2, policy))

    def test_nonfavorite_ev_and_control_are_different(self):
        rows = [event(1), event(2)]
        p = [[.65, .2, .15]]*2
        self.assertEqual(choose_tickets(rows, p, 'ev03_nonfavorite')['2025-08-15'], [])
        legs = choose_tickets(rows, p, 'nonfavorite_control')['2025-08-15']
        self.assertEqual([leg['choice'] for leg in legs], [1, 1])
        self.assertTrue(all(leg['ev'] < 0 for leg in legs))

    def test_positive_ev_underdog_can_be_bet(self):
        legs = choose_tickets([event(1), event(2)], [[.5, .3, .2]]*2,
                              'ev03_nonfavorite')['2025-08-15']
        self.assertEqual(len(legs), 2)
        self.assertTrue(all(leg['nonfavorite'] for leg in legs))

    def test_tied_favorites_are_not_underdogs(self):
        legs = choose_tickets([event(1, prices=[2., 2., 5.]), event(2, prices=[2., 2., 5.])],
                              [[.4, .4, .2]]*2, 'nonfavorite_control')['2025-08-15']
        self.assertEqual([leg['choice'] for leg in legs], [2, 2])

    def test_boundary_220_excluded(self):
        rows = [event(1, prices=[2.2, 4., 5.]), event(2, prices=[2.2, 4., 5.])]
        self.assertFalse(choose_tickets(rows, [[.7, .2, .1]]*2, 'p60_range')['2025-08-15'])

    def test_two_legs_one_ticket_and_cash(self):
        rows = [event(i) for i in range(4)]+[event(9, day='2025-08-16')]
        tickets = choose_tickets(rows, [[.7, .2, .1]]*5, 'p60_range')
        daily = settle(rows, tickets)
        result = summarize(rows, tickets, daily)
        self.assertEqual(result['tickets'], 1)
        self.assertEqual(result['wins'], 1)
        self.assertAlmostEqual(result['roi_raw'], 1.25)
        self.assertAlmostEqual(result['roi_rounded'], 1.3)
        self.assertAlmostEqual(result['budget_return_raw'], .625)

    def test_no_bets_is_null_roi_and_zero_profit(self):
        rows = [event(1)]
        tickets = choose_tickets(rows, [[.7, .2, .1]], 'p60_range')
        result = summarize(rows, tickets, settle(rows, tickets))
        self.assertIsNone(result['roi_raw'])
        self.assertIsNone(result['roi_raw_ci95_week_cluster'])
        self.assertEqual(result['profit_raw_units'], 0)

    def test_bad_inputs_rejected(self):
        with self.assertRaises(ValueError):
            choose_tickets([event(1), event(1)], [[.7, .2, .1]]*2, 'p60_range')
        with self.assertRaises(ValueError):
            choose_tickets([event(1)], [], 'p60_range')
        with self.assertRaises(ValueError):
            choose_tickets([event(1)], [[float('nan'), .2, .1]], 'p60_range')
        with self.assertRaises(ValueError):
            choose_tickets([event(1)], [[.7, .2, .1]], 'typo')

    def test_settlement_rejects_same_match_pair(self):
        tickets = choose_tickets([event(1), event(2)], [[.7, .2, .1]]*2, 'p60_range')
        tickets['2025-08-15'][1]['id'] = '1'
        with self.assertRaises(ValueError):
            settle([event(1), event(2)], tickets)

    def test_paired_identical_strategies_have_zero_interval(self):
        daily = [{'date': '2025-08-15', 'raw': -1.}, {'date': '2025-08-22', 'raw': 2.}]
        result = paired_budget_delta(daily, daily)
        self.assertEqual(result['ci95_week_cluster'], [0., 0.])
        self.assertEqual(result['budget_return_difference'], 0.)

    def test_cluster_ratio_keeps_week_together(self):
        records = [{'date': '2025-08-15', 'profit': 2., 'stake': 1},
                   {'date': '2025-08-16', 'profit': -1., 'stake': 1}]
        self.assertEqual(cluster_interval(records, 'profit', 'stake'), [.5, .5])

    def test_models_normalize_and_do_not_read_prediction_labels(self):
        train = [event(i, y=i%3) for i in range(30)]
        calibration = [event(i, y=i%3) for i in range(12)]
        models = train_models(train, calibration)
        rows = [event(90), event(91)]
        no_labels = [{k: v for k, v in r.items() if k != 'y'} for r in rows]
        a, b = predict(models, rows), predict(models, no_labels)
        self.assertEqual(len(a), 5)
        for name in a:
            np.testing.assert_allclose(a[name], b[name])
            np.testing.assert_allclose(a[name].sum(axis=1), 1.)

    def test_temporal_split_rejects_overlap(self):
        rows = [event(i, season=s) for i, s in enumerate(('2019-20', '2023-24', '2024-25', '2025-26'))]
        with self.assertRaises(ValueError):
            split_data(rows)

    def test_test_labels_cannot_change_selection_model(self):
        rows = [event(0, day='2019-08-01', season='2019-20'),
                event(1, day='2023-08-01', season='2023-24'),
                event(2, day='2024-08-01', season='2024-25'), event(3)]
        before = split_data(rows)
        altered = copy.deepcopy(rows); altered[-1]['y'] = 2
        after = split_data(altered)
        predictions = {'a': np.array([[.8, .1, .1]]), 'b': np.array([[.1, .1, .8]])}
        self.assertEqual(select_model(before['selection'], predictions),
                         select_model(after['selection'], predictions))

    def test_future_scores_cannot_change_past_features(self):
        rows = [event(i, day=str(date(2025, 1, 1)+timedelta(days=8*i))) for i in range(4)]
        altered = copy.deepcopy(rows); altered[-1]['goals'] = (0, 8)
        np.testing.assert_equal([r['x'] for r in features(rows)], [r['x'] for r in features(altered)])

    def test_diagnostics_distinguish_unpaired_candidate_from_no_candidate(self):
        rows = [event(1)]
        probabilities = [[.5, .3, .2]]
        metrics = ev_diagnostics(rows, probabilities)
        self.assertEqual(metrics['events_with_ev03_nonfavorite'], 1)
        self.assertFalse(choose_tickets(rows, probabilities, 'ev03_nonfavorite')['2025-08-15'])


if __name__ == '__main__':
    unittest.main()
