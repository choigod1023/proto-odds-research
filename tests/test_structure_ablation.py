import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from structure_ablation import LEGACY, NAMES, train_model, forecast, select, split, concentration
from sport_hybrid_validation import paper


class StructureTests(unittest.TestCase):
    def data(self):
        rng=np.random.default_rng(42)
        d=pd.DataFrame({c:rng.normal(size=90) for c in LEGACY})
        d['market']=[[.45,.25,.30]]*90
        d['y']=np.arange(90)%3
        d['league']='A'
        return d

    def test_forecasts_ignore_test_labels_and_normalize(self):
        d=self.data()
        for name in NAMES:
            with self.subTest(name=name):
                model=train_model(name,d)
                p=forecast(name,model,d)
                without=d.drop(columns='y')
                np.testing.assert_allclose(p,forecast(name,model,without))
                np.testing.assert_allclose(p.sum(1),1)
                self.assertTrue(np.isfinite(p).all() and (p>=0).all())

    def test_expert_falls_back_for_unseen_league(self):
        d=self.data()
        model=train_model('league_experts',d)
        d.league='new'
        np.testing.assert_allclose(forecast('league_experts',model,d),forecast('legacy',model[0],d))

    def test_selection_can_choose_market_only(self):
        market=np.array([[.99,.005,.005]]*5)
        bad=np.array([[.01,.49,.5]]*5)
        choices,winner=select({'bad':bad},market,np.zeros(5,int))
        self.assertEqual(choices[winner]['market_weight'],1.)

    def test_temporal_partitions_and_overlap_rejection(self):
        d=pd.DataFrame({'season':[2019,2020,2021], 'date':pd.to_datetime(['2019-01-01','2020-01-01','2021-01-01'])})
        a,b,c=split(d,2021)
        self.assertLess(a.date.max(),b.date.min())
        self.assertLess(b.date.max(),c.date.min())
        d.loc[0,'date']=pd.Timestamp('2022-01-01')
        with self.assertRaises(ValueError): split(d,2021)

    def test_paper_audit_matches_and_exposes_jackpot(self):
        rows=[]
        for day in range(1,9):
            for team in ('A','B'):
                rows.append(dict(date=pd.Timestamp(2024,1,day),league='L',home_team=team,
                                 away_team='C',y=0 if day==1 else 1,odds=[10.,2.,3.]))
        d=pd.DataFrame(rows); p=np.tile([.5,.25,.25],(len(d),1))
        audit=concentration(d,p,3); original=paper(d,p,3)
        self.assertEqual(audit['net'],original['net'])
        self.assertEqual(audit['tickets'],original['tickets'])
        self.assertGreater(audit['net'],0)
        self.assertLess(audit['net_without_best_ticket'],0)
