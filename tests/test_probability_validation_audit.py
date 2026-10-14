import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from probability_validation_audit import audit, probability


def prediction():
    return dict(record_type='prediction', event_id='event1', snapshot_id='snap1',
                as_of='2026-09-01T10:00:00Z', captured_at='2026-09-01T10:01:00Z',
                market_observed_at='2026-09-01T09:59:00Z', kickoff='2026-09-01T12:00:00Z',
                predictions=dict(selection_id='sel1', offer_id='offer1',
                                 probability_detail=dict(market=.6, ai_candidate=.65)),
                model=dict(residual_version='shadow-v1'))


def settlement():
    return dict(record_type='settlement', snapshot_id='snap1',
                settled_at='2026-09-01T14:00:00Z', captured_at='2026-09-01T14:01:00Z',
                source='official-test-fixture', settlement_version='official-v1',
                outcome=dict(result='hit', selection_id='sel1'))


class AuditTests(unittest.TestCase):
    def test_pending_is_not_a_loss_or_zero_roi(self):
        result = audit([prediction()])
        self.assertEqual(result['exact_settled_probability_pairs'], 0)
        self.assertIsNone(result['roi'])
        self.assertFalse(result['promotion_allowed'])

    def test_valid_pair_requires_further_review(self):
        result = audit([settlement(), prediction()])
        self.assertEqual(result['exact_settled_probability_pairs'], 1)
        self.assertEqual(result['distinct_utc_kickoff_days'], 1)
        self.assertEqual(result['status'], 'requires_temporal_split_and_provenance_review')
        self.assertFalse(result['promotion_allowed'])

    def test_latest_revision_chosen_before_label_availability(self):
        newer = prediction()
        newer.update(snapshot_id='snap2', as_of='2026-09-01T10:02:00Z',
                     captured_at='2026-09-01T10:03:00Z')
        for records in ([prediction(), newer, settlement()], [newer, settlement(), prediction()]):
            result = audit(records)
            self.assertEqual(result['selected_pre_t30_events'], 1)
            self.assertEqual(result['exact_settled_probability_pairs'], 0)
            self.assertEqual(result['exclusions']['selected_missing_exact_settlement'], 1)

    def test_invalid_prediction_clocks(self):
        for field, value, reason in [
            ('captured_at', '2026-09-01T11:30:00Z', 'not_before_t30'),
            ('as_of', '2026-09-01T10:00:00', 'missing_or_naive_time'),
            ('market_observed_at', '2026-09-01T10:02:00Z', 'inconsistent_observation_order'),
        ]:
            with self.subTest(field=field):
                row = prediction()
                row[field] = value
                self.assertEqual(audit([row])['exclusions'][reason], 1)

    def test_settlement_guards(self):
        cases = [
            ('source', '', 'unproven_settlement_provenance_or_time'),
            ('settlement_version', 'unverified', 'unproven_settlement_provenance_or_time'),
            ('settled_at', '2026-09-01T11:00:00Z', 'unproven_settlement_provenance_or_time'),
            ('captured_at', '2026-09-01T13:00:00Z', 'unproven_settlement_provenance_or_time'),
            ('outcome', dict(result='void', selection_id='sel1'), 'void_or_nonbinary_result'),
            ('outcome', dict(result='hit', selection_id='wrong'), 'settlement_selection_mismatch'),
        ]
        for field, value, reason in cases:
            with self.subTest(field=field, value=value):
                row = settlement()
                row[field] = value
                self.assertEqual(audit([prediction(), row])['exclusions'][reason], 1)

    def test_conflicting_corrections_not_cherry_picked(self):
        corrected = copy.deepcopy(settlement())
        corrected['outcome']['result'] = 'miss'
        self.assertEqual(audit([prediction(), settlement(), corrected])['exclusions']
                         ['conflicting_settlements'], 1)

    def test_probability_bounds(self):
        for value in [None, True, 0, 1, float('nan'), float('inf'), '0.6']:
            self.assertFalse(probability(value))
        self.assertTrue(probability(.6))


if __name__ == '__main__':
    unittest.main()
