"""Boundaries and selection rules for the fixed retrospective replay."""
import unittest

from scripts.validate_m0_handicap import choose, half_outcome, home_probability


class HandicapProofTests(unittest.TestCase):
    def test_actual_tied_score_negative_home_line(self):
        self.assertEqual(half_outcome(5, 5, -2.5), 'away')

    def test_tied_score_positive_home_line(self):
        self.assertEqual(half_outcome(5, 5, 2.5), 'home')

    def test_negative_line_below_boundary(self):
        self.assertEqual(half_outcome(7, 5, -2.5), 'away')

    def test_negative_line_above_boundary(self):
        self.assertEqual(half_outcome(8, 5, -2.5), 'home')

    def test_positive_line_boundary(self):
        self.assertEqual(half_outcome(3, 5, 2.5), 'home')
        self.assertEqual(half_outcome(2, 5, 2.5), 'away')

    def test_reject_non_half_line_and_invalid_score(self):
        for line in (0, 2, 0.25, float('nan')):
            with self.assertRaises(ValueError):
                half_outcome(5, 5, line)
        with self.assertRaises(ValueError):
            half_outcome(-1, 5, 2.5)

    def test_probability_symmetry(self):
        self.assertAlmostEqual(home_probability(5, 4, 2.5) + home_probability(4, 5, -2.5), 1.0)

    def test_fixed_selection(self):
        self.assertEqual(choose(.7, 1.4, 2.3), ('home', 'home'))
        self.assertEqual(choose(.3, 1.4, 2.3), ('away', 'home'))

    def test_ties_are_rejected(self):
        with self.assertRaises(ValueError):
            choose(.5, 1.4, 2.3)
        with self.assertRaises(ValueError):
            choose(.7, 1.4, 1.4)


if __name__ == '__main__':
    unittest.main()
