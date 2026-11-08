import importlib.util, unittest, copy
from pathlib import Path
p=Path(__file__).with_name('run.py');spec=importlib.util.spec_from_file_location('pairs',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class TestKboAdjacentPairs(unittest.TestCase):
 def setUp(self):
  self.rows=m.readgz(p.parent/'inputs.jsonl.gz');self.pred=m.readgz(p.parent/'predictions.jsonl.gz')
 def test_identity_order_and_temporal_split(self):
  self.assertEqual(len({r['id'] for r in self.rows}),len(self.rows));self.assertEqual(self.rows,sorted(self.rows,key=lambda x:(x['date'],x['id'])))
  for r in self.rows:
   for s in ('home','away'):self.assertEqual(len(set(r[s+'_lineup'])),9)
  self.assertTrue(all(x['date']>='2026-01-01' for x in self.pred))
 def test_pair_is_ordered_and_additive_is_not(self):
  r=copy.deepcopy(self.rows[0]);s=copy.deepcopy(r);s['home_lineup'][0],s['home_lineup'][1]=s['home_lineup'][1],s['home_lineup'][0]
  self.assertNotEqual({k for k in m.features(r,'home',True,1000) if k.startswith('pair=')},{k for k in m.features(s,'home',True,1000) if k.startswith('pair=')})
  self.assertEqual({k for k in m.features(r,'home',False,100) if k.startswith('batter=')},{k for k in m.features(s,'home',False,100) if k.startswith('batter=')})
 def test_eval_labels_do_not_change_predictions(self):
  train=[r for r in self.rows if r['date']<'2026-01-01'];r=copy.deepcopy(self.rows[-1]);model=m.fit(train,True,1000);a=m.predict(model,r,True,1000);r['home_runs']=99;r['away_runs']=0;b=m.predict(model,r,True,1000);self.assertTrue((a==b).all())
 def test_probability_symmetry(self):
  self.assertAlmostEqual(m.hp([4,4]),.5,places=12);self.assertAlmostEqual(m.hp([3,6])+m.hp([6,3]),1,places=12)
 def test_draw_and_prices(self):
  common=[x for x in self.pred if x['price'] and x['existing_p'] is not None];self.assertTrue(any(x['price']['winner']=='draw' for x in common))
  self.assertTrue(all(x['price']['winner']==('home' if x['home_runs']>x['away_runs'] else 'away' if x['home_runs']<x['away_runs'] else 'draw') for x in common))
 def test_all_evaluation_price_winners_and_score_join(self):
  evaluation=[r for r in self.rows if r['date']>='2026-01-01'];self.assertEqual(len(evaluation),709)
  by_id={r['id']:r for r in evaluation};self.assertEqual(len(self.pred),len(by_id))
  for x in self.pred:
   r=by_id[x['id']];self.assertEqual((x['home_runs'],x['away_runs'],x['price']),(r['home_runs'],r['away_runs'],r['price']))
   if x['price']:
    winner='home' if r['home_runs']>r['away_runs'] else 'away' if r['home_runs']<r['away_runs'] else 'draw'
    self.assertEqual(x['price']['winner'],winner)
 def test_source_conflict_gates(self):
  r={'fixture_id':'game','home_id':'A','away_id':'B'};s={'source_fixture_id':'game','hg':3,'ag':2};z={'fixture_id':'game','home_id':'A','away_id':'B','winner':'home','price_source':{'market_id':'m','prices':{'home':1.5,'away':2.2}}}
  self.assertTrue(m.compatible_price(r,s,z))
  for section,key,value in [('score','source_fixture_id','other'),('score','hg',0),('ledger','fixture_id','other'),('ledger','home_id','B'),('ledger','away_id','A'),('ledger','winner','draw'),('source','market_id',None),('prices','home',1),('prices','away',float('nan'))]:
   rr,ss,zz=copy.deepcopy(r),copy.deepcopy(s),copy.deepcopy(z); {'score':ss,'ledger':zz,'source':zz['price_source'],'prices':zz['price_source']['prices']}[section][key]=value
   self.assertFalse(m.compatible_price(rr,ss,zz),msg=(section,key))
  ss=copy.deepcopy(s);ss['ag']=ss['hg'];zz=copy.deepcopy(z);zz['winner']='draw';self.assertTrue(m.compatible_price(r,ss,zz))
if __name__=='__main__':unittest.main()
