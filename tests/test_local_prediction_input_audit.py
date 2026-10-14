import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.audit_local_prediction_inputs import audit


class LocalInputAuditTests(unittest.TestCase):
    def run_audit(self, rows):
        raw = ''.join(json.dumps(row) + '\n' for row in rows).encode()
        with TemporaryDirectory() as root:
            path = Path(root) / 'ledger.jsonl'
            path.write_bytes(raw)
            result = audit(path)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
        return result

    def row(self, **changes):
        result = dict(record_type='prediction', event_id='e1', snapshot_id='r1',
                      kickoff='2026-01-01T12:00:00Z',
                      captured_at='2026-01-01T11:30:00Z',
                      market_observed_at='2026-01-01T11:29:00Z')
        result.update(changes)
        return result

    def test_revisions_not_independent_and_boundary_allowed(self):
        out = self.run_audit([self.row(), self.row(snapshot_id='r2')])
        self.assertEqual(out['eligible_t30_rows'], 2)
        self.assertEqual(out['eligible_t30_events'], 1)
        self.assertFalse(out['roi_evaluable'])

    def test_old_observation_cannot_hide_late_capture(self):
        out = self.run_audit([self.row(captured_at='2026-01-01T12:01:00Z')])
        self.assertEqual(out['eligible_t30_rows'], 0)
        self.assertEqual(out['exclusions'], {'not_captured_by_t30': 1})

    def test_naive_missing_and_future_observation_fail_closed(self):
        out = self.run_audit([
            self.row(captured_at='2026-01-01T11:00:00'),
            self.row(captured_at=None),
            self.row(market_observed_at='2026-01-01T11:31:00Z')])
        self.assertEqual(out['eligible_t30_rows'], 0)
        self.assertEqual(out['exclusions']['invalid_or_missing_time'], 2)
        self.assertEqual(out['exclusions']['observation_after_capture'], 1)

    def test_result_field_is_not_validated_settlement(self):
        out = self.run_audit([self.row(result='win')])
        self.assertEqual(out['rows_with_result_fields'], 1)
        self.assertFalse(out['roi_evaluable'])

    def test_empty_file_no_roi(self):
        out = self.run_audit([])
        self.assertEqual(out['rows'], 0)
        self.assertIsNone(out['observed_min'])


if __name__ == '__main__':
    unittest.main()
