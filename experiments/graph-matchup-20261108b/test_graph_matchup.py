import importlib.util,datetime,gzip,json,decimal,unittest
from pathlib import Path
P=Path(__file__).with_name('run.py'); spec=importlib.util.spec_from_file_location('graph_study',P); mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
class GraphMatchupTests(unittest.TestCase):
 def test_four_day_weekend_and_month_end(self):
  rows=[{'day':d} for d in ['2024-02-25','2024-02-26','2024-02-27','2024-02-29','2024-03-01']]
  self.assertEqual([r['day'] for r in mod.available(rows,'2024-03-01')],['2024-02-25','2024-02-26'])
  rows=[{'day':d} for d in ['2025-12-27','2025-12-28','2025-12-29','2026-01-01']]
  self.assertEqual([r['day'] for r in mod.available(rows,'2026-01-01')],['2025-12-27','2025-12-28'])
 def test_unknown_team_and_future_edge(self):
  rows=[{'day':'2025-12-28','league':'L','home':'A','away':'B','market_p':[.5,.5],'y':0}]*12
  rows+= [{'day':'2025-12-29','league':'L','home':'C','away':'D','market_p':[.5,.5],'y':0}]
  for kind in ('additive','laplacian','matchup'):
   fitted=mod.fit(mod.available(rows,'2026-01-01'),kind)
   self.assertNotIn(('L','C'),fitted['teams']);self.assertNotIn(('L','C','D'),fitted['pairs'])
   unknown={'league':'L','home':'C','away':'D','market_p':[.5,.5]};self.assertAlmostEqual(mod.predict(fitted,unknown),float(mod.expit(fitted['x'][0])))
 def test_artifact_alignment_probability_settlement(self):
  path=P.with_name('predictions.jsonl.gz'); self.assertTrue(path.exists()); last=None; names=[]; n=0
  with gzip.open(path,'rt',encoding='utf8') as f:
   for line in f:
    r=json.loads(line); identity=(r['event_id'],r['day'],r['sport'],r['league'],r['y'],r['odds'],r['market_p'])
    if identity!=last:
     if last is not None:self.assertEqual(names,list(mod.MODELS))
     last=identity; names=[];n+=1
    names.append(r['model']);self.assertAlmostEqual(sum(r['p']),1);self.assertTrue(all(0<p<1 for p in r['p']))
    self.assertEqual(r['hit'],r['pick']==r['y']); net=decimal.Decimal(str(r['odds'][r['pick']]))-1 if r['hit'] else decimal.Decimal(-1);self.assertEqual(decimal.Decimal(r['net']),net)
  self.assertEqual(names,list(mod.MODELS));self.assertGreater(n,100)
if __name__=='__main__':unittest.main()
