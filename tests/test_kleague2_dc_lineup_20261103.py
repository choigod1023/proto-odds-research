import copy,datetime,importlib.util,sys,unittest,json
from pathlib import Path
import numpy as np
from scipy.optimize import check_grad
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
if __name__=='__main__':unittest.main()
