import copy,datetime,importlib.util,sys,unittest,json,math,hashlib,subprocess,collections
from decimal import Decimal
from pathlib import Path
import numpy as np
from scipy.optimize import check_grad
from scipy.stats import poisson
R=Path(__file__).resolve().parents[1]/'experiments/kleague2-dc-lineup-20261103';s=importlib.util.spec_from_file_location('dc_k2',R/'dc_model.py');d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
class TestK2DC(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.games,_=d.base.load_games();cls.features=d.base.history_features(cls.games)
 def test_joint_gradient_positive_and_negative_rho(self):
  games=self.games[:40]
  for candidate in (False,True):
   fn,teams,size=d.objective(games,self.features,10,candidate,datetime.date(2026,1,1))
   for raw in (-.25,.25):
    theta=np.linspace(-.1,.15,size+1);theta[0]=.1;theta[-1]=raw
    err=check_grad(lambda x:fn(x)[0],lambda x:fn(x)[1],theta)
    self.assertLess(err,0.00015,(candidate,raw,err))
 def test_rho_zero_equals_original_poisson(self):
  g=self.games[:15];teams={s:i for i,s in enumerate(sorted({t for x in g for t in x['teams']}))}
  for candidate in (False,True):
   X,_=d.base.design(g,self.features,teams,candidate);coeff=np.linspace(-.1,.1,X.shape[1]);fit={'teams':teams,'theta':np.r_[coeff,0.],'candidate':candidate,'rho':0.}
   _,p,_,tau=d.predict(fit,g,self.features);_,ref,_=d.base.predict({'teams':teams,'coeff':coeff,'candidate':candidate},g,self.features)
   np.testing.assert_allclose(p,ref,rtol=0,atol=1e-14);self.assertEqual(tau,1.)
 def test_safe_tau_unknown_teams_and_feature_domain(self):
  g=copy.deepcopy(self.games[:2]);g[0]['teams'][0]='UNSEEN';features=copy.deepcopy(self.features);features[g[0]['id']]=[[1.,3.,1.,1.],[0.,0.,0.,0.]]
  teams={s:i for i,s in enumerate(sorted({t for x in g[1:] for t in x['teams']}))};X,_=d.base.design(g,features,teams,True)
  for sign in (-1,1):
   theta=np.r_[np.linspace(-.15,.2,X.shape[1]),sign*3.];rho,_,_=d.rho_transform(theta,len(teams),True);rates,p,unknown,tau=d.predict({'teams':teams,'theta':theta,'candidate':True,'rho':rho},g,features)
   self.assertGreater(tau,0);self.assertTrue(unknown[0][0]);np.testing.assert_allclose(p.sum(axis=1),1,atol=3e-12)
  features[g[0]['id']][0][1]=3.01
  with self.assertRaisesRegex(ValueError,'domain'):d.validate_features(g,features)
 def test_future_label_poison_does_not_change_features(self):
  a=d.base.history_features(self.games);b=copy.deepcopy(self.games)
  for g in b:
   if g['date']>=datetime.date(2026,7,1):g['score']=[999,999]
  self.assertEqual(a,d.base.history_features(b))
 def test_external_cohort_and_features_rejected(self):
  _,_,test=d.base.partition(self.games);original=[json.loads(l) for l in (d.PRIOR/'frozen/test-predictions.jsonl').read_bytes().splitlines()]
  d.require_cohort(test,original,self.features)
  with self.assertRaisesRegex(ValueError,'external'):d.require_cohort(test[:-1],original,self.features)
  changed=copy.deepcopy(self.features);changed[test[0]['id']][0][0]=.5
  with self.assertRaisesRegex(ValueError,'feature'):d.require_cohort(test,original,changed)
 def test_probability_matches_independent_score_grid(self):
  games=self.games[:5];teams={s:i for i,s in enumerate(sorted({t for g in games for t in g['teams']}))};X,_=d.base.design(games,self.features,teams,True)
  for sign in (-1,1):
   theta=np.r_[np.linspace(-.1,.12,X.shape[1]),sign*.5];rho,_,_=d.rho_transform(theta,len(teams),True);rates,p,_,_=d.predict({'teams':teams,'theta':theta,'candidate':True,'rho':rho},games,self.features)
   for i,(h,a) in enumerate(rates):
    axis=np.arange(35);joint=np.outer(poisson.pmf(axis,h),poisson.pmf(axis,a))
    for j,k in ((0,0),(0,1),(1,0),(1,1)):joint[j,k]*=d.dixon_coles_tau(j,k,h,a,rho)
    expected=[np.tril(joint,-1).sum(),np.trace(joint),np.triu(joint,1).sum()]
    np.testing.assert_allclose(p[i],expected,atol=3e-12,rtol=0);self.assertAlmostEqual(float(joint.sum()),1.,places=12)
 def test_frozen_git_bytes_and_poisoned_labels(self):
  _,_,test=d.base.partition(self.games);frozen=(R/'frozen/test-predictions.jsonl').read_bytes();rows=[json.loads(l) for l in frozen.splitlines()];params=json.loads((R/'frozen/parameters.json').read_bytes())
  self.assertEqual(hashlib.sha256(frozen).hexdigest(),'b3e4783a3af9c16fdd04dfe6d37b9a565f765091c4b18b7805cf6ba62e13bf13')
  self.assertEqual(subprocess.check_output(['git','show','HEAD:'+str((R/'frozen/test-predictions.jsonl').relative_to(d.REPO)).replace('\\','/')],cwd=d.REPO),frozen)
  poisoned=copy.deepcopy(test)
  for game in poisoned:game['score']=[999,999]
  for mode in ('team_only','lineup'):
   x=params[mode];fit={'teams':x['teams'],'theta':np.array(x['theta']),'candidate':x['candidate'],'rho':x['rho']};_,p,_,_=d.predict(fit,poisoned,self.features)
   for i,row in enumerate(rows):np.testing.assert_allclose(p[i],row['models'][mode]['p'],rtol=0,atol=1e-12)
  self.assertTrue(all('score' not in row and 'outcome' not in row for row in rows))
 def test_original_poisson_artifacts_unchanged(self):
  for path,sha in [('frozen/test-predictions.jsonl','96006c7e54b335d31bc83a4c7bebc2ed07b31427ff196a567a9d41a8257a8872'),('results/metrics.json','982913ed8bb2181807afea3beff9e36104b73aa3880acf94173adee8278695cb'),('results/scored.jsonl','74f7a3db105dcf30d0aa9737fe0013bf23490ee23e041eaf85780f3b8dcbffe2')]:
   self.assertEqual(hashlib.sha256((d.PRIOR/path).read_bytes()).hexdigest(),sha)
 def test_independent_decimal_brier_and_drawdown(self):
  rows=[json.loads(l) for l in (R/'results/scored.jsonl').read_bytes().splitlines()];metrics=json.loads((R/'results/metrics.json').read_bytes());priced=[r for r in rows if r['odds'] is not None];self.assertEqual((len(rows),len(priced)),(128,127))
  for mode in ('poisson_team','poisson_lineup','dc_team','dc_lineup','market'):
   profit=Decimal(0);daily=collections.defaultdict(Decimal);hits=0;loss=0.;brier=0.
   for row in priced:
    p=row['p'][mode];y=row['outcome'];pick=max(range(3),key=lambda i:p[i]);hits+=pick==y;loss-=math.log(p[y]);brier+=sum((p[i]-(i==y))**2 for i in range(3));gain=Decimal(str(row['odds'][pick]))-1 if pick==y else Decimal(-1);profit+=gain;daily[row['date']]+=gain
   self.assertEqual(metrics['price_metrics'][mode]['hits'],hits);self.assertAlmostEqual(metrics['price_metrics'][mode]['logloss'],loss/127,places=12);self.assertAlmostEqual(metrics['price_metrics'][mode]['brier'],brier/127,places=12);self.assertAlmostEqual(metrics['price_returns'][mode]['profit_units'],float(profit),places=10)
   balance=peak=worst=Decimal(0)
   for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);worst=max(worst,peak-balance)
   self.assertAlmostEqual(metrics['price_returns'][mode]['daily_closing_max_drawdown_units'],float(worst),places=10)
if __name__=='__main__':unittest.main()
