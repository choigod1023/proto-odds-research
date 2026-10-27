import unittest
from scripts.audit_broad_history import unit_return,external_summary,fixed_diagnostics


class BroadHistoryTests(unittest.TestCase):
    def test_returns(self):
        for label,net in [('won',1),('lost',-1),('draw',0),('won 1/2',.5),('lost 1/2',-.5)]:
            self.assertEqual(unit_return(2,label),net)
        with self.assertRaises(ValueError):unit_return(2,'pending')
        with self.assertRaises(ValueError):unit_return(float('nan'),'won')

    def test_flat_stake_not_publisher_profit(self):
        r=dict(date='2025-01-01',sport='Baseball',league='MLB',match='A vs B',tip='A',tipster='x',odds='2',outcome='won',profit='100')
        summary=external_summary([r,r])
        self.assertEqual(summary['overall']['flat_unit_roi_pct'],100)
        self.assertEqual(summary['overall']['n'],1)
        self.assertEqual(summary['excluded']['duplicate_identity'],1)

    def test_empty_diagnostic(self):
        self.assertIsNone(fixed_diagnostics([])['roi_pct'])

    def test_fixed_hybrid(self):
        rows=[dict(date='2025-01-01',arms=dict(production=.8,tipster=.1),home_won=True,odds=[2,2])]
        self.assertEqual(fixed_diagnostics(rows)['roi_pct'],100)
