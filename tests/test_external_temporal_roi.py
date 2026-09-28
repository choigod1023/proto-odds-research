import unittest
from datetime import date, timedelta
import numpy as np
from scripts.evaluate_external_temporal_roi import features, payout, evaluate, qualifying, POLICIES


def row(i, day, y=0):
    return {'match_id':str(i),'day':day,'date':str(day),'home_team':'A','away_team':'B',
            'y':y,'goals':(2,0) if y==0 else (0,2),'prices':[1.6,4.,5.]}


class ExternalTemporalTests(unittest.TestCase):
    def test_rounding_combined_not_individual(self):
        self.assertEqual(payout(1.22,1.22),1.5)
        self.assertEqual(payout(1.49,1.5),2.3)
        self.assertEqual(payout(1.5,1.5),2.3)

    def test_future_and_same_day_results_do_not_change_features(self):
        d = date(2024,1,1)
        original = [row(1,d),row(2,d),row(3,d+timedelta(days=3))]
        changed = [row(1,d,2),row(2,d,2),row(3,d+timedelta(days=3),2)]
        np.testing.assert_equal([r['x'] for r in features(original)], [r['x'] for r in features(changed)])

    def test_historical_results_enter_after_embargo(self):
        d = date(2024,1,1)
        a = features([row(1,d),row(2,d+timedelta(days=8))])
        b = features([row(1,d,2),row(2,d+timedelta(days=8))])
        self.assertNotEqual(a[1]['x'][1],b[1]['x'][1])

    def test_one_pair_no_overlap_and_cash_day(self):
        d = date(2024,1,1)
        rows = [row(i,d) for i in range(4)]+[row(9,d+timedelta(days=1))]
        result = evaluate(rows,[[.7,.2,.1]]*5,POLICIES[0])
        self.assertEqual(result['tickets'],1)
        self.assertEqual(result['wins'],1)
        self.assertEqual(result['budget_days'],2)
        self.assertAlmostEqual(result['roi_rounded'],1.6)
        self.assertAlmostEqual(result['return_on_daily_budget'],.8)

    def test_non_favorite_can_be_selected(self):
        r = row(1,date(2024,1,1))
        self.assertEqual(qualifying(r,[.4,.3,.3],POLICIES[3])[3],2)

    def test_missing_prices_do_not_enter_features(self):
        r = row(1,date(2024,1,1)); r['prices']=None
        self.assertEqual(features([r]),[])

    def test_labels_do_not_change_qualification(self):
        d = date(2024,1,1)
        self.assertEqual(qualifying(row(1,d,0),[.7,.2,.1],POLICIES[0]),
                         qualifying(row(1,d,2),[.7,.2,.1],POLICIES[0]))

    def test_no_pair_is_not_zero_roi(self):
        result = evaluate([row(1,date(2024,1,1))],[[.7,.2,.1]],POLICIES[0])
        self.assertIsNone(result['roi_rounded'])
        self.assertEqual(result['profit_units'],0)


if __name__=='__main__':
    unittest.main()
