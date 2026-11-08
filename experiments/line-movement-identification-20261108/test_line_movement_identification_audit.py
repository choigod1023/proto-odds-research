import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('audit', Path(__file__).with_name('audit.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

class BoundsTest(unittest.TestCase):
    def test_nested_events(self):
        self.assertEqual(audit.bounds(8.5, 9.5, .5, 'under'), [0, .5])
        self.assertEqual(audit.bounds(8.5, 9.5, .5, 'over'), [.5, 1])
        self.assertEqual(audit.bounds(9.5, 8.5, .3, 'under'), [.3, 1])
        self.assertEqual(audit.bounds(9.5, 8.5, .3, 'over'), [0, .3])
        self.assertEqual(audit.bounds(8.5, 8.5, .3, 'under'), [.3, .3])

    def test_two_distributions_same_new_different_old(self):
        # Both have P(X < 9.5)=.5; X=9 is the intervening atom.
        a = {8: .5, 10: .5}
        b = {9: .5, 10: .5}
        prob = lambda d, line: sum(p for x, p in d.items() if x < line)
        self.assertEqual(prob(a, 9.5), prob(b, 9.5))
        self.assertEqual((prob(a, 8.5), prob(b, 8.5)), (.5, 0))

    def test_reject_unsupported(self):
        for args in [(8, 9.5, .5, 'under'), (8.5, 9.5, 1.1, 'under'), (8.5, 9.5, .5, 'draw')]:
            with self.assertRaises(ValueError): audit.bounds(*args)

if __name__ == '__main__': unittest.main()
