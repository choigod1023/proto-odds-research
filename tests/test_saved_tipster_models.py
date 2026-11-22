import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from scripts.compare_saved_tipster_models import metrics, paired, paper_summary


class SavedTipsterModelsTests(unittest.TestCase):
    def test_unsettled_is_not_break_even(self):
        events = [dict(type='bet', account='a', ticket='t', stake=10000,
                       legs=[dict(probability=.5, odds=2)])]
        result = paper_summary(events)['a']
        self.assertIsNone(result['roi_pct'])
        self.assertIsNone(result['settled_net_krw'])
        self.assertEqual(result['pending_stake_krw'], 10000)

    def test_settlement_accounts_separate(self):
        events = [dict(type='bet', account=a, ticket=a, stake=10000, legs=[])
                  for a in ('a', 'b')]
        events.append(dict(type='settlement', account='a', ticket='a', net=-10000))
        result = paper_summary(events)
        self.assertEqual(result['a']['roi_pct'], -100)
        self.assertIsNone(result['b']['roi_pct'])

    def test_metrics_known_returns_and_empty(self):
        rows = [dict(arms={'a': .7}, home_won=True, odds=[1.8, 2]),
                dict(arms={'a': .3}, home_won=True, odds=[1.8, 2])]
        result = metrics(rows, 'a')
        self.assertEqual(result['hit_rate_pct'], 50)
        self.assertAlmostEqual(result['roi_pct'], -10)
        self.assertIsNone(metrics([], 'a')['roi_pct'])

    def test_postgame_snapshot_never_enters_comparison(self):
        replay = SimpleNamespace(stamp=lambda s: datetime.fromisoformat(s), ET=timezone.utc)
        ledger = [dict(event_id='e', captured_at='2026-08-29T20:00:00+00:00',
                       kickoff='2026-08-29T19:00:00+00:00',
                       as_of='2026-08-29T18:00:00+00:00')]
        games = [dict(event_id='e', league='MLB', home='LA다저스', away='뉴욕메츠')]
        self.assertEqual(paired(ledger, games, [], replay), ([], {}, 0))


if __name__ == '__main__':
    unittest.main()
