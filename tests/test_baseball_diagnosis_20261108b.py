import importlib.util, unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'experiments/baseball-diagnosis-20261108b/run.py'
s=importlib.util.spec_from_file_location('baseball_diagnostic',p); m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class BaseballDiagnosticTest(unittest.TestCase):
 def test_draw_refund_and_hit_denominator(self):
  rows=[{'winner':'draw','arms':{'x':{'side':'home','odds':2.,'profit':m.profit('home','draw',{'home':2})}}},{'winner':'home','arms':{'x':{'side':'home','odds':2.,'profit':m.profit('home','home',{'home':2}),'p_home':.7}}}]
  a=m.metrics(rows,'x');self.assertEqual((a['n'],a['decided'],a['refunds'],a['hits']), (2,1,1,1));self.assertEqual(a['roi'],.5)
 def test_temperature_keeps_direction_and_probability(self):
  for p in (.01,.3,.5,.7,.99):
   q=m.sigmoid(.75*m.logit(p)); self.assertEqual(p>=.5,q>=.5); self.assertGreater(q,0); self.assertLess(q,1)
 def test_paired_interval_same_predictions_is_zero(self):
  rows=[{'date':'2026-01-01','winner':'home','arms':{k:{'profit':1.,'p_home':.6} for k in ('a','b')}}]
  self.assertEqual(m.ci(rows,'a','b'),{'roi':[0.,0.],'logloss':[0.,0.]})
if __name__=='__main__': unittest.main()
