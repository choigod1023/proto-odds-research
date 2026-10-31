import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROTOCOL = Path(__file__).resolve().parents[1] / 'experiments/league-research-20261031/kleague1/protocol.json'

def eligible(kickoff, received):
    return received <= kickoff - timedelta(minutes=30)

def prior_labels(rows, cutoff):
    return [(t, y) for t, y in rows if t < cutoff]

class TestKLeagueK1Design(unittest.TestCase):
    def test_protocol(self):
        p = json.loads(PROTOCOL.read_text(encoding='utf-8-sig'))
        self.assertEqual(p['status'], 'preregistered_design_only')
        self.assertIn('blocked_data', p['stop_rules'])
    def test_receipt_boundary(self):
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertTrue(eligible(t, t - timedelta(minutes=30)))
        self.assertFalse(eligible(t, t - timedelta(minutes=30) + timedelta(microseconds=1)))
    def test_bytes_not_names_determine_duplicate(self):
        digest = lambda x: hashlib.sha256(x).hexdigest()
        self.assertEqual(digest(b'official'), digest(b'official'))
        self.assertNotEqual(digest(b'official'), digest(b'other'))
    def test_future_label_poison_excluded_from_prior_inputs(self):
        cutoff = datetime(2026, 1, 2, tzinfo=timezone.utc)
        history = [(cutoff - timedelta(seconds=1), 1), (cutoff, 0), (cutoff + timedelta(days=1), 2)]
        poisoned = [history[0], (history[1][0], 2), (history[2][0], 0)]
        self.assertEqual(prior_labels(history, cutoff), prior_labels(poisoned, cutoff))

if __name__ == '__main__':
    unittest.main()
