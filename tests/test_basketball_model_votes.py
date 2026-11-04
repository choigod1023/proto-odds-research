import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('basketball',Path(__file__).resolve().parents[1]/'scripts/link_basketball_model_votes.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class BasketballComparison(unittest.TestCase):
    def test_market_and_scope_are_not_interchangeable(self):
        x={'market':'spread','period':'full_game_including_overtime','side':'home','fixture_id':'x','result_evidence':'official_exact_fixture','home_score':90,'away_score':80,'proto_odds':[1.5,2.6],'proto_offer_evidence':'archived_exact_fixture','author':'A'}
        self.assertIn('different_or_unparsed_market',m.classify(x))
        x['market']='moneyline'; x['period']=None
        self.assertIn('overtime_scope_unverified',m.classify(x))
    def test_win_loss_refund(self):
        self.assertEqual(m.settle('home',100,90,1.7),0.7)
        self.assertEqual(m.settle('away',100,90,2.0),-1)
        self.assertEqual(m.settle('home',100,100,1.7),0)
    def test_timestamp_requires_timezone(self):
        self.assertIsNone(m.timestamp('2025-01-01T12:00:00'))
        self.assertIsNotNone(m.timestamp('2025-01-01T12:00:00Z'))

if __name__=='__main__': unittest.main()
