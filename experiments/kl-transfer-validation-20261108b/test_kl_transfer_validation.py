import gzip,json,sys
from decimal import Decimal
from pathlib import Path
import numpy as np
P=Path(__file__).parent;sys.path.insert(0,str(P))
import frozen_reference as f

def test_grid_simplex_and_settlement():
 q=f.poisson(1.5,1.2); offers=[(f.masks('승무패',None),np.array([.46,.26,.28])),(f.masks('핸디캡',-1),np.array([.2,.25,.55])),(f.masks('언더오버',2.5),np.array([.52,.48]))]
 p=f.project(q,offers);assert np.isclose(p.sum(),1) and np.all(p>=0)
 assert all(np.isclose((m@p).sum(),1) for m,_ in offers)
 assert f.choose_outcome('핸디캡',-1,2,1)==1
 assert f.choose_outcome('언더오버',2,1,1) is None

def test_outcome_never_enters_fit_on_weekend_or_month_end():
 odds=[(f.masks('승무패',None),f.devig([2.1,3.2,3.8])),(f.masks('핸디캡',-1),f.devig([4.,3.5,1.6])),(f.masks('언더오버',2.5),f.devig([1.8,1.9]))]
 # Models are functions only of prices: hypothetical Saturday/month-end outcomes do not enter.
 first=f.project(f.fit(odds[:1]),odds); _hypothetical_future_results=[('2026-01-31',0),('2026-02-01',2)]
 assert np.array_equal(first,f.project(f.fit(odds[:1]),odds))

def test_saved_artifact_matches_decimal_and_market_probabilities():
 with gzip.open(P/'predictions.jsonl.gz','rt',encoding='utf8') as z:
  rows=[json.loads(x) for x in z]
 assert len(rows)==(563+147+451)*3*5
 for r in rows:
  assert abs(sum(r['p'])-1)<1e-10 and min(r['p'])>=0
  assert r['hit']==(r['pick']==r['y'])
  assert Decimal(r['net'])==(Decimal(str(r['odds'][r['pick']]))-1 if r['hit'] else Decimal(-1))
  assert f.choose_outcome(r['family'],r['line'],*r['score'])==r['y']
  if r['model']=='market_devig':assert r['p']==r['market_p'] and r['pick']==int(np.argmax(r['p']))
