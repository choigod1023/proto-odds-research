import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sport_hybrid_validation import brier, choose_blend, fit, predict, paper


class HybridTests(unittest.TestCase):
    def frame(self,k=2):
        return pd.DataFrame([dict(date=day,league='test',home_team=f'H{i}',away_team=f'A{i}',
                                 y=1,market=[1/k]*k,odds=[1.8]*k,elo_diff=i)
                             for day in pd.date_range('2025-01-01',periods=50) for i in range(2)])

    def test_three_way_draw_is_retained(self):
        d=self.frame(3); d['y']=np.arange(len(d))%3
        p=predict(fit(d,['elo_diff']),d)
        self.assertEqual(p.shape,(100,3))
        np.testing.assert_allclose(p.sum(1),1)
        self.assertTrue((p[:,1]>0).all())

    def test_test_outcomes_cannot_affect_predictions(self):
        d=self.frame(); d['y']=np.arange(len(d))%2
        model=fit(d,['elo_diff'],True)
        p=predict(model,d)
        changed=d.copy(); changed['y']=1-changed.y
        np.testing.assert_allclose(p,predict(model,changed))

    def test_blend_tie_prefers_market(self):
        p=np.array([[.6,.4],[.4,.6]])
        self.assertEqual(choose_blend(p,p,[0,1]),1)

    def test_pure_component_can_be_selected(self):
        q=np.array([[.1,.9],[.9,.1]])
        p=1-q
        self.assertEqual(choose_blend(q,p,[0,1]),0)
        self.assertEqual(brier(np.eye(3),[0,1,2]).sum(),0)

    def test_monthly_full_loss_reserved(self):
        d=self.frame(); p=np.tile([.8,.2],(len(d),1))
        r=paper(d,p,3)
        self.assertEqual(r['worst_month'],-50000)
        self.assertTrue(all(v>=-50000 for v in r['monthly_net'].values()))

    def test_no_edge_no_bet(self):
        d=self.frame(); p=np.tile([.5,.5],(len(d),1))
        self.assertEqual(paper(d,p,2)['tickets'],0)


if __name__=='__main__': unittest.main()
