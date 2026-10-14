import copy
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import hashlib
import json
import unittest

import numpy as np

from scripts import evaluate_draw_calibration_holdout as m


def row(i, year=22, day=None, y=0, league='D1', prices=None):
    return dict(match_id=str(i), season=year, date=day or f'20{year}-09-01', league=league,
                prices=prices or [2., 3., 4.], y=y)


class DrawCalibrationTests(unittest.TestCase):
    def test_normalization_and_home_away_conditional_invariance(self):
        p = np.array([[.5, .2, .3], [.1, .3, .6]])
        for b in [-2., 0., 2.]:
            q = m.calibrate(p, b)
            np.testing.assert_allclose(q.sum(1), 1)
            np.testing.assert_allclose(q[:, 0]/q[:, 2], p[:, 0]/p[:, 2])
        np.testing.assert_allclose(m.calibrate(p, 0), p)

    def test_fit_never_uses_test_outcomes(self):
        rows = [row(i, y=i % 3) for i in range(30)]+[row(31, 23)]
        train, _ = m.split(rows)
        b = m.fit_draw(train)
        rows[-1]['y'] = 2
        self.assertEqual(b, m.fit_draw(m.split(rows)[0]))
        with self.assertRaises(ValueError):
            m.fit_draw(rows)
        self.assertAlmostEqual(m.calibrate(m.probabilities(train), b)[:, 1].mean(), 1/3, places=10)

    def test_overlap_duplicate_and_unknown_league_rejected(self):
        for rows in [[row(1), row(2, 23, '2021-01-01')], [row(1), row(1, 23)],
                     [row(1), row(2, 23, league='I1')]]:
            with self.assertRaises(ValueError):
                m.split(rows)

    def test_selection_without_results_and_permutation(self):
        rows = [row(i, 23) for i in range(3)]
        p = np.tile([.5, .3, .2], (3, 1))
        expected = m.select(rows, p)
        clean = [{k: v for k, v in r.items() if k != 'y'} for r in rows]
        self.assertEqual(expected, m.select(clean, p))
        self.assertEqual(expected, m.select(clean[::-1], p[::-1]))
        for r in rows:
            r['y'] = 2
        self.assertEqual(expected, m.select(rows, p))
        pair = next(iter(expected.values()))
        self.assertEqual([a['id'] for a in pair], ['0', '1'])
        self.assertTrue(all(a['choice'] != 0 for a in pair))

    def test_cash_ties_and_budget_preservation(self):
        rows = [row(1, 23, prices=[3., 3., 3.]), row(2, 23), row(3, 23, league='SP1')]
        p = m.probabilities(rows)
        tickets = m.select(rows, p)
        self.assertTrue(all(not pair for pair in tickets.values()))
        missing = row(4, 23, '2023-09-02')
        missing['prices'] = None
        settled = m.settle(rows+[missing], tickets)
        self.assertEqual(len(settled), 3)
        self.assertEqual(sum(r['raw'] for r in settled), 0)

    def test_settlement_and_actual_rounding_boundaries(self):
        self.assertEqual(m.payout(1.01, 2.01), 2.1)  # 2.0301 -> 2.03 -> 2.1
        self.assertEqual(m.payout(1.001, 2.), 2.)  # 2.002 -> 2.00 -> 2.0
        self.assertEqual(m.payout(1.05, 2.), 2.1)
        rows = [row(1, 23, y=1), row(2, 23, y=1)]
        tickets = m.select(rows, np.tile([.3, .5, .2], (2, 1)))
        r = m.settle(rows, tickets)[0]
        self.assertEqual((r['wins'], r['stake'], r['raw']), (1, 1, 8.))
        rows[1]['y'] = 0
        self.assertEqual(m.settle(rows, tickets)[0]['raw'], -1.)
        pair = next(iter(tickets.values()))
        with self.assertRaises(ValueError):
            m.settle(rows, {('D1', '2023-09-01'): [pair[0], pair[0]]})

    def test_week_cluster_pairs_and_constant_ratio(self):
        rows = [dict(date='2024-01-01', num=2., den=1), dict(date='2024-01-02', num=2., den=1),
                dict(date='2024-01-08', num=4., den=2)]
        self.assertEqual(m.interval(rows, 'num', 'den'), [2., 2.])
        a = m.settle([row(1, 23)], {})
        self.assertEqual(m.paired_summary(a, copy.deepcopy(a))['raw']['ci95'], [0., 0.])
        b = copy.deepcopy(a)
        b[0]['date'] = '2023-09-02'
        with self.assertRaises(ValueError):
            m.paired_summary(a, b)

    def test_bad_source_and_bad_probability_rejected(self):
        with self.assertRaises(ValueError):
            m.parse_source(b'Div,Date,HomeTeam\n', 'D1', 23)
        with self.assertRaises(ValueError):
            m.calibrate([[.5, .5, .5]], .2)
        with self.assertRaises(ValueError):
            m.calibrate([[float('nan'), .3, .7]], .2)

    def test_source_coverage_results_and_missing_odds(self):
        header = 'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\n'
        body = ''.join(f'D1,01/09/2023,H{i},A{i},1,0,H,2,3,4\n' for i in range(306))
        rows = m.parse_source((header+body).encode(), 'D1', 23)
        self.assertEqual(len(rows), 306)
        for broken in [body.replace('1,0,H', '1,0,D', 1), body.replace('D1', 'I1', 1),
                       body.replace('01/09/2023', '01/09/2022', 1), body+body.splitlines()[0]+'\n']:
            with self.assertRaises(ValueError):
                m.parse_source((header+broken).encode(), 'D1', 23)
        missing = m.parse_source((header+body.replace(',2,3,4', ',,3,4', 1)).encode(), 'D1', 23)
        self.assertIsNone(missing[0]['prices'])

    def test_leagues_same_week_are_one_cluster(self):
        # Independent row resampling would create nonzero intervals here.
        rows = [dict(date=day, num=value, den=1) for day, value in
                [('2024-01-01', 100), ('2024-01-02', -100), ('2024-01-08', 50), ('2024-01-09', -50)]]
        self.assertEqual(m.interval(rows, 'num', 'den'), [0., 0.])

    def test_saved_artifact_reconciles_independently(self):
        path = m.ROOT/'docs/research/2026-10-01-draw-calibration-results.json'
        if not path.exists():
            self.skipTest('Experiment artifact not generated yet')
        j = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(j['code_sha256'], hashlib.sha256((m.ROOT/'scripts/evaluate_draw_calibration_holdout.py').read_bytes()).hexdigest())
        for name, rows in j['records'].items():
            summary = j['results']['all']['policy'][name]
            self.assertEqual(summary['tickets'], sum(r['stake'] for r in rows))
            self.assertEqual(summary['wins'], sum(r['wins'] for r in rows))
            self.assertAlmostEqual(summary['profit_raw'], sum(r['raw'] for r in rows))
            for r in rows:
                if not r['pair']:
                    self.assertEqual((r['raw'], r['rounded'], r['stake']), (0., 0., 0))
                    continue
                a, b = r['pair']
                self.assertNotEqual(a['id'], b['id'])
                product = Decimal(str(a['odds']))*Decimal(str(b['odds']))
                rounded = product.quantize(Decimal('.01'), rounding=ROUND_DOWN).quantize(Decimal('.1'), rounding=ROUND_CEILING)
                self.assertAlmostEqual(r['raw'], float(product)*r['wins']-1)
                self.assertAlmostEqual(r['rounded'], float(rounded)*r['wins']-1)
        expected = (j['results']['all']['policy']['draw']['profit_raw']-j['results']['all']['policy']['base']['profit_raw'])/len(j['records']['base'])
        self.assertAlmostEqual(expected, j['results']['all']['paired']['raw']['value'])


if __name__ == '__main__':
    unittest.main()
