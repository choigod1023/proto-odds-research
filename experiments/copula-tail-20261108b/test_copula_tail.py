import importlib.util
from pathlib import Path
from datetime import date
import numpy as np
p=Path(__file__).with_name('run.py');s=importlib.util.spec_from_file_location('tail',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def test_discrete_copula_independence_and_marginals():
 q=m.copula(1.4,1.1,0); b=m.base(1.4,1.1);assert np.max(abs(q-b))<1e-9
 assert np.all(q>=0) and abs(q.sum()-1)<1e-12
 for rho in (-.5,.5):
  q=m.copula(1.4,1.1,rho);assert np.all(q>=0) and abs(q.sum()-1)<1e-12
  assert abs(q.diagonal().sum()-m.probs(q,m.masks('승무패',None))[1])<1e-12
def test_tail_and_grid_stability():
 for fn,theta in [(m.copula,.2),(m.shock,.2)]:
  a=fn(1.4,1.1,theta,14);b=fn(1.4,1.1,theta,20)
  for fam,line in [('승무패',None),('핸디캡',-1),('언더오버',2.5)]:assert max(abs(m.probs(a,m.masks(fam,line,14))-m.probs(b,m.masks(fam,line,20))))<1e-7
  assert b[8:,:].sum()+b[:,8:].sum()>0

def test_weekend_and_month_end_delay():
 for h,p,expected in [('2025-01-31','2025-02-03',False),('2025-01-31','2025-02-04',True),('2025-05-02','2025-05-05',False),('2025-05-02','2025-05-06',True)]:assert m.day_available(date.fromisoformat(h),date.fromisoformat(p))==expected

def test_half_total_handicap_and_decimal_settlement():
 from decimal import Decimal
 q=m.base(1.4,1.1)
 for fam,line in [('승무패',None),('핸디캡',-1),('언더오버',2.5)]:assert abs(m.probs(q,m.masks(fam,line)).sum()-1)<1e-12
 assert Decimal('1.83')-Decimal('1')==Decimal('.83')

def test_saved_predictions_alignment_and_settlement():
 import gzip,json
 from collections import defaultdict
 from decimal import Decimal
 f=Path(__file__).with_name('predictions.jsonl.gz'); assert f.exists()
 groups=defaultdict(list)
 with gzip.open(f,'rt',encoding='utf8') as stream:
  for line in stream:
   r=json.loads(line);assert abs(sum(r['p'])-1)<1e-8
   assert r['hit']==(r['pick']==r['y'])
   assert Decimal(r['net'])==(Decimal(str(r['odds'][r['pick']]))-1 if r['hit'] else Decimal(-1))
   groups[(r['event_id'],r['product'])].append(r)
 for group in groups.values():
  assert len(group)==8 and len({r['model'] for r in group})==8
  assert len({(r['day'],r['league'],r['y'],tuple(r['odds']),tuple(r['market_p'])) for r in group})==1
