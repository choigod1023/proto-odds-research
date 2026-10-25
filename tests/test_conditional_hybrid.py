import copy
import unittest
from scripts.simulate_conditional_hybrid import weight, predict, validate, metrics


def row():
    return dict(event='one', date='2026-09-03', snapshot_at='2026-09-03T10:00:00+00:00',
        at='2026-09-03T12:00:00+00:00', kickoff='2026-09-03T14:00:00+00:00',
        settled_at='2026-09-03T18:00:00+00:00', authors=5,
        arms=dict(production=.6, tipster=.2), home_won=True, odds=[1.6, 2.2])


class ConditionalTest(unittest.TestCase):
    def test_signals(self):
        r = row()
        self.assertEqual(weight(r, 'disagreement50', []), .5)
        self.assertEqual(weight(r, 'consensus50', []), .5)
        r['authors'] = 3
        self.assertEqual(weight(r, 'consensus50', []), .25)

    def test_current_result_does_not_change_selection(self):
        for policy in ('model', 'tipster', 'fixed25', 'disagreement50', 'consensus50', 'past_brier'):
            r = row(); a = predict([r], policy)[0]['probability']
            r['home_won'] = False
            self.assertEqual(a, predict([r], policy)[0]['probability'])

    def test_future_and_pending_results_excluded(self):
        r = row(); history=[]
        for i in range(12):
            x=copy.deepcopy(r); x['date']='2026-09-02'; x['event']=str(i)
            history.append(x)
        self.assertEqual(weight(r, 'past_brier', history), .25)
        for x in history:
            x['settled_at']='2026-09-02T18:00:00+00:00'
        self.assertEqual(weight(r, 'past_brier', history), 0.)

    def test_validation(self):
        r=row(); validate([r])
        with self.assertRaises(ValueError): validate([r,r])
        r['snapshot_at']='2026-09-03T15:00:00+00:00'
        with self.assertRaises(ValueError): validate([r])

    def test_empty_and_fixed(self):
        self.assertIsNone(metrics([])['roi_pct'])
        self.assertAlmostEqual(predict([row()], 'fixed25')[0]['probability'], .5)
