import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

P = Path(__file__).resolve().parents[1] / 'experiments/league-research-20261031/kleague2/audit.py'
spec = importlib.util.spec_from_file_location('k2_audit', P)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

class TestK2Audit(unittest.TestCase):
    def test_real_sources(self):
        x = a.audit()
        self.assertEqual((x['official_results'], x['unique_official_ids'], x['prior_identity_links'], x['score_agreements'], x['prior_unmatched']), (275, 275, 23, 23, 0))
        self.assertEqual(x['review_targets_matching_identity'], 0)
    def test_time_boundaries_and_late_receipt(self):
        k = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        d = k - timedelta(minutes=30)
        self.assertTrue(a.eligible(d, d, k))
        self.assertFalse(a.eligible(d + timedelta(seconds=1), d, k))
        self.assertFalse(a.eligible(d, d + timedelta(seconds=1), k))
    def test_duplicate_document_and_distinct_event(self):
        rows = [{'event_id':'x','sha256':'a'}, {'event_id':'x','sha256':'a'}, {'event_id':'y','sha256':'a'}]
        self.assertEqual(len(a.unique_docs(rows)), 2)
    def test_future_and_current_label_poison(self):
        cut = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [{'available_at':cut-timedelta(seconds=1),'label':1}, {'available_at':cut,'label':0}, {'available_at':cut+timedelta(seconds=1),'label':0}]
        before = a.training_labels(rows, cut)
        rows[1]['label'] = 999
        rows[2]['label'] = 999
        self.assertEqual(a.training_labels(rows, cut), before)
        self.assertEqual(len(before), 1)

if __name__ == '__main__':
    unittest.main()
