import unittest, hashlib
from datetime import datetime, timezone

def eligible(received, decision):
    return received <= decision

def train_mean(rows, cutoff):
    values = [label for received, label in rows if received < cutoff]
    return sum(values) / len(values)

class AuditGuards(unittest.TestCase):
    def test_late_receipt(self):
        self.assertFalse(eligible(31, 30))
        self.assertTrue(eligible(30, 30))
    def test_duplicate_actual_bytes(self):
        docs = [b'official sample', b'official sample', b'other']
        digests = [hashlib.sha256(x).hexdigest() for x in docs]
        self.assertEqual(len(digests) - len(set(digests)), 1)
    def test_future_label_poison(self):
        a = [(1, 1), (2, 0), (11, 0)]
        b = [(1, 1), (2, 0), (11, 100000)]
        self.assertEqual(train_mean(a, 10), train_mean(b, 10))
        self.assertEqual(train_mean(a + [(10, 100000)], 10), train_mean(a, 10))

if __name__ == '__main__': unittest.main()
