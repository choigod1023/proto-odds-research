"""Checks for the independent frozen-artifact reconstruction."""
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import audit_frozen as audit

class FrozenDesignTests(unittest.TestCase):
    def test_equal_unknown_on_both_sides_cancels_m2_flags(self):
        spec={'teams':['H','A'],'hitters':[],'starters':[],'mean':[0.]*8,'scale':[1.]*8}
        values={s+'_'+k:0. for s in ('home','away') for k in ('team_strength','rest_days','lineup_strength','starter_strength','bullpen_load')};values['park_factor']=None
        row={'home_id':'H','away_id':'A','values':values,'roster':{'home':{'hitters':['h'+str(i) for i in range(9)],'starter':None},'away':{'hitters':['a'+str(i) for i in range(9)],'starter':None}}}
        x,flags=audit.design_one(row,spec,'M2');nbase=1+len(spec['teams'])+16+4
        self.assertEqual(flags[0],flags[1]);self.assertEqual(flags[0][1],1.0)
        np.testing.assert_array_equal(x[0,nbase-4:nbase],np.zeros(4))
        y,m1flags=audit.design_one(row,spec,'M1');self.assertEqual(m1flags,flags)
        self.assertEqual(y[0,2+2*len(spec['teams'])+16+1],1.0)

if __name__=='__main__':unittest.main()
