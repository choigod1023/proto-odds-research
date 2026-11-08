import importlib.util
from pathlib import Path
import numpy as np
p=Path(__file__).resolve().parents[1]/'experiments/volleyball-diagnosis-20261108b/run.py'
spec=importlib.util.spec_from_file_location('volley_diagnosis',p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def test_market_home_away_and_drawdown():
 rows=[{'odds':[2.,2.],'y':0},{'odds':[2.,2.],'y':1}]
 assert m.market(rows[0]) == .5
 r=m.evaluate(rows,[.6,.6]); assert r['accuracy']==.5 and r['roi']==0 and r['max_drawdown_units']==1

def test_fit_ignores_future_rows_by_caller_contract():
 rows=[{'odds':[1.5,3.0],'y':0},{'odds':[3.,1.5],'y':1}]*20
 b=m.fit(rows); assert np.isfinite(b).all()
 assert m.evaluate(rows,[m.market(r) for r in rows])['accuracy']==1

def test_ledger_audit_distinguishes_capture_from_asof(tmp_path):
 import gzip, json, importlib.util
 script=p.parent/'audit_ledger.py'; spec=importlib.util.spec_from_file_location('volley_ledger',script); a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
 row={'record_type':'prediction','snapshot_id':'s','event_id':'e','features':{'fixture':{'sport':'vl','league':'L','date':'d'},'tipster_hybrid':{'authors':0,'reason':'market_fallback_no_votes'}},'predictions':{'selection':'홈','market':'승패','odds':1.5},'model':{'selection_policy':'tipster-vote-v1'},'kickoff':'2026-11-04T10:00:00Z','captured_at':'2026-11-04T09:31:00Z','as_of':'2026-11-04T09:29:00Z','market_observed_at':'2026-11-04T09:28:00Z'}
 f=tmp_path/'ledger.gz';f.write_bytes(gzip.compress((json.dumps(row)+'\n').encode()));x=a.audit(f)['rows'][0]
 assert x['before_t30_asof'] and not x['before_t30_captured'] and x['result'] is None
