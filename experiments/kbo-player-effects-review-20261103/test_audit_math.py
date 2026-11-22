import math
import unittest
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import audit_math as a

class AuditMathTests(unittest.TestCase):
    def test_contract_is_pinned(self):
        c=a.contract();self.assertEqual(c['temporal_protocol']['inner_fit'],'2023');self.assertEqual(c['evaluation']['price_2026_extended_n'],667)
    def test_order_weights_are_fixed_and_position_dependent(self):
        self.assertAlmostEqual(sum(a.WEIGHTS),9);self.assertGreater(a.WEIGHTS[0],a.WEIGHTS[8])
        self.assertTrue(all(x>y for x,y in zip(a.WEIGHTS,a.WEIGHTS[1:])))
    def test_prior_date_and_poison(self):
        x=[{'date':'2026-08-01','fixture_id':'past','score':1},{'date':'2026-08-02','fixture_id':'same','score':999},{'date':'2026-08-03','fixture_id':'future','score':999}]
        self.assertEqual([z['fixture_id'] for z in a.prior_history(x,'2026-08-02')],['past'])
        with self.assertRaises(ValueError):a.assert_prior(x,'2026-08-02','target')
        with self.assertRaises(ValueError):a.assert_prior([{'date':'2026-08-01','fixture_id':'target'}],'2026-08-02','target')
    def test_missing_is_not_zero(self):
        self.assertEqual(a.numeric(None,2,4),(0,1));self.assertEqual(a.numeric(0,2,4),(-.5,0))
        with self.assertRaises(ValueError):a.numeric(float('nan'),2,4)
    def test_roles_and_unknowns(self):
        batters=[str(i) for i in range(9)];h=set(batters[:-1]);p={'8'}
        r=a.exposures(batters,'8',h,p)
        self.assertEqual(r['unknown_hitter_count'],1);self.assertNotIn('8',r['hitters']);self.assertEqual(r['opposing_starter'],{'8':1.0})
        self.assertEqual(a.exposures(None,None,h,p)['lineup_missing'],1)
        self.assertEqual(a.exposures(batters,'unknown',h,p)['unknown_starter'],1)
    def test_substitution_and_swap(self):
        ids=[str(i) for i in range(10)];v=set(ids);h=a.exposures(ids[:9],'0',v,v);z=a.exposures(ids[1:10],'1',v,v)
        d=a.signed_difference(h,z);reverse=a.signed_difference(z,h)
        for role in d:
            for pid,value in d[role].items():self.assertAlmostEqual(value,-reverse[role][pid])
        self.assertNotEqual(h['hitters'],z['hitters']);self.assertNotEqual(h['opposing_starter'],z['opposing_starter'])
        with self.assertRaises(ValueError):a.exposures(['x']*9,'0',v,v)
    def test_poisson_tail_and_symmetry(self):
        p,tail=a.poisson_3way(1.3,2.1);q,_=a.poisson_3way(2.1,1.3)
        self.assertLessEqual(tail,1e-13);self.assertAlmostEqual(sum(p),1);self.assertAlmostEqual(p[0],q[2]);self.assertAlmostEqual(p[1],q[1])
        with self.assertRaises(ValueError):a.poisson_3way(0,2)
    def test_softmax_stable(self):
        p=a.softmax([1000,999,-1000]);self.assertAlmostEqual(sum(p),1);self.assertGreater(p[0],p[1]);self.assertEqual(p[2],0.)
        with self.assertRaises(ValueError):a.softmax([1,2,math.inf])
    def test_selection_tolerance_and_fixed_grid(self):
        self.assertEqual(a.select_penalty({1:1.0,10:1.0+2e-13,100:1.1}),10)
        self.assertEqual(a.select_family({'M0':(1.0,.5),'M1':(1.0+2e-13,.4),'M2':(1.1,.1)}),'M1')
        self.assertEqual(a.select_family({'M0':(1.0,.4),'M1':(1.0,.4+1e-13),'M2':(1.1,.1)}),'M0')
        with self.assertRaises(ValueError):a.select_penalty({1:1,2:1,10:1})
    def test_refund_ties_decimal_and_drawdown(self):
        self.assertEqual(a.settlement([.5,.5],[2,3],'home'),('home',Decimal('1')))
        self.assertEqual(a.settlement([.3,.7],[2,2.5],'draw'),('away',Decimal('0')))
        r=[{'date':'2026-01-01','p':[.6,.4],'prices':[1.8,2.1],'winner':'away'}, {'date':'2026-01-02','p':[.6,.4],'prices':[1.8,2.1],'winner':'draw'}, {'date':'2026-02-01','p':[.6,.4],'prices':[1.8,2.1],'winner':'home'}]
        z=a.summarize(r);self.assertEqual(z['net'],Decimal('-.2'));self.assertEqual(z['roi'],Decimal('-.2')/3);self.assertEqual(z['drawdown'],Decimal('1'))
        self.assertEqual(z['months'],{'2026-01':Decimal('-1'),'2026-02':Decimal('.8')})
        self.assertIsNone(a.summarize([])['roi'])
    def test_draw_and_decisive_diagnostic(self):
        records=[{'probabilities':{'home':.45,'draw':.25,'away':.30},'winner':'draw'},{'probabilities':{'home':.40,'draw':.20,'away':.40},'winner':'home'},{'probabilities':{'home':.20,'draw':.20,'away':.60},'winner':'away'}]
        d=a.draw_diagnostic(records);self.assertEqual((d['n'],d['observed_draws'],d['decisive_n'],d['decisive_hits']),(3,1,2,2))
        self.assertAlmostEqual(d['mean_draw_probability'],.65/3);self.assertAlmostEqual(d['conditional_logloss'],(-math.log(.5)-math.log(.75))/2);self.assertAlmostEqual(d['conditional_brier'],(.5+.125)/2)

if __name__=='__main__':unittest.main()
