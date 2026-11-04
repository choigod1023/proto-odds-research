import copy
import unittest
from scripts.recalculate_matched_multisport import cohorts, payout, score_row


def row():
    return dict(fixture_id='official1', league='KBO', kickoff='2026-01-01T18:30:00+09:00',
                odds=[1.5, 2.5], outcome=1, vote_share=[0, 1], models={'M0': [.4, .6]},
                evidence='retrospective', claimed_profit={'vote': 1.5})


class RecalculationTests(unittest.TestCase):
    def test_same_odds_actual_payout_and_no_cap(self):
        result = score_row(row())
        self.assertEqual(result['profits']['M0'], 1.5)
        summary = cohorts([result])['baseball/player_intersection']['methods']
        self.assertEqual(summary['M0']['roi'], 1.5)
        self.assertEqual(summary['market_shin']['roi'], -1)
        monthly = cohorts([result])['baseball/player_intersection/2026-01']['methods']
        self.assertEqual(monthly['M0'], summary['M0'])

    def test_wrong_worker_payout_fails(self):
        r = row(); r['claimed_profit']['vote'] = 1.6
        with self.assertRaisesRegex(ValueError, 'payout differs'):
            score_row(r)

    def test_wrong_selection_cannot_hide_in_identical_losing_payoffs(self):
        r = row(); r.update(odds=[2.2, 3.0, 3.0], outcome=2,
                            vote_share=[1, 0, 0], models={}, claimed_profit={'vote': -1},
                            claimed_selections={'vote': 1})
        with self.assertRaisesRegex(ValueError, 'selection differs'):
            score_row(r)

    def test_ties_refund_and_duplicate_fixture(self):
        r = row(); r['vote_share'] = [.5, .5]; r['claimed_profit'] = {}
        result = score_row(r)
        self.assertIsNone(result['profits']['vote'])
        self.assertEqual(payout(0, None, [1.5, 2.5]), 0)
        with self.assertRaisesRegex(ValueError, 'duplicate physical'):
            cohorts([result, copy.deepcopy(result)])

    def test_no_missing_model_is_filled_from_market(self):
        r = row(); r['models'] = {}
        summary = cohorts([score_row(r)])
        self.assertNotIn('baseball/player_intersection', summary)
        self.assertNotIn('M0', summary['baseball/controls']['methods'])


if __name__ == '__main__':
    unittest.main()
