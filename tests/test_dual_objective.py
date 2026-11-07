import sys
from pathlib import Path
import itertools
import unittest
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dual_objective import select_pairs,compatible,fit_value,value_predict,simulate,inference


class DualTests(unittest.TestCase):
    def fixture(self):
        rows=[]
        for event in range(4):
            for choice in range(2):
                p=.8-event*.04 if choice==0 else .2+event*.04
                rows.append(dict(event=event,choice=choice,date=pd.Timestamp('2025-01-01'),sport='bs',
                    league='A',home='H'+str(event//2),away='V'+str(event),price=1.8+choice,
                    p=p,q=p,v=.1-event*.03+choice*.01,ev=p*(1.8+choice)-1,hit=choice==0))
        return pd.DataFrame(rows)

    def test_global_pair_matches_brute_force(self):
        d=self.fixture()
        for policy in ('market','probability','value','hybrid'):
            score=lambda x: np.log(x.q) if policy=='market' else np.log(x.p) if policy=='probability' else np.log1p(x.v) if policy=='value' else .5*(np.log(x.p)+np.log1p(x.v))
            pairs=[(a,b) for a,b in itertools.combinations(d.itertuples(),2) if compatible(a,b)]
            best=max(score(a)+score(b) for a,b in pairs)
            result=select_pairs(d,policy)[0]
            chosen=[d[(d.event==e)&(d.choice==c)].iloc[0] for e,c in zip(result['events'],result['choices'])]
            self.assertAlmostEqual(sum(score(x) for x in chosen),best)
            self.assertNotEqual(chosen[0].home,chosen[1].home)

    def test_selection_independent_of_labels(self):
        d=self.fixture()
        for policy in ('market','probability','value','hybrid'):
            a=select_pairs(d,policy)[0];d.hit=~d.hit;b=select_pairs(d,policy)[0]
            self.assertEqual(a['events'],b['events']);self.assertEqual(a['choices'],b['choices'])

    def test_abstain_does_not_force_pairs(self):
        d=self.fixture();d.ev=-.1
        self.assertEqual(select_pairs(d,'hybrid',True),[])
        self.assertEqual(len(select_pairs(d,'hybrid',False)),1)

    def test_value_is_finite_and_bounded(self):
        p=np.tile([.6,.4],(40,1));odds=np.tile([1.5,2.2],(40,1));y=np.arange(40)%2
        beta=fit_value(p,p,odds,y);v=value_predict(beta,p,p,odds)
        self.assertTrue(np.isfinite(v).all());self.assertTrue((v>=-.95).all());self.assertTrue((v<=odds-1).all())

    def test_month_loss_cap_and_week_limit(self):
        tickets=[dict(date=str(d.date()),return_=-1,hit=0,leg_hits=0,odds=3) for d in pd.date_range('2025-01-01','2025-02-28')]
        r=simulate(tickets,3,['2025-01','2025-02'])
        self.assertEqual(r['tickets'],10);self.assertEqual(r['net'],-100000);self.assertEqual(r['worst_month'],-50000)
        r=simulate(tickets[:7],2,['2025-01']);self.assertLessEqual(r['tickets'],4)
        self.assertIsNone(simulate([],2,['2025-01'])['roi'])

    def test_identical_policies_have_zero_difference(self):
        tickets=[dict(date='2025-01-01',return_=-1,hit=0,leg_hits=0,odds=3)]
        tests,metrics=inference({p:tickets for p in ('market','probability','value','hybrid')})
        self.assertTrue(all(t['delta']==0 and t['p']==1 for t in tests))
        self.assertTrue(all(m['tickets']==1 for m in metrics.values()))
