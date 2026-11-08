import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from bets import SEL_NAMES
from same_line_research_rules_20261108 import canonical_options, partition

class SameLineRulesTest(unittest.TestCase):
    def row(self, round_no=1, capture='2026-11-01T09:30:00+00:00', kickoff='2026-11-01T10:00:00+00:00'):
        return {'fixture_key': [round_no, 'sc', 'league', 'home', 'away', '11.01'],
                'round': round_no, 'kickoff': kickoff, 'record_captured_at': capture}

    def test_reversed_options_are_mapped_by_official_name(self):
        options = [{'선택': '오버', '배당': 1.5}, {'선택': '언더', '배당': 2.5}]
        ordered = canonical_options('언더오버', options, SEL_NAMES)
        self.assertEqual([x['선택'] for x in ordered], ['언더', '오버'])
        # A coincidentally reversed price vector cannot swap settlement directions.
        self.assertEqual([x['배당'] for x in ordered], [2.5, 1.5])
        self.assertIsNone(canonical_options('언더오버', [options[0], options[0]], SEL_NAMES))
        self.assertIsNone(canonical_options('unknown', options, SEL_NAMES))

    def test_cross_round_physical_duplicates_are_excluded(self):
        a, b = self.row(1), self.row(2)
        c = self.row(3, kickoff='2026-11-01T11:00:00+00:00')
        pre, late, duplicates = partition([a, b, c])
        self.assertEqual((pre, late, duplicates), ([c], [], 2))

    def test_t30_is_inclusive_and_late_and_missing_are_separate(self):
        on_time = self.row()
        late = self.row(2, capture='2026-11-01T09:30:01+00:00', kickoff='2026-11-01T10:00:00+00:00')
        late['fixture_key'][3] = 'other'
        missing = self.row(3, capture=None)
        missing['fixture_key'][3] = 'third'
        pre, excluded, duplicates = partition([on_time, late, missing])
        self.assertEqual(pre, [on_time])
        self.assertEqual(excluded, [late, missing])
        self.assertEqual(duplicates, 0)

    def test_actual_artifact_aggregation(self):
        import json
        artifact = ROOT / 'experiments/same-line-market-evidence-20261108/direction-results.json'
        data = json.loads(artifact.read_text(encoding='utf8'))
        rows = data['candidates']; total = data['total']; changed = data['changed']
        self.assertEqual(total['n'], len(rows))
        self.assertEqual(changed['n'], sum(r['first_pick'] != r['last_pick'] for r in rows))
        self.assertEqual(total['additional_wins'], total['loss_to_win'] - total['win_to_loss'])
        for i, field in enumerate(('first_pick', 'last_pick')):
            wins = sum(r[field] == r['winner'] for r in rows)
            profit = sum(r['last_odds'][r[field]] - 1 if r[field] == r['winner'] else -1 for r in rows)
            self.assertEqual(wins, total[['first_wins', 'last_wins'][i]])
            self.assertAlmostEqual(profit, total['profit_units'][i])
            self.assertAlmostEqual(profit / len(rows), total['roi'][i])
        eligible, excluded, duplicates = partition(rows)
        self.assertEqual((len(eligible), len(excluded), duplicates), (len(rows), 0, 0))

if __name__ == '__main__': unittest.main()
