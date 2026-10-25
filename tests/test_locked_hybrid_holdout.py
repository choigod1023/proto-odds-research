import copy
import unittest
from scripts.locked_hybrid_holdout import evaluate,freeze,metrics,scores


def row(event='e',date='2026-09-01'):
    return dict(event=event,event_id=event,date=date,at=date+'T12:00:00+00:00',
        snapshot_at=date+'T11:50:00+00:00',kickoff=date+'T18:00:00+00:00',authors=4,
        home_won=True,odds=[1.8,2.1],arms=dict(production=.8,tipster=.2))


class LockedHybridTests(unittest.TestCase):
    def setUp(self):
        self.lock=freeze(dict(paired_rows=[row()]),b'dev','2026-10-25T09:09:04+00:00')

    def test_fixed_weights(self):
        self.assertAlmostEqual(scores(row())['hybrid75_25'],.65)
        self.assertAlmostEqual(metrics([row()],'hybrid75_25')['roi_pct'],80)

    def test_development_cannot_reenter(self):
        r=row();r['date']='2026-12-01'
        result=evaluate(dict(paired_rows=[r]),self.lock)
        self.assertEqual(result['excluded'],dict(development_event=1))
        self.assertIsNone(result['groups']['prospective']['hybrid75_25']['roi_pct'])

    def test_retrospective_and_prospective_separate(self):
        result=evaluate(dict(paired_rows=[row('r','2026-09-02'),row('p','2026-10-26')]),self.lock)
        self.assertEqual(result['groups']['prospective']['production']['n'],1)
        self.assertEqual(result['groups']['retrospective_unseen']['production']['n'],1)

    def test_duplicate_excluded_not_first_selected(self):
        r=row('p','2026-10-26')
        result=evaluate(dict(paired_rows=[r,copy.deepcopy(r)]),self.lock)
        self.assertEqual(result['excluded']['duplicate_event'],2)

    def test_policy_tampering_rejected(self):
        self.lock['tipster_weight']=.3
        with self.assertRaises(ValueError):evaluate(dict(paired_rows=[]),self.lock)

    def test_postgame_snapshot_excluded(self):
        r=row('p','2026-10-26');r['snapshot_at']='2026-10-26T20:00:00+00:00'
        self.assertEqual(evaluate(dict(paired_rows=[r]),self.lock)['excluded']['invalid_or_nonpregame_record'],1)
