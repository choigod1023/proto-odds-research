"""Executable source, time-causality, frozen selection, and settlement regressions."""
from copy import deepcopy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('kbo_retune', ROOT/'scripts/kbo_retune_research.py')
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)


class KboResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.games, cls.pitching, cls.batting, cls.audit = k.load_facts(k.DEFAULT)
        cls.frozen = json.loads((k.DEFAULT/'frozen-selection.json').read_text(encoding='utf8'))
        cls.features = k.read(k.DEFAULT/'features.jsonl.gz')

    def test_fixed_cohort_and_source_coverage(self):
        self.assertEqual(self.audit['cohort_counts'], {'train': 710, 'validation': 712, 'evaluation': 198})
        ev = [r for r in self.rows if r['role'] == 'evaluation']
        self.assertEqual(sum(r['date'] < '2026-07-01' for r in ev), 152)
        self.assertEqual(sum(r['date'] >= '2026-07-01' for r in ev), 46)
        self.assertEqual(self.audit['pitching_games'], 2656)
        self.assertEqual(self.audit['batter_games_used'], 2656)
        self.assertEqual(len(k.candidate_specs()), 30)

    def test_current_and_future_raw_facts_cannot_change_features(self):
        target = next(r for r in self.rows if r['role'] == 'evaluation' and r['date'] >= '2026-07-01')
        before = k.build_features(target, self.games, self.pitching, self.batting)
        games, pitching, batting = map(deepcopy, [self.games, self.pitching, self.batting])
        changed = [0, 0, 0]
        for i, rows in enumerate([games, pitching, batting]):
            for row in rows:
                if row['date'] >= target['date']:
                    changed[i] += 1
                    if i < 2:
                        row['hg'], row['ag'] = 9999, 8888
                    if i == 1:
                        for side in row['pitchers']:
                            for p in side:
                                p.update(outs=999, runs=999, k=999, bb=999, player='poison')
                    if i == 2:
                        row['totals'] = [dict.fromkeys(t, 999999) for t in row['totals']]
        poisoned_target = dict(target, hg=999, ag=888, prices=[99., 1.01], vote_counts={'away': 100},
                               actual_starters=['poison'], pitchers=[['poison']], lineup=['poison'])
        after = k.build_features(poisoned_target, games, pitching, batting)
        self.assertEqual(before, after)
        self.assertTrue(all(n > 0 for n in changed))
        self.assertTrue(all(p['latest_score_date'] < target['date'] for p in
                            [before['proof']['home'], before['proof']['away']]))

    def test_prior_raw_batting_and_pitching_change_new_variables(self):
        target = next(r for r in self.rows if r['role'] == 'evaluation' and r['date'] >= '2026-07-01')
        before = k.build_features(target, self.games, self.pitching, self.batting)
        batting = deepcopy(self.batting)
        raw = next(r for r in reversed(batting) if r['date'] < target['date'] and
                   r['date'][:4] == '2026' and target['home'] in (r['home'], r['away']))
        side = 0 if raw['home'] == target['home'] else 1
        raw['totals'][side]['bb'] += 10
        after = k.build_features(target, self.games, self.pitching, batting)
        self.assertNotEqual(before['features']['season_ops_proxy'], after['features']['season_ops_proxy'])
        pitching = deepcopy(self.pitching)
        raw = next(r for r in reversed(pitching) if r['date'] < target['date'] and
                   (k.date.fromisoformat(target['date'])-k.date.fromisoformat(r['date'])).days <= 28
                   and target['home'] in (r['home'], r['away']))
        side = 0 if raw['home'] == target['home'] else 1
        raw['pitchers'][side][0]['outs'] += 3
        after = k.build_features(target, self.games, pitching, self.batting)
        self.assertNotEqual(before['features']['starter_innings'], after['features']['starter_innings'])

    def test_shin_matches_committed_main_implementation(self):
        devig_spec = importlib.util.spec_from_file_location('devig_test', ROOT/'src/devig.py')
        devig = importlib.util.module_from_spec(devig_spec)
        devig_spec.loader.exec_module(devig)
        for row in self.rows:
            prices, order = k.prices_order(row)
            self.assertAlmostEqual(k.market(row), devig.market_probabilities(prices)[order.index('home')], places=14)

    def test_prices_order_votes_and_refunds(self):
        row = {'role': 'evaluation', 'prices': [2.15, 1.61], 'order': ['away', 'home'],
               'voters': 10, 'vote_counts': {'home': 7, 'away': 3}, 'hg': 4, 'ag': 2}
        self.assertAlmostEqual(k.reference(row), .9*k.market(row)+.07)
        self.assertEqual(k.settlement(row, .7), (1, 6100, 1))
        self.assertEqual(k.settlement(row, .3), (0, -10000, 1))
        self.assertEqual(k.settlement(dict(row, ag=4), .7), (0, 0, 0))
        self.assertEqual(k.settlement(dict(row, hg=0), .3), (1, 11500, 1))

    def test_nb_is_draw_aware_and_symmetric(self):
        p, draw, tail = k.nb_probability(4.7, 4.7, 3.2)
        self.assertAlmostEqual(p, .5, places=13)
        self.assertGreater(draw, 0)
        self.assertLess(tail, 1e-8)
        a, _, _ = k.nb_probability(6., 3., 3.2)
        b, _, _ = k.nb_probability(3., 6., 3.2)
        self.assertAlmostEqual(a+b, 1., places=13)
        # Independent finite double sum checks strict greater/less and tie handling.
        h = k.nbinom.pmf(np.arange(201), 3.2, 3.2/(3.2+6.))
        v = k.nbinom.pmf(np.arange(201), 3.2, 3.2/(3.2+3.))
        joint = h[:, None]*v[None, :]
        self.assertAlmostEqual(a, np.tril(joint, -1).sum()/(joint.sum()-np.trace(joint)), places=13)

    def test_frozen_selection_uses_validation_only(self):
        selected = k.choose(self.frozen['candidates'])
        self.assertEqual(selected, self.frozen['heads'])
        poisoned = deepcopy(self.frozen['candidates'])
        for i, c in enumerate(poisoned.values()):
            c['evaluation'] = {'accuracy': i, 'roi': 100-i, 'logloss': -i}
        self.assertEqual(k.choose(poisoned), selected)
        for c in self.frozen['candidates'].values():
            self.assertLess(c['selection_fit']['train_last'], '2024-01-01')
            self.assertLess(c['final_fit']['train_last'], '2025-01-01')
        with self.assertRaisesRegex(ValueError, 'future_outcomes_in_training'):
            k.fit([r for r in self.features if r['role'] == 'evaluation'], k.candidate_specs()['logistic:full:ridge0.1'])

    def test_frozen_fits_replay_and_eval_outcome_poisoning(self):
        train = [r for r in self.features if r['role'] == 'train']
        final = [r for r in self.features if r['role'] in {'train', 'validation'}]
        mixed = deepcopy(self.features)
        for row in mixed:
            if row['role'] == 'evaluation':
                row.update(hg=777, ag=888)
        final_poison = [r for r in mixed if r['role'] in {'train', 'validation'}]
        for name in ['logistic:full:ridge0.1', 'nb:s20:season:w0.1', 'pyth:s60:e2:w0.3']:
            spec = k.candidate_specs()[name]
            self.assertEqual(k.fit(train, spec), self.frozen['candidates'][name]['selection_fit'])
            self.assertEqual(k.fit(final, spec), k.fit(final_poison, spec))
            self.assertEqual(k.fit(final, spec), self.frozen['candidates'][name]['final_fit'])

    def test_all_validation_probabilities_and_metrics_replay(self):
        ledger = k.read(k.DEFAULT/'validation_predictions.jsonl')
        val = [r for r in self.features if r['role'] == 'validation']
        self.assertEqual(len(ledger), 712)
        for name, candidate in self.frozen['candidates'].items():
            probabilities = np.array([r['candidate_probabilities'][name] for r in ledger])
            np.testing.assert_allclose(probabilities, k.predict(candidate['selection_fit'], val), rtol=0, atol=0)
            self.assertEqual(candidate['validation'], k.metrics(val, probabilities))

    def test_source_guard_rejects_gap_and_score_corruption(self):
        for kind in ['gap', 'runs']:
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                shutil.copytree(k.DEFAULT/'sources', folder/'sources')
                manifest = deepcopy(json.loads((k.DEFAULT/'input-manifest.json').read_text(encoding='utf8')))
                rows = k.read(folder/'sources/pitching.jsonl.gz')
                idx = next(i for i, r in enumerate(rows) if r['date'] == '2024-04-02')
                if kind == 'gap':
                    del rows[idx]
                else:
                    rows[idx]['pitchers'][0][0]['runs'] += 1
                p = folder/'sources/pitching.jsonl.gz'
                k.write_rows(p, rows)
                manifest['committed_inputs'][p.name] = k.digest(p)
                k.write_json(folder/'input-manifest.json', manifest)
                with self.assertRaisesRegex(ValueError, 'prior28_pitching_gap|historical_target_missing_box' if kind == 'gap'
                                            else 'pitching_run_sum_mismatch'):
                    k.load_facts(folder)

    def test_ledger_independent_settlement_and_period_reconciliation(self):
        result_path = k.DEFAULT/'results.json'
        if not result_path.exists():
            self.skipTest('evaluation not run yet')
        results = json.loads(result_path.read_text(encoding='utf8'))
        ledger = k.read(k.DEFAULT/'predictions.jsonl')
        self.assertEqual(len({r['fixture_id'] for r in ledger}), 198)
        self.assertEqual(results['heads'], self.frozen['heads'])
        for name, c in results['candidates'].items():
            wins = net = settled = 0
            ys, ps = [], []
            for r in ledger:
                p = r['candidate_probabilities'][name]
                if r['hg'] == r['ag']:
                    continue
                side = 'home' if p >= .5 else 'away'
                hit = (r['hg'] > r['ag']) == (side == 'home')
                wins += hit
                settled += 1
                net += int((Decimal(str(r['prices'][r['order'].index(side)]))-1)*10000) if hit else -10000
                ps.append(p)
                ys.append(r['hg'] > r['ag'])
            m = c['evaluation']
            self.assertEqual((m['wins'], m['net_krw'], m['settled']), (wins, net, settled))
            self.assertAlmostEqual(m['accuracy'], wins/settled)
            self.assertAlmostEqual(m['roi'], net/1980000)
            self.assertAlmostEqual(m['brier'], float(np.mean((np.array(ps)-ys)**2)))
        for role in ['primary', 'hit_leader', 'profit_leader']:
            for r in ledger:
                self.assertEqual(r[role]['candidate'], self.frozen['heads'][role])
                self.assertEqual(r[role]['probability'], r['candidate_probabilities'][r[role]['candidate']])
            periods = results['periods']
            self.assertEqual(periods['all']['heads'][role]['metrics']['net_krw'],
                             sum(periods[p]['heads'][role]['metrics']['net_krw'] for p in ['before_july', 'july_onward']))


if __name__ == '__main__':
    unittest.main()
