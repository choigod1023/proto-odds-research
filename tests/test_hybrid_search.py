import unittest
from datetime import datetime, timedelta, timezone
from scripts.search_tipster_hybrids import candidates, probability, measure, search, validate


class HybridSearchTests(unittest.TestCase):
    def test_count_and_unique(self):
        specs=candidates()
        self.assertEqual(len(specs),230)
        self.assertEqual(len({s['name'] for s in specs}),230)

    def test_empty_cannot_produce_winner(self):
        r=search([])
        self.assertIsNone(r['selected'])
        self.assertIsNone(r['evaluation'])
        self.assertEqual(r['monthly_loss_limit_krw'],50000)

    def test_probabilities_and_endpoints(self):
        r=dict(arms=dict(production=.8,tipster=0),authors=3)
        specs=candidates()
        self.assertEqual(probability(r,specs[0]),.8)
        self.assertEqual(probability(r,specs[1]),0)
        for s in specs:
            self.assertTrue(0<=probability(r,s)<=1)

    def test_known_profit(self):
        r=dict(arms=dict(production=.8,tipster=.1),authors=3,home_won=True,odds=[1.8,2.3])
        self.assertAlmostEqual(measure([r],candidates()[0])['roi_pct'],80)
        self.assertEqual(measure([r],candidates()[1])['roi_pct'],-100)

    def test_duplicate_and_future_rejected(self):
        r=dict(event='e',at='2026-08-29T12:00:00+00:00',snapshot_at='2026-08-29T11:00:00+00:00',
               kickoff='2026-08-29T18:00:00+00:00',home_won=True,authors=3,odds=[1.8,2.3],
               arms=dict(production=.8,tipster=.1))
        validate([r])
        with self.assertRaises(ValueError): validate([r,r])
        r['snapshot_at']='2026-08-29T19:00:00+00:00'
        with self.assertRaises(ValueError): validate([r])

    def test_final_outcomes_do_not_change_selected_candidate(self):
        rows=[]
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        for d in range(30):
            at=start+timedelta(days=d,hours=12)
            for i in range(20):
                rows.append(dict(event=f'{d}-{i}',date=at.date().isoformat(),at=at.isoformat(),
                    snapshot_at=at.isoformat(),kickoff=(at+timedelta(hours=6)).isoformat(),
                    home_won=True,authors=5,odds=[1.8,2.2],arms=dict(production=.8,tipster=.1)))
        first=search(rows)
        for r in rows[-120:]: r['home_won']=False
        second=search(rows)
        self.assertEqual(first['selected'],second['selected'])
        self.assertNotEqual(first['evaluation'],second['evaluation'])
