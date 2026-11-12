import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from all_sport_dynamic import state_features,top_two,holm,block_inference,candidates
from sport_hybrid_validation import paper


class DynamicTests(unittest.TestCase):
    def test_paper_places_positive_edge_tickets_and_caps_loss(self):
        days=pd.date_range('2025-01-01',periods=28).repeat(2)
        d=pd.DataFrame(dict(date=days,odds=[[2.,2.]]*56,y=[1]*56,
                            league=['A']*56,home_team=['H']*56,away_team=['V']*56))
        r=paper(d,np.tile([.7,.3],(56,1)),3)
        self.assertEqual(r['tickets'],5)
        self.assertEqual(r['net'],-50000)
        self.assertEqual(r['worst_month'],-50000)

    def fixture(self):
        return pd.DataFrame(dict(date=pd.to_datetime(['2024-01-01','2024-01-01','2024-01-05','2024-02-05']),
            league=['A']*4,sport=['bs']*4,home_team=['H']*4,away_team=['V']*4,y=[0,1,0,1],market=[[.6,.4]]*4))

    def test_same_day_and_future_results_excluded(self):
        d=self.fixture();before=state_features(d,.002)
        d.loc[2:,'y']=1-d.loc[2:,'y'];after=state_features(d,.002)
        np.testing.assert_allclose(before.iloc[:3],after.iloc[:3])
        np.testing.assert_allclose(before.iloc[0],before.iloc[1])

    def test_same_day_order_invariance(self):
        d=self.fixture()
        np.testing.assert_allclose(state_features(d,.002),state_features(d.iloc[[1,0,2,3]],.002).reindex(d.index))

    def test_uncertainty_and_league_isolation(self):
        d=self.fixture();d.loc[3,'league']='new'
        f=state_features(d,.002)
        self.assertEqual(f.loc[3,'state'],0)
        self.assertAlmostEqual(f.loc[3,'variance'],.2)
        self.assertTrue((f.variance>0).all())

    def test_rank_count_ties_and_labels(self):
        d=self.fixture();p=np.array(d.market.tolist())
        before=top_two(d,p);d.y=1
        np.testing.assert_equal(before,top_two(d,p))
        self.assertEqual(len(before),4)

    def test_holm_and_zero_effect(self):
        np.testing.assert_allclose(holm([.01,.04,.03]),[.03,.06,.06])
        effect,ci,p=block_inference(np.zeros((10,2)),np.ones((10,2)),repeats=100)
        np.testing.assert_equal(effect,0);np.testing.assert_equal(ci,0);np.testing.assert_equal(p,1)

    def test_forecasts_do_not_need_test_labels(self):
        d=pd.concat([self.fixture()]*60,ignore_index=True)
        for name in ('slow','fast'):
            d[name+'_state']=np.tile([0,.1,-.1,.05],60)
            d[name+'_variance']=.2
        first=candidates(d,d,[]);second=candidates(d,d.drop(columns='y'),[])
        for n in first:
            np.testing.assert_allclose(first[n],second[n]);np.testing.assert_allclose(first[n].sum(1),1)
