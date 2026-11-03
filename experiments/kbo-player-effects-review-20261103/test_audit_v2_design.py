import copy
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import audit_v2_design as v2

class CorrectionDesignTests(unittest.TestCase):
    def setUp(self):
        self.spec={'teams':['H','A'],'hitters':['known'+str(i) for i in range(18)],'starters':['hp','ap'],'mean':[0.]*8,'scale':[1.]*8}
        values={s+'_'+k:0. for s in ('home','away') for k in ('team_strength','rest_days','lineup_strength','starter_strength','bullpen_load')};values['park_factor']=0.
        self.row={'home_id':'H','away_id':'A','values':values,'roster':{'home':{'hitters':['known'+str(i) for i in range(9)],'starter':'hp'},'away':{'hitters':['known'+str(i) for i in range(9,18)],'starter':'ap'}}}
    def test_pinned_correction(self):self.assertTrue(v2.correction_contract()['no_new_candidate_family'])
    def test_both_missing_versus_both_unseen(self):
        x=copy.deepcopy(self.row);y=copy.deepcopy(self.row)
        for side in ('home','away'):
            x['roster'][side]={'hitters':None,'starter':None}
            y['roster'][side]={'hitters':[side+str(i) for i in range(9)],'starter':side+'unknown'}
        a,old_a,_=v2.design(x,self.spec);b,old_b,_=v2.design(y,self.spec)
        np.testing.assert_array_equal(old_a,old_b);self.assertGreater(np.max(np.abs(a-b)),0)
    def test_both_numeric_null_versus_mean_and_actual_zero(self):
        x=copy.deepcopy(self.row);y=copy.deepcopy(self.row)
        x['values']['home_lineup_strength']=x['values']['away_lineup_strength']=None
        a,old_a,_=v2.design(x,self.spec);b,old_b,_=v2.design(y,self.spec)
        np.testing.assert_array_equal(old_a,old_b);self.assertGreater(np.max(np.abs(a-b)),0)
    def test_single_and_paired_unknowns_and_swap(self):
        x=copy.deepcopy(self.row);x['roster']['home']['hitters'][0]='unseen';x['roster']['away']['starter']=None
        before,_,sums=v2.design(x,self.spec);nbase=1+len(self.spec['teams'])+16+4
        self.assertEqual(sums[9],1/9);self.assertEqual(sums[10],1)
        swapped=copy.deepcopy(x);swapped['home_id'],swapped['away_id']=x['away_id'],x['home_id'];swapped['roster']['home'],swapped['roster']['away']=x['roster']['away'],x['roster']['home']
        for k in ('team_strength','rest_days','lineup_strength','starter_strength','bullpen_load'):
            swapped['values']['home_'+k],swapped['values']['away_'+k]=x['values']['away_'+k],x['values']['home_'+k]
        after,_,new_sums=v2.design(swapped,self.spec)
        np.testing.assert_allclose(sums,new_sums,rtol=0,atol=0);np.testing.assert_allclose(before[1:nbase],-after[1:nbase],rtol=0,atol=0)
        np.testing.assert_allclose(before[nbase+12:],-after[nbase+12:],rtol=0,atol=0)
        self.assertNotEqual(before[0],-after[0])

if __name__=='__main__':unittest.main()
