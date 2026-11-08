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
