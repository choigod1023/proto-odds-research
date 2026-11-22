import copy
import unittest

from scripts.audit_saved_player_comparison import audit, canonical, describe, digest


def row():
    return {'record_type': 'prediction', 'event_id': 'event1', 'decision_id': 'd1',
            'kickoff': '2026-11-03T09:30:00Z', 'as_of': '2026-11-03T08:51:00Z',
            'features': {'fixture': {'league': 'KBO'}, 'score_inputs': {'home': None, 'away': None},
                         'research_player_inputs': {'status': 'captured', 'source_game_id': 'official1',
                                                   'observed_at': '2026-11-03T08:50:00Z',
                                                   'lineups': {'home': [{'name': 'A'}], 'away': [{'name': 'B'}]},
                                                   'coverage': {'both_lineups_present': True},
                                                   'lineup_status': {'official_today': True}}}}


def sign(records):
    previous = None
    for i, r in enumerate(records, 1):
        r.pop('record_hash', None)
        r.update(ledger_sequence=i, previous_record_hash=previous)
        r['record_hash'] = digest(canonical(r))
        previous = r['record_hash']
    return records


class AuditTests(unittest.TestCase):
    def test_capture_claim_does_not_supply_ids_or_predictions(self):
        result = describe(row())
        self.assertTrue(result['both_lineups_named'])
        self.assertFalse(result['both_lineups_expected_count'])
        self.assertTrue(result['before_t30_in_enclosing_ledger'])
        for k in ('both_lineups_have_ids', 'score_forecast_present', 'score_inputs_both_present', 'candidate_input_present'):
            self.assertFalse(result[k])

    def test_future_source_or_boundary_is_excluded(self):
        r = row()
        r['features']['research_player_inputs']['observed_at'] = '2026-11-03T09:01:00Z'
        self.assertFalse(describe(r)['before_t30_in_enclosing_ledger'])
        r['as_of'] = '2026-11-03T09:00:00Z'
        r['features']['research_player_inputs']['observed_at'] = r['as_of']
        self.assertFalse(describe(r)['before_t30_in_enclosing_ledger'])

    def test_deduplicate_reissued_event_using_official_identity(self):
        a = row(); b = copy.deepcopy(a)
        b.update(event_id='reissued', decision_id='d2', as_of='2026-11-03T08:52:00Z')
        result = audit(sign([a, b]))
        self.assertEqual(result['captured_revisions'], 2)
        self.assertEqual(result['unique_named_lineup_cases_before_t30'], 1)
        self.assertEqual(result['named_lineup_cases'][0]['decision_id'], 'd2')

    def test_tamper_is_fatal(self):
        records = sign([row()])
        records[0]['features']['score_forecast'] = {'home': 0.9}
        with self.assertRaisesRegex(ValueError, 'invalid ledger'):
            audit(records)

    def test_later_clock_inversion_does_not_erase_earlier_eligible_revision(self):
        a = row(); b = copy.deepcopy(a)
        b.update(decision_id='d2', as_of='2026-11-03T08:52:00Z')
        b['features']['research_player_inputs']['observed_at'] = '2026-11-03T08:53:00Z'
        result = audit(sign([a, b]))
        self.assertEqual(result['named_lineup_cases_with_exact_clock_order'], 0)
        self.assertEqual(result['named_lineup_cases_with_any_exact_clock_order_revision'], 1)


if __name__ == '__main__':
    unittest.main()
