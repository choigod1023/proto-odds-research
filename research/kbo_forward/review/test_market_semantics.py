"""Contract arithmetic only; does not test a real adapter or establish source rules."""
from decimal import Decimal
import unittest

def settle(outcome, selection, odds, draw_refund):
    if outcome == 'draw' and draw_refund:
        return Decimal('0')
    if outcome == selection:
        return Decimal(odds) - Decimal('1')
    return Decimal('-1')

class ContractExamples(unittest.TestCase):
    def test_draw_differs_between_three_way_and_draw_void(self):
        self.assertEqual(settle('draw', 'home', '1.80', False), Decimal('-1'))
        self.assertEqual(settle('draw', 'home', '1.80', True), Decimal('0'))

    def test_home_win_and_away_win(self):
        self.assertEqual(settle('home', 'home', '1.80', True), Decimal('.80'))
        self.assertEqual(settle('away', 'home', '1.80', True), Decimal('-1'))

if __name__ == '__main__':
    unittest.main()
