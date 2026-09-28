import copy
from datetime import date, timedelta
import unittest

import numpy as np

from scripts.evaluate_mixed_underdog import (
    add_features, choose, design, incremental, parse_price, predict, signature,
    selection_diagnostics,
)
from scripts.evaluate_calibration_underdog import settle


def fixture():
    return [{'match_id': str(i), 'league': 'I1', 'day': date(2024, 1, 1),
             'date': '2024-01-01', 'season': 23, 'home_team': f'h{i}', 'away_team': f'a{i}',
             'prices': [1.5, 4., 6.], 'best': [1.6, 4.2, 6.2],
             'market': np.array([.6, .25, .15]), 'x': [1., .2, .1, 0, 0],
             'rest_gap': .1, 'goals': (1, 0), 'y': 0} for i in range(3)]


class MixedUnderdogTests(unittest.TestCase):
    def test_compositions_and_distinct_matches(self):
        rows = fixture()
        p = np.tile([.6, .25, .15], (3, 1))
        for kind, n in [('FF', 0), ('FD', 1), ('DD', 2)]:
            legs = choose(rows, p, kind, 'base', 'control', 'b365')['2024-01-01']
            self.assertEqual(sum(x['nonfavorite'] for x in legs), n)
            self.assertNotEqual(legs[0]['id'], legs[1]['id'])

    def test_fixed_bonus_cancels_with_fixed_composition(self):
        rng = np.random.default_rng(44)
        for _ in range(30):
            rows = fixture()
            p = rng.dirichlet([3, 2, 1], 3)
            for kind in ('FF', 'FD', 'DD'):
                self.assertEqual(choose(rows, p, kind, 'base', 'control', 'b365'),
                                 choose(rows, p, kind, 'fixed_rank', 'control', 'b365'))

    def test_bonus_can_change_unrestricted_pair_without_probability_inflation(self):
        rows = fixture()
        p = np.tile([.68, .25, .07], (3, 1))
        base = choose(rows, p, 'any', 'base', 'control', 'b365')
        extra = choose(rows, p, 'any', 'fixed_rank', 'control', 'b365')
        self.assertNotEqual(signature(base['2024-01-01']), signature(extra['2024-01-01']))
        for leg in extra['2024-01-01']:
            self.assertEqual(leg['probability'], p[int(leg['id']), leg['choice']])

    def test_rank_bonus_does_not_relax_ev_gate(self):
        rows = fixture()
        p = np.tile([.65, .2525, .0975], (3, 1))
        strict = choose(rows, p, 'DD', 'fixed_rank', 'ev03', 'b365')
        relaxed = choose(rows, p, 'DD', 'relaxed_gate', 'ev03', 'b365')
        self.assertEqual(strict['2024-01-01'], [])
        self.assertEqual(len(relaxed['2024-01-01']), 2)
        self.assertLess(relaxed['2024-01-01'][0]['ev'], .03)

    def test_outcome_invariance(self):
        rows = fixture()
        p = np.tile([.65, .25, .1], (3, 1))
        a = choose(rows, p, 'any', 'fixed_rank', 'control', 'b365')
        for r in rows:
            del r['y']
            del r['goals']
        self.assertEqual(a, choose(rows, p, 'any', 'fixed_rank', 'control', 'b365'))

    def test_tied_favorite_is_not_underdog(self):
        rows = fixture()
        for r in rows:
            r['prices'] = [2., 4., 2.]
        legs = choose(rows, np.tile([.3, .2, .5], (3, 1)), 'FF', 'base', 'control', 'b365')['2024-01-01']
        self.assertTrue(all(leg['choice'] == 2 and not leg['nonfavorite'] for leg in legs))

    def test_bad_input(self):
        rows = fixture()
        p = np.tile([.6, .3, .1], (3, 1))
        for broken in [p[:2], p*2, p*np.nan]:
            with self.assertRaises(ValueError):
                choose(rows, broken, 'any', 'base', 'control', 'b365')
        with self.assertRaises(ValueError):
            choose([rows[0]]*3, p, 'any', 'base', 'control', 'b365')
        rows[0]['league'] = 'F1'
        with self.assertRaises(ValueError):
            choose(rows, p, 'any', 'base', 'control', 'b365')

    def test_empty_day_cash(self):
        rows = fixture()[:1]
        tickets = choose(rows, [[.6, .3, .1]], 'any', 'base', 'control', 'b365')
        self.assertEqual(settle(rows, tickets)[0]['stake'], 0)

    def test_low_odds_allowed(self):
        rows = fixture()
        for r in rows:
            r['prices'] = [1.3, 5., 9.]
        legs = choose(rows, np.tile([.9, .06, .04], (3, 1)), 'FF', 'base', 'ev03', 'b365')['2024-01-01']
        self.assertEqual(len(legs), 2)
        self.assertTrue(all(leg['odds'] < 1.5 for leg in legs))

    def test_conditional_design_direction_and_zero_model(self):
        rows = fixture()
        x = design(rows, True)
        self.assertEqual(x.shape, (3, 3, 15))
        self.assertEqual(x[0, 0, 9], 0)
        self.assertEqual(x[0, 2, 12], -.2)
        model = {'conditional': True, 'temperature': 1., 'weights': [0.]*15}
        np.testing.assert_allclose(predict(model, rows), [r['market'] for r in rows])
        for r in rows:
            del r['y']
        np.testing.assert_allclose(predict(model, rows), [r['market'] for r in rows])

    def test_future_outcomes_and_same_day_do_not_change_features(self):
        rows = fixture()
        for i, r in enumerate(rows):
            r['day'] += timedelta(days=i*10)
            r['date'] = r['day'].isoformat()
            r['home_team'] = 'home'
            r['away_team'] = 'away'
        before = add_features(rows)
        altered = copy.deepcopy(rows)
        altered[-1]['goals'] = (0, 20)
        altered[-1]['y'] = 2
        after = add_features(altered)
        np.testing.assert_allclose([r['x'] for r in before], [r['x'] for r in after])
        self.assertEqual([r['rest_gap'] for r in before], [r['rest_gap'] for r in after])

    def test_replacements_are_not_new_budget_days(self):
        rows = fixture()
        p = np.tile([.68, .25, .07], (3, 1))
        base = choose(rows, p, 'any', 'base', 'control', 'b365')
        extra = choose(rows, p, 'any', 'fixed_rank', 'control', 'b365')
        stats = incremental(rows, extra, base)
        self.assertEqual(stats['newly_bet_days'], 0)
        self.assertEqual(stats['replaced_days'], 1)
        self.assertEqual(stats['tickets'], 1)

    def test_price_parser_rejects_invalid_values(self):
        for value in ('', None, 'NaN', 'inf', '1', '-1'):
            self.assertIsNone(parse_price(value))
        self.assertEqual(parse_price('1.49'), 1.49)

    def test_better_price_on_same_picks_and_draw_counts(self):
        rows = fixture()
        for r in rows:
            r['y'] = 1
        p = np.tile([.6, .3, .1], (3, 1))
        tickets = choose(rows, p, 'DD', 'base', 'control', 'b365')
        d = selection_diagnostics(rows, tickets)
        self.assertEqual(d['draw_legs'], 2)
        self.assertEqual(d['pairs_with_draw'], 1)
        self.assertEqual(d['pairs_without_draw'], 0)
        self.assertAlmostEqual(d['same_picks_best_price_raw_roi'], 4.2*4.2-1)

    def test_missing_price_result_still_updates_future_features(self):
        rows = fixture()[:2]
        rows[0]['prices'] = None
        rows[1]['home_team'] = rows[0]['home_team']
        rows[1]['away_team'] = rows[0]['away_team']
        rows[1]['day'] += timedelta(days=10)
        rows[1]['date'] = rows[1]['day'].isoformat()
        featured = add_features(rows)
        self.assertEqual(len(featured), 1)
        self.assertGreater(featured[0]['x'][1], 0)

    def test_league_histories_are_isolated(self):
        rows = fixture()[:1]
        foreign = copy.deepcopy(rows[0])
        foreign.update(league='F1', match_id='foreign', goals=(0, 20), y=2)
        foreign['day'] -= timedelta(days=10)
        foreign['date'] = foreign['day'].isoformat()
        a = add_features(rows)[0]
        b = next(r for r in add_features([foreign]+rows) if r['league'] == 'I1')
        np.testing.assert_allclose(a['x'], b['x'])


if __name__ == '__main__':
    unittest.main()
