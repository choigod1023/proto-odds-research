import copy,datetime,importlib.util,sys,unittest
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parents[1]/'experiments/kleague2-lineup-challenger-20261103/model.py'
s=importlib.util.spec_from_file_location('k2_model',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class TestK2Model(unittest.TestCase):
 def test_regular_scope_and_identity(self):
  games,excluded=m.load_games();self.assertEqual(len(games),521);self.assertEqual(excluded.get('non_regular_playoff'),2)
  self.assertEqual(len({g['id'] for g in games}),521)
 def test_target_future_label_poison(self):
  games,_=m.load_games();a=m.history_features(games);changed=copy.deepcopy(games)
  cut=datetime.date(2026,7,1)
  for g in changed:
   if g['date']>=cut:g['score']=[999,999]
  self.assertEqual(a,m.history_features(changed))
 def test_same_day_history_and_missing(self):
  ids=[str(i).zfill(8) for i in range(22)]
  def g(i,d,teams,xi):return {'id':str(i),'date':datetime.date.fromisoformat(d),'teams':teams,'xi':xi}
  a=g(1,'2025-01-01',['A','B'],[ids[:11],ids[11:]])
  b=g(2,'2025-01-01',['A','C'],[ids[:11],ids[11:]])
  c=g(3,'2025-01-02',['A','D'],[ids[:11],ids[11:]])
  f=m.history_features([a,b,c]);self.assertEqual(f['2'][0],[0,0,1,1]);self.assertEqual(f['3'][0],[0,0,1,1]);self.assertEqual(f['3'][1],[0,0,1,1])
 def test_unknown_team_and_probability(self):
  xi=[str(i).zfill(8) for i in range(22)];g={'id':'x','teams':['NEW','OLD'],'xi':[xi[:11],xi[11:]]};feat={'x':[[0,0,1,1],[0,0,1,1]]};teams={'OLD':0}
  X,unknown=m.design([g],feat,teams,False);self.assertEqual(unknown,[[True,False]])
  model={'teams':teams,'coeff':np.zeros(X.shape[1]),'candidate':False}
  rates,p,_=m.predict(model,[g],feat);self.assertTrue(np.all(p>=0));self.assertAlmostEqual(float(p.sum()),1.0);self.assertEqual(rates.tolist(),[[1.,1.]])
if __name__=='__main__':unittest.main()
