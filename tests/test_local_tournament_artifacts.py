"""Independent standard-library audit of committed experiment evidence."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'docs/research/2026-10-01-local-tournament-results.json'


def text_hashes(path):
    # Evidence records execution bytes on Windows. Git may convert only EOLs.
    lf = path.read_bytes().replace(b'\r\n', b'\n')
    return {hashlib.sha256(raw).hexdigest() for raw in (lf, lf.replace(b'\n', b'\r\n'))}


class TournamentArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = json.loads(REPORT.read_text(encoding='utf-8'))

    def test_evidence_matches_source_code(self):
        result = self.result
        script = ROOT / 'scripts/local_model_tournament.py'
        self.assertIn(result['code_sha256'], text_hashes(script))
        for name, digest in result['dependency_sha256'].items():
            self.assertIn(digest, text_hashes(ROOT / name))
        self.assertEqual(len(result['sources']), 12)
        self.assertEqual(sum(s['matches'] for s in result['sources']), 4116)

    def test_frozen_grid_and_inner_selection(self):
        for fold in self.result['folds']:
            trials = fold['inner_trials']
            self.assertEqual(len(trials), 240)
            self.assertEqual(len({t['config']['id'] for t in trials}), 240)
            self.assertEqual(len(fold['outer_trials']), 240)
            eligible = [t for t in trials if t['summary']['tickets'] >= 20]
            selected = sorted(eligible, key=lambda t: (
                -t['summary']['budget_return'],
                fold['inner_models'][t['config']['model']]['logloss'],
                t['config']['id']))[0]['config']
            self.assertEqual(fold['winners']['policy'], selected)
            model = min(fold['inner_models'], key=lambda m: (
                fold['inner_models'][m]['logloss'], m))
            self.assertEqual(fold['winners']['probability_model'], model)
            split = fold['split']
            self.assertLess(split['train']['last'], split['inner']['first'])
            self.assertLess(split['inner']['last'], split['outer']['first'])

    def test_decimal_ledgers_and_identical_cash_budgets(self):
        totals = {}
        for fold in self.result['folds']:
            base_keys = [(r['date'], r['league']) for r in fold['records']['baseline']]
            self.assertEqual(len(base_keys), len(set(base_keys)))
            for name, records in fold['records'].items():
                self.assertEqual([(r['date'], r['league']) for r in records], base_keys)
                profit, stakes, wins = Decimal(0), 0, 0
                for row in records:
                    pair = row['pair']
                    self.assertEqual(row['budget'], 1)
                    self.assertIn(len(pair), (0, 2))
                    self.assertEqual(row['stake'], int(bool(pair)))
                    self.assertIn(row['wins'], (0, 1))
                    self.assertLessEqual(row['wins'], row['stake'])
                    self.assertEqual(len({a['id'] for a in pair}), len(pair))
                    gross = Decimal(1)
                    for leg in pair:
                        self.assertIn(leg['choice'], (0, 1, 2))
                        self.assertTrue(leg['id'].startswith(row['league'] + ':' + row['date'] + ':'))
                        gross *= Decimal(str(leg['odds']))
                    earned = row['wins'] * gross - row['stake']
                    self.assertAlmostEqual(float(earned), row['profit'], places=10)
                    profit += earned
                    stakes += row['stake']
                    wins += row['wins']
                reported = fold['policy'][name]
                self.assertEqual(reported['tickets'], stakes)
                self.assertEqual(reported['wins'], wins)
                self.assertAlmostEqual(reported['profit'], float(profit), places=10)
                prior = totals.get(name, (Decimal(0), 0, 0, 0))
                totals[name] = (prior[0]+profit, prior[1]+stakes, prior[2]+wins, prior[3]+len(records))
        for name, (profit, stakes, wins, budgets) in totals.items():
            reported = self.result['pooled']['policy'][name]
            self.assertEqual((reported['tickets'], reported['wins'], reported['budgets']), (stakes, wins, budgets))
            self.assertAlmostEqual(reported['profit'], float(profit), places=10)
            self.assertAlmostEqual(reported['roi'], float(profit/stakes), places=10)
            self.assertAlmostEqual(reported['budget_return'], float(profit/budgets), places=10)


if __name__ == '__main__':
    unittest.main()
