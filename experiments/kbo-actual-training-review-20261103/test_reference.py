import copy,math,sys,unittest
from decimal import Decimal
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import reference as r

class IndependentReferenceTests(unittest.TestCase):
    def setUp(self):
        self.e={'fixture_id':'g','team_id':'H','batting_basis':'official_confirmed_lineup','starter_basis':'official_start_flag','batting':[{'order':i+1,'player_id':str(i+101),'fixture_id':'g','team_id':'H','identity_verified':True} for i in range(9)],'starter':{'player_id':'303','fixture_id':'g','team_id':'H','identity_verified':True}}
        values={s+'_'+k:0. for s in ('home','away') for k in ('team_strength','rest_days','lineup_strength','starter_strength','bullpen_load')};values['park_factor']=0.
        self.row={'home_id':'H','away_id':'A','values':values,'roster':{'home':{'hitters':[str(i) for i in range(9)],'starter':'h'},'away':{'hitters':[str(i+10) for i in range(9)],'starter':'a'}}}
        self.spec={'teams':['A','H'],'hitters':[],'starters':[],'mean':[0.]*8,'scale':[1.]*8}
    def test_contract_and_fixed_families(self):self.assertEqual(set(r.contract()['model_families']),{'M0','M2'})
    def test_accepted_official_starters(self):
        self.assertEqual(r.roster_eligibility(self.e),(True,None));x=copy.deepcopy(self.e);x['starter_basis']='official_verified_first_pitcher';self.assertTrue(r.roster_eligibility(x)[0])
    def test_reject_assumptions(self):
        for field,value in [('batting_basis','first_nine_postgame'),('batting_basis','batting_order_only'),('starter_basis','schedule_probable'),('starter_basis','winning_pitcher')]:
            x=copy.deepcopy(self.e);x[field]=value;self.assertFalse(r.roster_eligibility(x)[0])
    def test_reject_ambiguous_identity_and_wrong_game(self):
        for mutate in ('duplicate','unknown','game','team','false_profile'):
            x=copy.deepcopy(self.e)
            if mutate=='duplicate':x['batting'][2]['player_id']=x['batting'][0]['player_id']
            if mutate=='unknown':x['starter']['player_id']='0'
            if mutate=='game':x['starter']['fixture_id']='different'
            if mutate=='team':x['batting'][0]['team_id']='A'
            if mutate=='false_profile':x['batting'][0]['identity_verified']=False
            self.assertFalse(r.roster_eligibility(x)[0])
    def test_same_date_future_and_target_are_rejected(self):
        for item in ({'fixture_id':'g','date':'2025-01-01'},{'fixture_id':'x','date':'2025-05-01'},{'fixture_id':'x','date':'2025-05-02'}):
            with self.assertRaises(ValueError):r.assert_prior([item],'g','2025-05-01')
        r.assert_prior([{'fixture_id':'x','date':'2025-04-30'}],'g','2025-05-01')
    def test_missing_does_not_equal_zero_and_bf_not_used(self):
        self.assertEqual(r.numeric(None,2.,4.),(0.,1.));self.assertEqual(r.numeric(0,2.,4.),(-.5,0.))
        self.assertIsNone(r.scalars('home_lineup_strength',[{'history':[{'ab':0,'hit':0,'bb':0}]}]))
        self.assertEqual(r.scalars('home_starter_strength',{'appearances':[{'er':1,'outs':9,'source_bf_raw':99999}]}),3.)
    def test_paired_missing_versus_unseen(self):
        a=copy.deepcopy(self.row);b=copy.deepcopy(self.row)
        for s in ('home','away'):
            a['roster'][s]={'hitters':None,'starter':None};b['roster'][s]={'hitters':[s+str(i) for i in range(9)],'starter':s+'unknown'}
        self.assertGreater(np.max(abs(r.design(a,self.spec,'M2')-r.design(b,self.spec,'M2'))),0.)
    def test_paired_numeric_null_versus_observed_mean(self):
        a=copy.deepcopy(self.row);b=copy.deepcopy(self.row);a['values']['home_lineup_strength']=a['values']['away_lineup_strength']=None
        self.assertGreater(np.max(abs(r.design(a,self.spec,'M2')-r.design(b,self.spec,'M2'))),0.)
    def test_weights_roles_unknowns_and_train_only(self):
        self.assertAlmostEqual(sum(r.WEIGHTS),9);self.assertGreater(r.WEIGHTS[0],r.WEIGHTS[-1]);spec=r.train_spec([self.row]);self.assertNotIn('new',spec['hitters']);self.assertIn('h',spec['starters']);self.assertNotIn('h',spec['hitters'])
    def test_probability_and_conditional_draw(self):
        p=r.poisson(1.3,2.1);q=r.poisson(2.1,1.3);self.assertAlmostEqual(sum(p),1);self.assertAlmostEqual(p[0],q[2]);self.assertAlmostEqual(p[1],q[1]);self.assertAlmostEqual(sum(r.softmax([1000,999,-1000])),1)
    def test_refund_and_decimal_daily_drawdown(self):
        self.assertEqual(r.settle([.5,.5],[2,3],'draw'),('home',Decimal(0)))
        rows=[{'date':'2026-04-01','p':[.6,.4],'prices':[1.8,2.1],'winner':'away'},{'date':'2026-04-02','p':[.6,.4],'prices':[1.8,2.1],'winner':'draw'},{'date':'2026-05-01','p':[.6,.4],'prices':[1.8,2.1],'winner':'home'}];z=r.ledger_metrics(rows)
        self.assertEqual(z['net'],Decimal('-.2'));self.assertEqual(z['roi'],Decimal('-.2')/3);self.assertEqual(z['drawdown'],Decimal(1));self.assertIsNone(r.ledger_metrics([])['roi'])
if __name__=='__main__':unittest.main()
