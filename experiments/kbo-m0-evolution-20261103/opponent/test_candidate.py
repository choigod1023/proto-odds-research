import copy,sys,unittest
from datetime import date
from pathlib import Path
import numpy as np
from scipy.sparse import csr_matrix
sys.path.insert(0,str(Path(__file__).resolve().parent))
import candidate as c

class CandidateTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.data=c.core.load();cls.train,cls.val=c.core.development(cls.data);cls.rows=cls.train[:120]
 def test_weights_use_only_training_dates_and_mean_one(self):
  rows=[{'event_date':'2024-01-01'},{'event_date':'2024-12-31'}];w,last=c.training_weights(rows,365)
  self.assertEqual(last,'2024-12-31');self.assertAlmostEqual(w.sum(),2);self.assertAlmostEqual(w[1]/w[0],2)
  ones,last=c.training_weights(rows,None);np.testing.assert_array_equal(ones,[1,1])
 def test_exact_products_and_missing_flags(self):
  rows=[c.core.normalize(self.rows[0])];spec=c.core.legacy.make_spec(rows);spec['hitters']=[];spec['starters']=[]
  spec['mean']=[[0.]*8,[0.]*8];spec['scale']=[[1.]*8,[1.]*8]
  r=rows[0];r['features']['home_lineup_strength']['value']=2.;r['features']['away_starter_strength']['value']=3.;r['features']['away_bullpen_load']['value']=4.;r['features']['home_rest_days']['value']=5.
  actual=c.features(rows,spec);np.testing.assert_allclose(actual[0],[6.,12.,20.])
  before,nb=c.core.legacy.design(rows,spec,'M0');r['features']['away_starter_strength']['value']=None
  actual=c.features(rows,spec);np.testing.assert_allclose(actual[0],[0.,0.,20.]);after,_=c.design(rows,spec,c.CONFIGS['interaction10']);expected,_=c.core.legacy.design(rows,spec,'M0');np.testing.assert_allclose(after[:,:nb].toarray(),expected.toarray());self.assertFalse(np.array_equal(before.toarray(),expected.toarray()))
 def test_gradient_matches_finite_differences(self):
  rng=np.random.default_rng(41);X=csr_matrix(rng.normal(size=(16,7)));y=rng.integers(0,7,16).astype(float);w=rng.uniform(.2,1.5,16);pen=np.array([0.,10.,10.,10.,100.,100.,100.]);b=rng.normal(size=7)*.1;_,grad=c.objective(b,X,y,w,pen);eps=1e-6;numeric=[]
  for i in range(len(b)):
   e=np.zeros_like(b);e[i]=eps;numeric.append((c.objective(b+e,X,y,w,pen)[0]-c.objective(b-e,X,y,w,pen)[0])/(2*eps))
  np.testing.assert_allclose(grad,numeric,rtol=1e-6,atol=1e-6)
 def test_train_only_spec_no_mutation_and_target_raw_poison(self):
  original=copy.deepcopy(self.rows);model=c.fit(self.rows,self.data['labels'],'interaction100');self.assertEqual(self.rows,original);self.assertEqual(model['spec']['hitters'],[]);self.assertEqual(model['spec']['starters'],[])
  spec=c.core.legacy.make_spec([c.core.normalize(x) for x in self.rows]);self.assertEqual(model['spec']['mean'],spec['mean']);self.assertEqual(model['spec']['scale'],spec['scale']);self.assertEqual(model['weight_anchor_training_date'],max(r['event_date'] for r in self.rows))
  target=copy.deepcopy(self.rows[-3:])
  for i,row in enumerate(target):row['event_date']='2025-03-01';row['fixture_id']='future:'+str(i)
  pred=c.predict(model,target)
  for row in target:row['raw_features']={'target_and_future':{'value':999999}};row['target_game_score']=999999
  self.assertEqual(pred,c.predict(model,target))
 def test_only_registered_configs_and_penalties(self):
  self.assertEqual(set(c.CONFIGS),{'interaction10','interaction100','training_decay365','training_decay365_interaction100'})
  for name,config in c.CONFIGS.items():
   self.assertIn(config['interaction_penalty'],(None,10.,100.));self.assertIn(config['decay_days'],(None,365))
  with self.assertRaises(ValueError):c.fit(self.rows,self.data['labels'],'unregistered')
if __name__=='__main__':unittest.main()
