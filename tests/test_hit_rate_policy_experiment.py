import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import hit_rate_policy_experiment as experiment


def fixture():
    train = {'league': 'K1', 'event_id': 'train', 'kickoff': '2024-12-20T15:00:00+09:00',
        'feature_as_of': '2024-12-18T00:00:00+09:00', 'process_latest_allowed_date': '2024-12-18',
        'odds': [1.7, 3.5, 4.5], 'target': 0}
    test = {**train, 'event_id': 'test', 'kickoff': '2025-01-10T15:00:00+09:00'}
    data = {'rows': [train, test], 'test_rows': [test],
        'probabilities': {name: [[.6, .2, .2]] for name in ('market', 'process', 'market_calibration')}}
    report = {'training_mode': 'frozen_through2024', 'train_games': 1}
    return data, report


class HitRatePolicyTests(unittest.TestCase):
    def test_test_labels_cannot_change_payload_or_ranking(self):
        data, report = fixture()
        original = experiment.payload(data)
        data['test_rows'][0]['target'] = 2
        self.assertEqual(original, experiment.payload(data))
        self.assertTrue(all('target' not in p and 'hit' not in p for p in original[0]))
        self.assertEqual(experiment.select(original[0], True), experiment.select(experiment.payload(data)[0], True))

    def test_future_features_rejected(self):
        data, report = fixture()
        data['test_rows'][0]['feature_as_of'] = '2025-01-10T15:00:00+09:00'
        with self.assertRaisesRegex(ValueError, 'feature after cutoff'):
            experiment.validate(data, report)

    def test_future_process_history_rejected(self):
        data, report = fixture()
        data['test_rows'][0]['process_latest_allowed_date'] = '2025-01-09'
        with self.assertRaisesRegex(ValueError, 'process history'):
            experiment.validate(data, report)

    def test_frozen_cache_required(self):
        data, report = fixture()
        report['training_mode'] = 'past365days_daily_refit'
        with self.assertRaisesRegex(ValueError, 'frozen'):
            experiment.validate(data, report)

    def test_duplicate_and_misaligned_probability_rejected(self):
        data, report = fixture()
        data['test_rows'].append(copy.deepcopy(data['test_rows'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            experiment.validate(data, report)
        data, report = fixture()
        data['probabilities']['process'] = []
        with self.assertRaisesRegex(ValueError, 'alignment'):
            experiment.validate(data, report)

    def test_valid_unknown_odds_timing_is_not_claimed_verified(self):
        data, report = fixture()
        self.assertEqual(len(experiment.validate(data, report)), 1)
        data['test_rows'][0]['odds_as_of'] = '2025-01-10T15:00:00+09:00'
        with self.assertRaisesRegex(ValueError, 'odds after cutoff'):
            experiment.validate(data, report)

    def test_equal_budget_kst_group_and_deterministic_ties(self):
        data, _ = fixture()
        a = experiment.payload(data)[0][0]
        a.update(selection_id='a', market_prob=.7, process_prob=.3, agreement=False)
        b = {**a, 'selection_id': 'b', 'event_key': 'b', 'market_prob': .6, 'process_prob': .6, 'agreement': True}
        self.assertEqual(experiment.select([a,b])[0]['selection_id'], 'a')
        self.assertEqual(experiment.select([a,b], True)[0]['selection_id'], 'b')
        self.assertEqual(experiment.select([b,a], True), experiment.select([a,b], True))
        self.assertEqual(experiment.bucket({**a, 'kickoff_at': '2025-01-09T16:00:00+00:00'}), ('K1', '2025-01-10'))

    def test_paired_interval_requires_equal_counts(self):
        data, _ = fixture()
        a = {**experiment.payload(data)[0][0], 'hit': 1}
        b = {**a, 'hit': 0}
        self.assertEqual(experiment.interval([a], [b], 100)['hit_rate_delta'], -1)
        with self.assertRaisesRegex(ValueError, 'unmatched'):
            experiment.interval([a], [])

    def test_actual_frontend_eligibility_and_daily_priority(self):
        data, _ = fixture()
        primary = experiment.payload(data)[0]
        low = [{**p, 'event_key': 'low', 'selection_id': 'low|' + str(p['choice']),
                'odds': [1.3, 5, 7][p['choice']], 'market_prob': [.75,.15,.10][p['choice']],
                'predicted_hit_prob': [.75,.15,.10][p['choice']]} for p in primary]
        result = subprocess.run(['node', str(experiment.ROOT/'scripts/hit_rate_policy_bridge.mjs')],
            input=json.dumps([primary, low]), text=True, encoding='utf-8', capture_output=True, check=True)
        policy = json.loads(result.stdout)
        self.assertEqual([p['selection_id'] for p in policy['eligible']], ['test|0'])
        self.assertEqual([p['selection_id'] for p in policy['production']], ['test|0'])


if __name__ == '__main__':
    unittest.main()
