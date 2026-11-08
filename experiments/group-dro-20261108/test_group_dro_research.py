import importlib.util,pathlib,unittest
import numpy as np
p=pathlib.Path(__file__).with_name('run.py'); spec=importlib.util.spec_from_file_location('dro',p); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
class TestDRO(unittest.TestCase):
 def test_three_way_and_group_objective(self):
  rows=[]
  for g,ys in [('A',[0]*18+[1]*6+[2]*6),('B',[0]*6+[1]*12+[2]*12)]:
   for i,y in enumerate(ys): rows.append({'market_p':[.5,.3,.2],'y':y,'league':g,'odds':[2,3,5]})
  erm=mod.fit(rows,'erm'); dro=mod.fit(rows,'dro'); pe=mod.predict(rows,erm); pd=mod.predict(rows,dro)
  self.assertTrue(np.allclose(pd.sum(axis=1),1)); self.assertEqual(pd.shape,(60,3))
  def maxloss(p): return max(-np.log(p[np.array([r['league']==g for r in rows]),:][np.arange(30),np.array([r['y'] for r in rows if r['league']==g])]).mean() for g in ['A','B'])
  self.assertLess(maxloss(pd),maxloss(pe))
 def test_settlement(self):
  rows=[{'y':0,'odds':[2,3,4]},{'y':1,'odds':[2,3,4]}]; p=np.array([[.6,.2,.2],[.6,.2,.2]])
  m=mod.metrics(rows,p); self.assertEqual(m['roi'],0); self.assertEqual(m['max_drawdown_units'],1); self.assertEqual(m['accuracy'],.5)
if __name__=='__main__': unittest.main()
