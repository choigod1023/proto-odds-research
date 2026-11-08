import importlib.util
from pathlib import Path
from datetime import date,timedelta
P=Path(__file__).with_name('run.py'); spec=importlib.util.spec_from_file_location('regime_run',P); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def row(day,ident,y):
 return dict(event_id=ident,day=day,sport='bs',league='test',home='h',away='a',y=y,odds=[1.8,2.1],market_p=[.55,.45],form_p=.55,fallback=False)
def forecasts(rows):
 return {(r['day'],r['model']):r['p'] for r in m.calculate(rows)}
def test_weekend_month_boundary_delay_and_no_future_leakage():
 # Friday 30 Jan is released Tuesday 3 Feb: no observation exactly on due date.
 a=[row('2026-01-30','a',0),row('2026-02-02','b',1),row('2026-02-04','c',0),row('2026-02-05','d',1)]
 b=[dict(x) for x in a];b[0]['y']=1
 x,y=forecasts(a),forecasts(b)
 for model in ('short_long','bocpd','gas'):
  assert x['2026-02-02',model]==y['2026-02-02',model]
  assert x['2026-02-04',model]!=y['2026-02-04',model]
 c=[dict(x) for x in a];c[-1]['y']=0
 assert forecasts(a)['2026-02-05','bocpd']==forecasts(c)['2026-02-05','bocpd']
def test_probability_and_decimal_settlement():
 rows=m.calculate([row('2026-01-02','a',0)])
 assert len(rows)==5
 for r in rows:
  assert abs(sum(r['p'])-1)<1e-12
  assert r['net']=='0.8' and r['hit']
def test_conditional_draw_exclusion_and_mapping():
 r={'market_p':[.5,.2,.3],'odds':[1.8,3,4],'y':1,'predictions':{'outcome_form':{'p':[.4,.3,.3]}},'id':'x','day':'2026-01-01','sport':'sc','league':'l','home':'h','away':'a','fallback':False}
 assert m.transform(r) is None
 r['y']=2; x=m.transform(r)
 assert x['y']==1 and x['odds']==[1.8,4] and abs(sum(x['market_p'])-1)<1e-12
