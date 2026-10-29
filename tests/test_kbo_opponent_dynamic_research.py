"""Causal replay, real-source accounting, and frozen model regression tests."""
import copy
from datetime import date
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import unittest

import numpy as np

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('kbo_dynamic', ROOT/'scripts/kbo_opponent_dynamic_research.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def game(day='2023-04-01', home='LG', away='KT', hg=5, ag=2, gid='one'):
    return dict(date=day, home=home, away=away, hg=hg, ag=ag, source_fixture_id=gid)


class StateTests(unittest.TestCase):
    def test_candidate_budget_and_independent_families(self):
        self.assertEqual(len(m.candidate_specs()), 26)
        self.assertEqual({s['family'] for s in m.state_specs().values()}, {'elo', 'attack_defence'})

    def test_elo_win_against_strong_opponent_updates_more(self):
        changes = []
        for strength in [-1., 1.]:
            state = m.OnlineState(m.state_specs()['e1'])
            state.m[m.TEAMS.index('KT'), 0] = strength
            state.advance('2023-04-01')
            state.update([game()])
            changes.append(state.m[m.TEAMS.index('LG'), 0])
        self.assertGreater(changes[1], changes[0])

    def test_elo_margin_weight_and_tie(self):
        movements = []
        for score in [3, 15]:
            state = m.OnlineState(m.state_specs()['e0'])
            state.advance('2023-04-01')
            state.update([game(hg=score, ag=2)])
            movements.append(state.m[m.TEAMS.index('LG'), 0])
            self.assertAlmostEqual(state.m.sum(), 0.)
        self.assertGreater(movements[1], movements[0])
        state = m.OnlineState(m.state_specs()['e0'])
        state.advance('2023-04-01')
        state.update([game(hg=2, ag=2)])
        self.assertTrue(np.isfinite(state.m).all())

    def test_attack_defence_direction_and_uncertainty(self):
        state = m.OnlineState(m.state_specs()['a1'])
        state.advance('2023-04-01')
        state.update([game(hg=10, ag=0)])
        h, a = m.TEAMS.index('LG'), m.TEAMS.index('KT')
        self.assertGreater(state.m[h, 0], 0)
        self.assertGreater(state.m[h, 1], 0)
        self.assertLess(state.m[a, 0], 0)
        self.assertLess(state.m[a, 1], 0)
        self.assertTrue(np.all(state.v[[h, a]] < .08))
        before = state.observe_pair('LG', 'KT')
        state.v[:] = .25
        after = state.observe_pair('LG', 'KT')
        self.assertLess(after['state_logit'], before['state_logit'])

    def test_score_innovation_depends_on_opponent_defence(self):
        change = []
        for defence in [-.5, .5]:
            state = m.OnlineState(m.state_specs()['a1'])
            state.m[m.TEAMS.index('KT'), 1] = defence
            state.advance('2023-04-01')
            state.update([game(hg=5)])
            change.append(state.m[m.TEAMS.index('LG'), 0])
        self.assertGreater(change[1], change[0])

    def test_rest_decay_season_shrink_and_variance_growth(self):
        for state_spec in m.state_specs().values():
            state = m.OnlineState(state_spec)
            state.m[:] = .5
            state.advance('2023-12-31')
            state.advance('2024-01-01')
            expected = .5*2**(-1/state_spec['half_life'])*state_spec['season_keep']
            np.testing.assert_allclose(state.m, expected, atol=1e-12, rtol=0)
            state.advance('2024-06-01')
            self.assertLess(float(state.m.max()), expected)
            if state_spec['family'] != 'elo':
                self.assertGreater(float(state.v.min()), .08)

    def test_doubleheader_snapshot_and_order_invariance(self):
        first, second = game(gid='dh1'), game(hg=0, ag=7, gid='dh2')
        targets = [dict(first), dict(second)]
        a, _ = m.replay(targets, [first, second])
        b, _ = m.replay(targets, [second, first])
        self.assertEqual(a[0]['dynamic'], a[1]['dynamic'])
        self.assertEqual(a, b)
        tomorrow = game(day='2023-04-02', gid='tomorrow')
        aa, _ = m.replay([tomorrow], [first, second])
        bb, _ = m.replay([tomorrow], [second, first])
        self.assertEqual(aa, bb)
        for state in aa[0]['dynamic'].values():
            self.assertEqual(state['home_games'], 2)
            self.assertEqual(state['away_games'], 2)
            self.assertEqual(state['total_observations'], 2)
        # Duplicate exact IDs must fail, even if scores match.
        with self.assertRaisesRegex(ValueError, 'duplicate_update_fixture'):
            m.replay([tomorrow], [first, first])

    def test_draw_refund_price_order_and_logloss(self):
        r = dict(game(), prices=[2.1, 1.6], order=['away', 'home'])
        self.assertEqual(m.settlement(r, .7), (1, 6000, 1))
        self.assertEqual(m.settlement(r, .3), (0, -10000, 1))
        draw = dict(r, hg=2, ag=2)
        self.assertEqual(m.settlement(draw, .7), (0, 0, 0))
        metric = m.metrics([r, draw], [.7, .1])
        self.assertEqual(metric['net_krw'], 6000)
        self.assertEqual(metric['roi'], .3)
        self.assertEqual(metric['pushes'], 1)
        self.assertAlmostEqual(metric['logloss'], -np.log(.7))

    def test_bad_calibration_years_rejected(self):
        for role, year in [('train', '2025'), ('evaluation', '2026')]:
            with self.assertRaisesRegex(ValueError, 'forbidden_calibration_year'):
                m.fit([dict(game(day=year+'-04-01'), role=role)], {'kind': 'baseline'})


class RealSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cohorts, cls.games, cls.audit = m.load_facts(m.DEFAULT)
        cls.rows, _ = m.replay(cls.cohorts, cls.games)

    def test_exact_cohort_and_fixture_recovery(self):
        self.assertEqual(self.audit['cohort_counts'], {'train': 710, 'validation': 712, 'evaluation': 198})
        self.assertEqual(self.audit['merged_score_games'], 2913)
        self.assertEqual(self.audit['doubleheader_extra_games'], 14)
        self.assertEqual(self.audit['allstar_excluded'], 4)
        self.assertEqual(self.audit['synthetic_year_ids'], 50)
        self.assertEqual(len({r['source_fixture_id'] for r in self.games}), len(self.games))

    def test_shin_independent_production_and_archived_baselines(self):
        prod_spec = importlib.util.spec_from_file_location('read_only_devig', ROOT/'src/devig.py')
        prod = importlib.util.module_from_spec(prod_spec)
        prod_spec.loader.exec_module(prod)
        for r in self.cohorts:
            prices, order = m.prices_order(r)
            np.testing.assert_allclose(m.market(r), prod.shin(prices)[order.index('home')], atol=1e-12, rtol=0)
        for prices in [[1.01, 1.1], [3., 4.], [2., 2.], [1.5, 2.5]]:
            r = dict(prices=prices, order=['home', 'away'])
            np.testing.assert_allclose(m.market(r), prod.shin(prices)[0], atol=1e-12, rtol=0)
        for role, file in [('validation', 'prior-validation_predictions.jsonl'), ('evaluation', 'prior-predictions.jsonl')]:
            archived = m.read(m.DEFAULT/'sources'/file)
            rows = [r for r in self.cohorts if r['role'] == role]
            self.assertEqual([m.key(r) for r in rows], [m.key(r) for r in archived])
            np.testing.assert_allclose([m.market(r) for r in rows], [r['market_probability'] for r in archived], atol=1e-12, rtol=0)
            np.testing.assert_allclose([m.reference(r) for r in rows], [r['baseline_probability'] for r in archived], atol=1e-12, rtol=0)
            prior_result = json.loads((m.DEFAULT/'sources/prior-results.json').read_text(encoding='utf8'))
            expected = prior_result['candidates']['baseline'][role]
            actual = m.metrics(rows, [m.reference(r) for r in rows])
            for field in ['wins', 'net_krw', 'daily_close_max_drawdown_krw', 'pushes', 'settled']:
                self.assertEqual(actual[field], expected[field])
            for field in ['accuracy', 'roi', 'logloss', 'brier']:
                np.testing.assert_allclose(actual[field], expected[field], atol=1e-12, rtol=0)

    def test_all_feature_proofs_strictly_prior_and_nonzero(self):
        nonzero = {s: 0 for s in m.state_specs()}
        for r in self.rows:
            for name, state in r['dynamic'].items():
                if state['latest_observation']:
                    self.assertLess(state['latest_observation'], r['date'])
                nonzero[name] += int(any(abs(x) > 1e-12 for x in state['x']))
        self.assertTrue(all(n > 1000 for n in nonzero.values()))

    def test_actual_future_and_same_day_score_mutation(self):
        cutoff = '2026-06-16'
        changed = [dict(g, hg=g['ag']+30, ag=g['hg']+1) if g['date'] >= cutoff else dict(g) for g in self.games]
        new, _ = m.replay(self.cohorts, changed)
        for before, after in zip(self.rows, new):
            if before['date'] <= cutoff:
                self.assertEqual(before['dynamic'], after['dynamic'])
        later = [(b, a) for b, a in zip(self.rows, new) if b['date'] > cutoff]
        self.assertTrue(any(b['dynamic'] != a['dynamic'] for b, a in later))

    def test_entire_eval_mutation_cannot_change_selection_or_coefficients(self):
        changed = [dict(g, hg=g['ag']+25, ag=g['hg']) if g['date'][:4] >= '2025' else dict(g) for g in self.games]
        altered_cohort = [dict(r, hg=r['ag']+17, ag=r['hg']) if r['role'] == 'evaluation' else dict(r) for r in self.cohorts]
        new, _ = m.replay(altered_cohort, changed)
        original_fit, original_pred = m.freeze_from_rows(self.rows)
        new_fit, new_pred = m.freeze_from_rows(new)
        self.assertEqual(original_fit, new_fit)
        self.assertEqual(original_pred, new_pred)

    def test_committed_frozen_fit_reproduction(self):
        path = m.DEFAULT/'frozen-selection.json'
        if not path.exists():
            self.skipTest('requires freeze artifact; run again after freezing')
        frozen = json.loads(path.read_text(encoding='utf8'))
        fresh, vp = m.freeze_from_rows(self.rows)
        self.assertEqual(fresh['heads'], frozen['heads'])
        for name, c in fresh['candidates'].items():
            stored = frozen['candidates'][name]
            for stage in ['selection_fit', 'final_fit']:
                for field in ['beta', 'scale']:
                    if field in c[stage]:
                        np.testing.assert_allclose(c[stage][field], stored[stage][field], atol=1e-12, rtol=0)
        val = [r for r in self.rows if r['role'] == 'validation']
        recorded = m.read(m.DEFAULT/'validation-ledger.jsonl.gz')
        for name, p in vp.items():
            actual = [r['candidates'][name] for r in recorded]
            np.testing.assert_allclose(p, [r['probability'] for r in actual], atol=1e-12, rtol=0)
            self.assertEqual([m.settlement(r, z) for r, z in zip(val, p)],
                             [(r['hit'], r['profit_krw'], r['settled']) for r in actual])

    def test_evaluation_ledger_exact_coverage_and_new_state_changes(self):
        path = m.DEFAULT/'evaluation-ledger.jsonl.gz'
        if not path.exists():
            self.skipTest('requires evaluation artifact; run again after evaluating')
        records = m.read(path)
        frozen = json.loads((m.DEFAULT/'frozen-selection.json').read_text(encoding='utf8'))
        rows = [r for r in self.rows if r['role'] == 'evaluation']
        self.assertEqual(len(records), 198)
        self.assertEqual([m.key(r) for r in rows], [m.key(r) for r in records])
        for name, candidate in frozen['candidates'].items():
            p = m.predict(candidate['final_fit'], rows)
            actual = [r['candidates'][name] for r in records]
            np.testing.assert_allclose(p, [r['probability'] for r in actual], atol=1e-12, rtol=0)
            self.assertEqual([m.settlement(r, z) for r, z in zip(rows, p)],
                             [(r['hit'], r['profit_krw'], r['settled']) for r in actual])
        for state in m.state_specs():
            names = [n for n, c in frozen['candidates'].items() if c['specification'].get('state') == state]
            self.assertTrue(any(any(r['candidates'][n]['side'] != r['candidates']['baseline']['side'] for r in records) for n in names))
        for r in records:
            self.assertEqual(set(r['candidates']), set(m.candidate_specs()))
            self.assertEqual({v['stake_krw'] for v in r['candidates'].values()}, {10000})

    def test_independent_every_row_price_settlement_and_daily_drawdown(self):
        path = m.DEFAULT/'evaluation-ledger.jsonl.gz'
        if not path.exists():
            self.skipTest('requires evaluation artifact')
        records = m.read(path)
        result = json.loads((m.DEFAULT/'results.json').read_text(encoding='utf8'))
        for name in m.candidate_specs():
            wins = net = peak = balance = drawdown = 0
            by_day = {}
            for row in records:
                p = row['candidates'][name]
                side = 'home' if p['probability'] >= .5 else 'away'
                self.assertEqual(side, p['side'])
                prices, order = m.prices_order(row)
                price = prices[order.index(side)]
                self.assertEqual(p['price'], price)
                draw = row['hg'] == row['ag']
                hit = not draw and ((side == 'home') == (row['hg'] > row['ag']))
                profit = 0 if draw else int((Decimal(str(price))-1)*10000) if hit else -10000
                self.assertEqual((int(hit), profit), (p['hit'], p['profit_krw']))
                wins += hit
                net += profit
                by_day[row['date']] = by_day.get(row['date'], 0)+profit
            for day in sorted(by_day):
                balance += by_day[day]
                peak = max(peak, balance)
                drawdown = max(drawdown, peak-balance)
            expected = result['candidates'][name]['evaluation']
            self.assertEqual((wins, net, drawdown),
                             (expected['wins'], expected['net_krw'], expected['daily_close_max_drawdown_krw']))
            self.assertEqual(net/1980000, expected['roi'])

    def test_frozen_choice_is_validation_only_and_all_heads_agree(self):
        path = m.DEFAULT/'frozen-selection.json'
        if not path.exists():
            self.skipTest('requires frozen artifact')
        frozen = json.loads(path.read_text(encoding='utf8'))
        candidates = frozen['candidates']
        vm = lambda n: candidates[n]['validation']
        market = vm('market_null')
        eligible = [n for n in candidates if vm(n)['wins'] >= market['wins'] and vm(n)['net_krw'] >= market['net_krw']]
        self.assertEqual(frozen['heads']['primary'], min(eligible, key=lambda n: (vm(n)['logloss'], vm(n)['brier'], n)))
        for n in candidates:
            self.assertNotIn('evaluation', candidates[n])
            self.assertEqual(candidates[n]['selection_fit']['training_years'], ['2023'])
            self.assertEqual(candidates[n]['final_fit']['training_years'], ['2023', '2024'])

    def test_actual_2025_state_affects_2026_without_calibration(self):
        changed = [dict(g, hg=25, ag=0) if g['date'][:4] == '2025' else dict(g) for g in self.games]
        rows, _ = m.replay(self.cohorts, changed)
        before = next(r for r in self.rows if r['role'] == 'evaluation')
        after = next(r for r in rows if r['role'] == 'evaluation')
        self.assertTrue(all(before['dynamic'][s]['x'] != after['dynamic'][s]['x'] for s in m.state_specs()))
        for a, b in zip(self.rows, rows):
            if a['role'] != 'evaluation':
                self.assertEqual(a, b)

    def test_all_daily_state_snapshots_reproduce(self):
        path = m.DEFAULT/'daily-state-snapshots.jsonl.gz'
        if not path.exists():
            self.skipTest('requires frozen artifact')
        _, actual = m.replay(self.cohorts, self.games, True)
        expected = m.read(path)
        self.assertEqual(len(actual), len(expected))
        for a, b in zip(actual, expected):
            for name in ['date', 'state', 'counts', 'latest_observation', 'total_observations', 'teams']:
                self.assertEqual(a[name], b[name])
            for name in ['mean', 'variance']:
                np.testing.assert_allclose(a[name], b[name], atol=1e-12, rtol=0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
