import copy
from decimal import Decimal
from itertools import combinations, product
import json
import unittest

import numpy as np

from scripts import local_model_tournament as m


def row(i, season=21, day=None, league='D1', y=0, prices=None):
    return dict(match_id=str(i), season=season, date=day or f'20{season}-09-01', league=league, y=y,
                prices=prices or [1.3, 5., 8.])


class TournamentTests(unittest.TestCase):
    def test_grid_and_baseline(self):
        self.assertEqual(len(m.grid()), 240)
        self.assertEqual(len({c['id'] for c in m.grid()}), 240)
        self.assertIsNone(m.BASELINE['threshold'])

    def test_temporal_date_batch_and_duplicates(self):
        rows = [row(1), row(2, 22), row(3, 23), row(4, 24)]
        train, inner, outer = m.split_fold(rows, 23)
        self.assertEqual([r['season'] for r in train+inner+outer], [21, 22, 23])
        for bad in [rows+[rows[0]], [row(1), row(2, 22, '2021-09-01'), row(3, 23)]]:
            with self.assertRaises(ValueError):
                m.split_fold(bad, 23)
        with self.assertRaises(ValueError):
            m.fit_models(train, train[0]['date'])

    def test_models_normalized_and_no_prediction_labels(self):
        train = [row(i, y=i % 3, prices=[1.6+i % 4*.2, 3.5, 4.]) for i in range(36)]
        models = m.fit_models(train, '2022-01-01')
        future = [row(100, 23), row(101, 23, prices=[3., 3.5, 2.])]
        preds = m.predict_models(models, future)
        clean = [{k: v for k, v in r.items() if k != 'y'} for r in future]
        for name, p in m.predict_models(models, clean).items():
            np.testing.assert_allclose(p, preds[name])
            np.testing.assert_allclose(p.sum(1), 1)

    def test_inner_winner_and_training_ignore_outer_labels(self):
        rows = [row(i, y=i % 3) for i in range(30)]+[row(40, 22), row(41, 23)]
        train, inner, outer = m.split_fold(rows, 23)
        models = m.fit_models(train, inner[0]['date'])
        metrics = {'shin': {'logloss': .9}, 'power': {'logloss': 1.}}
        trials = [dict(stage='inner', config={**m.BASELINE, 'id':'x'}, summary=dict(tickets=25, budget_return=.1)),
                  dict(stage='inner', config={**m.BASELINE, 'id':'y'}, summary=dict(tickets=1, budget_return=10.))]
        winner = m.choose_winners(trials, metrics)
        outer[0]['y'] = 2
        self.assertEqual(winner, m.choose_winners(trials, metrics))
        self.assertEqual(models, m.fit_models(m.split_fold(rows, 23)[0], inner[0]['date']))
        self.assertEqual(winner['policy']['id'], 'x')
        with self.assertRaises(ValueError):
            m.choose_winners([{**trials[0], 'stage':'outer_exploratory'}], metrics)

    def test_cash_equal_budgets_and_low_odds(self):
        rows = [row(1, 23), row(2, 23), row(3, 23, league='SP1')]
        p = np.tile([.75, .15, .1], (3, 1))
        base = m.settle(rows, m.choose(rows, p, m.BASELINE))
        cash = m.settle(rows, m.choose(rows, p, {**m.BASELINE, 'threshold': 5.}))
        self.assertEqual(len(base), len(cash))
        self.assertEqual(m.summary(base)['low_odds_legs'], 2)
        self.assertEqual(m.summary(cash)['cash'], 2)
        self.assertIsNone(m.summary(cash)['roi'])
        self.assertEqual(m.summary(cash)['budget_return'], 0.)

    def test_choice_result_free_distinct_and_exhaustive_equivalence(self):
        rows = [row(i, 23, prices=odds) for i, odds in enumerate([[2., 3., 4.], [1.3, 5., 9.], [3., 3., 2.5]])]
        probs = np.array([[.5, .3, .2], [.7, .2, .1], [.4, .2, .4]])
        clean = [{k: v for k, v in r.items() if k != 'y'} for r in rows]
        for ranking in ('ev', 'maxprob'):
            policy = {**m.BASELINE, 'rank': ranking}
            selected = next(iter(m.choose(clean, probs, policy).values()))
            self.assertEqual(len({a['id'] for a in selected}), 2)
            actual = np.prod([a['probability']*(a['odds'] if ranking == 'ev' else 1) for a in selected])
            brute = max(np.prod([probs[i,j]*(rows[i]['prices'][j] if ranking == 'ev' else 1),
                                 probs[k,l]*(rows[k]['prices'][l] if ranking == 'ev' else 1)])
                        for i,k in combinations(range(3),2) for j,l in product(range(3),repeat=2))
            self.assertAlmostEqual(actual, brute)
            self.assertEqual(m.choose(rows, probs, policy), m.choose(clean[::-1], probs[::-1], policy))

    def test_gate_boundary_and_secondary_range(self):
        rows = [row(1, 23, prices=[1.5,3.,5.]), row(2, 23, prices=[1.5,3.,5.])]
        p = np.tile([.68,.2,.12], (2,1))
        self.assertTrue(next(iter(m.choose(rows,p,{**m.BASELINE,'threshold':.02}).values())))
        self.assertTrue(next(iter(m.choose(rows,p,m.SECONDARY).values())))
        rows[0]['prices'][0] = 1.49
        self.assertFalse(next(iter(m.choose(rows,p,m.SECONDARY).values())))

    def test_week_cluster_pair_and_cash(self):
        a = [dict(date=d, league=l, profit=0., budget=1, stake=0) for d,l in [('2024-01-01','D1'),('2024-01-02','SP1'),('2024-01-08','D1'),('2024-01-09','SP1')]]
        b = copy.deepcopy(a)
        for r,v in zip(b,[100.,-100.,50.,-50.]):
            r['profit'] = v
        self.assertEqual(m.paired_ci(a,b,'profit','budget')['delta']['ci95'], [0.,0.])
        self.assertEqual(m.paired_ci(a,b,'profit','stake')['delta']['valid_bootstraps'], 0)
        with self.assertRaises(ValueError):
            m.paired_ci(a,b[::-1],'profit','budget')

    def test_settlement_rejects_same_game(self):
        rows = [row(1,23),row(2,23)]
        t = m.choose(rows,np.tile([.8,.1,.1],(2,1)),m.BASELINE)
        a = next(iter(t.values()))[0]
        with self.assertRaises(ValueError):
            m.settle(rows,{('D1','2023-09-01'):[a,a]})
        settled = m.settle(rows,t)
        self.assertAlmostEqual(settled[0]['profit'], 1.3*1.3-1)

    def test_parser_complete_season_missing_price_and_bad_result(self):
        header = 'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\n'
        body = ''.join(f'D1,01/09/2023,H{i},A{i},1,0,H,2,3,4\n' for i in range(306))
        self.assertEqual(len(m.parse_source((header+body).encode(), 'D1', 23)), 306)
        missing = m.parse_source((header+body.replace(',2,3,4', ',,3,4', 1)).encode(), 'D1', 23)
        self.assertIsNone(missing[0]['prices'])
        for broken in [body.replace('1,0,H', '1,0,D', 1), body.replace('D1', 'I1', 1), body[:100]]:
            with self.assertRaises((ValueError, KeyError, TypeError)):
                m.parse_source((header+broken).encode(), 'D1', 23)

    def test_saved_tournament_inner_selection_and_settlement(self):
        path = m.ROOT/'docs/research/2026-10-01-local-tournament-results.json'
        if not path.exists():
            self.skipTest('Run artifact not generated')
        j = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(j['grid']['unique_trials'], 240)
        self.assertEqual(len(j['folds']), 2)
        pooled = {k: [] for k in ('baseline', 'selected', 'secondary')}
        for f in j['folds']:
            self.assertEqual(len(f['inner_trials']), 240)
            self.assertEqual(len(f['outer_trials']), 240)
            self.assertEqual(m.choose_winners(f['inner_trials'], f['inner_models']), f['winners'])
            for name, rs in f['records'].items():
                pooled[name].extend(rs)
                self.assertEqual(m.summary(rs), f['policy'][name])
                for r in rs:
                    if r['stake']:
                        a,b = r['pair']
                        self.assertNotEqual(a['id'],b['id'])
                        expected = r['wins']*Decimal(str(a['odds']))*Decimal(str(b['odds']))-1
                        self.assertAlmostEqual(r['profit'],float(expected))
                    else:
                        self.assertEqual(r['profit'],0.)
        for name,rs in pooled.items():
            self.assertEqual(m.summary(rs),j['pooled']['policy'][name])


if __name__ == '__main__':
    unittest.main()
