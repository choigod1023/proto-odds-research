import unittest
from scripts.link_baseball_model_votes import pick, settle, aggregate, dt

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
    def test_empty_summary(self):
        self.assertEqual(aggregate([])['vote']['bets'],0)
        self.assertIsNone(aggregate([])['vote']['roi'])

if __name__ == '__main__': unittest.main()
