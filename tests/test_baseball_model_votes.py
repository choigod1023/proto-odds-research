import json
import pathlib
import unittest
from scripts.link_baseball_model_votes import pick, settle, aggregate, dt, vote_fraction, market_vote_probability, classify_vote_excerpt

class BaseballComparisonTests(unittest.TestCase):
    def test_ties_and_refunds(self):
        self.assertIsNone(pick(.5))
        self.assertEqual(settle('home', 'draw', {'home':1.8,'away':2}), 0)
        self.assertAlmostEqual(settle('away', 'away', {'home':1.8,'away':2.2}), 1.2)
        self.assertEqual(settle('away', 'home', {'home':1.8,'away':2}), -1)
        self.assertIsNone(settle(None,'home',{'home':1.8,'away':2}))
    def test_timezone_and_missing(self):
        self.assertLess(dt('2026-03-29T02:05:10+09:00'),dt('2026-03-29T14:00:00+09:00'))
        self.assertIsNone(dt('2026-03-29'))
    def test_vote_ties_neutral_and_no_fallback(self):
        self.assertIsNone(vote_fraction([]))
        self.assertIsNone(vote_fraction(['home', 'away']))
        self.assertIsNone(vote_fraction(['home', 'draw']))
        self.assertIsNone(market_vote_probability({'home':1.5,'away':3}, None))
        self.assertEqual(vote_fraction(['away','away','home']), 1/3)

    def test_fixed_market_control_uses_normalized_prices(self):
        prices={'home':1.5,'away':3.0}
        self.assertAlmostEqual(market_vote_probability(prices, 0), .6)
        self.assertEqual(pick(market_vote_probability(prices, 0)), 'home')
        self.assertIsNone(pick(market_vote_probability({'home':2,'away':2}, .5)))

    def test_market_excerpt_f5_counterexample_and_mixed_products(self):
        self.assertEqual(classify_vote_excerpt('NC vs KT | NC F5 승 | 오버', 'NC 다이노스'), 'other_or_mixed_market')
        self.assertEqual(classify_vote_excerpt('승패 (ML) | NC 다이노스 승 | 62%', 'NC 다이노스'), 'explicit_team_win_no_other_market_markers')
        for excerpt in ['NC first 5 승', 'NC 5회 승', 'NC +1.5 승', 'NC 승 | O', 'NC 승 | UNDER 8.5']:
            self.assertEqual(classify_vote_excerpt(excerpt, 'NC 다이노스'), 'other_or_mixed_market')
        self.assertEqual(classify_vote_excerpt('NC 다이노스 근소 우세', 'NC 다이노스'), 'no_explicit_selected_team_win')

    def test_saved_results_and_metadata_recovery(self):
        root=pathlib.Path('experiments/matched-baseball-20261104')
        rows=json.loads((root/'rows.json').read_text(encoding='utf8'))
        recovery=json.loads((root/'recovered-official-metadata.json').read_text(encoding='utf8'))
        audit=json.loads((root/'vote-excerpt-audit.json').read_text(encoding='utf8'))
        self.assertEqual(len(audit),226)
        self.assertTrue(any(x['official_id']=='20260613NCKT0' and 'F5' in x['excerpt'] and x['classification']=='other_or_mixed_market' for x in audit))
        self.assertEqual(len({r['official_id'] for r in rows}),103)
        self.assertEqual(recovery['summary']['official_id_team_date_time_verified'],239)
        self.assertEqual(recovery['summary']['schedule_bodies_hash_verified'],66)
        f5=[v for r in rows if r['official_id']=='20260613NCKT0' for v in r['votes']]
        self.assertEqual([v['author'] for v in f5], ['sportsanalyst1'])
        for r in rows:
            self.assertEqual(r['selections']['market90_vote10_multiplicative'],pick(market_vote_probability(r['prices'],r['vote_home_fraction'])))
            self.assertEqual(r['profit']['market90_vote10_multiplicative'],settle(r['selections']['market90_vote10_multiplicative'],r['winner'],r['prices']))
            self.assertTrue(all(dt(v['published_at_claim']) < dt(r['kickoff']) for v in r['votes']))
            self.assertTrue(all(classify_vote_excerpt(v['excerpt'],v['selected_team']) == 'explicit_team_win_no_other_market_markers' for v in r['votes']))

    def test_empty_summary(self):
        self.assertEqual(aggregate([])['vote']['bets'],0)
        self.assertIsNone(aggregate([])['vote']['roi'])

if __name__ == '__main__': unittest.main()
