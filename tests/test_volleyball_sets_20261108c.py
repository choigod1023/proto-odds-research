import importlib.util
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'experiments/volleyball-sets-20261108c/run.py';spec=importlib.util.spec_from_file_location('set_study',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def test_no_same_day_result_leakage():
 rows=[]
 for i in range(5):
  rows.extend([{'day':f'2023-01-0{i+1}','id':f'a{i}','league':'L','home':'H','away':'X','hs':3,'as':0},{'day':f'2023-01-0{i+1}','id':f'b{i}','league':'L','home':'A','away':'Y','hs':3,'as':1}])
 rows.extend([{'day':'2023-01-06','id':'c','league':'L','home':'H','away':'A','hs':3,'as':0},{'day':'2023-01-06','id':'d','league':'L','home':'H','away':'A','hs':0,'as':3}])
 out=m.daily_features(rows);assert len(out)==2 and out[0]['feature']==out[1]['feature']
def test_metrics_uses_selected_odds_and_chronological_drawdown():
 r=[{'y':1,'odds':[1.5,2.7]},{'y':0,'odds':[1.7,2.2]}];v=m.metrics(r,[.8,.8]);assert v['accuracy']==.5 and abs(v['roi']+.25)<1e-9 and abs(v['mdd']-1)<1e-9
