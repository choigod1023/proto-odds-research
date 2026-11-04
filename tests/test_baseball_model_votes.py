import json
import pathlib
import unittest
from scripts.link_baseball_model_votes import pick, settle, aggregate, dt, vote_fraction, market_vote_probability

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

    def test_saved_results_and_metadata_recovery(self):
        root=pathlib.Path('experiments/matched-baseball-20261104')
        rows=json.loads((root/'rows.json').read_text(encoding='utf8'))
        recovery=json.loads((root/'recovered-official-metadata.json').read_text(encoding='utf8'))
        self.assertEqual(len({r['official_id'] for r in rows}),197)
        self.assertEqual(recovery['summary']['official_id_team_date_time_verified'],239)
        self.assertEqual(recovery['summary']['schedule_bodies_hash_verified'],66)
        for r in rows:
            self.assertEqual(r['selections']['market90_vote10_multiplicative'],pick(market_vote_probability(r['prices'],r['vote_home_fraction'])))
            self.assertEqual(r['profit']['market90_vote10_multiplicative'],settle(r['selections']['market90_vote10_multiplicative'],r['winner'],r['prices']))
            self.assertTrue(all(dt(v['published_at_claim']) < dt(r['kickoff']) for v in r['votes']))

    def test_empty_summary(self):
        self.assertEqual(aggregate([])['vote']['bets'],0)
        self.assertIsNone(aggregate([])['vote']['roi'])

if __name__ == '__main__': unittest.main()
