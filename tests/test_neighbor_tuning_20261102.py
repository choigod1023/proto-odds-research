import copy,importlib.util,sys
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'experiments'/'neighbor-tuning-20261102'
sys.path.insert(0,str(P))
spec=importlib.util.spec_from_file_location('tuning_run',P/'run.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_probabilities_and_feature_gate():
    rows=m.b.read();hist=[r for r in rows if r['split']=='meta_history_2024'];t=hist[-1];x={'market_p':t['market_p'],'dc_p':t['dc_p']}
    for c in m.CONFIGS:
        p=m.forecast(hist,x,c);assert abs(sum(p)-1)<1e-12 and all(v>0 for v in p)
    with pytest.raises(ValueError):m.forecast(hist,{**x,'y':0},m.CONFIGS[0])

def test_future_outcomes_poisoned():
    rows=m.b.read();changed=copy.deepcopy(rows)
    for r in changed:
        if r['split']=='evaluation_2025':r['y']=(r['y']+1)%3;r['home_score']=999;r['away_score']=999
    a,la=m.build(rows);b,lb=m.build(changed)
    assert a['selected']==b['selected'] and a['validation_candidates']==b['validation_candidates']
    assert [[v['probability'] for v in r['forecasts'].values()] for r in la]==[[v['probability'] for v in r['forecasts'].values()] for r in lb]

def test_market_fallback(monkeypatch):
    rows=m.b.read();hist=[r for r in rows if r['split']=='meta_history_2024']
    monkeypatch.setattr(m,'forecast',lambda bank,target,cfg:target['market_p'])
    assert m.select(hist)[0] is None
