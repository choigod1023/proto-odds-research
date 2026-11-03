import copy,datetime,importlib.util,sys,unittest,json,hashlib,subprocess,math
from decimal import Decimal
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parents[1]/'experiments/kleague2-lineup-challenger-20261103/model.py'
s=importlib.util.spec_from_file_location('k2_model',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
sys.modules['model']=m
es=importlib.util.spec_from_file_location('k2_evaluation',P.parent/'evaluate.py');ev=importlib.util.module_from_spec(es);es.loader.exec_module(ev)
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
 def test_poison_target_labels_does_not_change_frozen_predictions(self):
  games,_=m.load_games();_,_,test=m.partition(games);features=m.history_features(games)
  params=json.loads((P.parent/'frozen/model-parameters.json').read_bytes())
  frozen=[json.loads(l) for l in (P.parent/'frozen/test-predictions.jsonl').read_bytes().splitlines()]
  altered=copy.deepcopy(test)
  for g in altered:g['score']=[999,999]
  for mode,candidate in [('team_only',False),('lineup',True)]:
   par=params[mode];fit={'teams':par['teams'],'coeff':np.array(par['coefficients']),'candidate':candidate}
   _,p,_=m.predict(fit,altered,features)
   for i,rec in enumerate(frozen):np.testing.assert_allclose(p[i],rec['models'][mode]['p'],atol=1e-12,rtol=0)
 def test_frozen_git_bytes(self):
  p=P.parent/'frozen/test-predictions.jsonl'; b=p.read_bytes()
  self.assertEqual(hashlib.sha256(b).hexdigest(),ev.PRED_SHA)
  repo=P.parents[2];rel=p.relative_to(repo).as_posix()
  self.assertEqual(subprocess.check_output(['git','show','HEAD:'+rel],cwd=repo),b)
  self.assertTrue(all('score' not in json.loads(line) and 'outcome' not in json.loads(line) for line in b.splitlines()))
 def test_price_identity_void_draw_and_market_guard(self):
  games,_=m.load_games();g=games[0];out=ev.outcomes(g)
  q={'fixture_id':g['id'],'league_id':2,'season':g['date'].year,'gmId':'G101','betman_league_code':'SC003','home_team_id':g['teams'][0],'away_team_id':g['teams'][1],'kickoff_kst':g['kickoff'],'official_score':g['score'],'settlement_code':str(out),'gameReject':'0','buyReject':'0','market_type':{'betId':'1','betTypId':'1','betNm':'\ucd95\uad6c \uc2b9\ubb34\ud328','handi':0},'prices':{'home':2.0,'draw':3.0,'away':4.0}}
  self.assertTrue(ev.valid_price(q,g))
  for patch in ({'home_team_id':g['teams'][1]},{'settlement_code':'4'},{'buyReject':'1'},{'prices':{'home':1,'draw':3.,'away':4.}},{'market_type':{'betId':'1','betTypId':'1','betNm':'\ucd95\uad6c \uc2b9\ubb34\ud328','handi':1}}):
   self.assertFalse(ev.valid_price(dict(q,**patch),g))
  row={'outcome':1,'p':{'lineup':[.2,.6,.2]},'odds':[2,3,4]};self.assertEqual(ev.profit(row,'lineup'),2.)
 def test_independent_metrics_and_decimal_settlement(self):
  rows=[json.loads(l) for l in (P.parent/'results/scored.jsonl').read_bytes().splitlines()];metrics=json.loads((P.parent/'results/metrics.json').read_bytes())
  for mode in ('team_only','lineup','market'):
   subset=[r for r in rows if r['odds'] is not None];hits=0;loss=0.;profit=Decimal(0)
   for r in subset:
    p=r['p'][mode];pick=max(range(3),key=lambda i:p[i]);y=r['outcome'];hits+=pick==y;loss-=math.log(p[y]);profit+=Decimal(str(r['odds'][pick]))-1 if pick==y else Decimal(-1)
   a=metrics['price_matched_accuracy_loss'][mode];b=metrics['price_matched_returns'][mode]
   self.assertEqual(a['hits'],hits);self.assertAlmostEqual(a['logloss'],loss/len(subset),places=12);self.assertAlmostEqual(b['profit_units'],float(profit),places=10)
if __name__=='__main__':unittest.main()
