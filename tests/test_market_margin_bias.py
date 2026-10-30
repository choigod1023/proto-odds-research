import importlib.util
from pathlib import Path
import numpy as np

spec=importlib.util.spec_from_file_location('margin_study',Path(__file__).resolve().parents[1]/'experiments/market-margin-bias-20261030/study.py')
study=importlib.util.module_from_spec(spec);spec.loader.exec_module(study)

def test_de_vig_probability_and_rank():
    for odds in ([1.7,2.2],[1.9,3.3,4.1]):
        for m in study.METHODS:
            p=study.probabilities(odds,m)
            assert abs(sum(p)-1)<1e-9 and np.all(p>0)
            assert int(np.argmax(p))==int(np.argmin(odds))

def test_temperature_keeps_argmax_and_draw_offset_can_change_it():
    for n in range(2,11):
        p=np.arange(n,0,-1,dtype=float); p/=p.sum()
        for temperature in (.1,.5,1,2,10):
            assert np.argmax(p**(1/temperature))==0
    assert np.argmax(study.corrected(np.array([.42,.35,.23]),[0,1,0]))==1

def test_heldout_labels_never_affect_coefficients_or_predictions():
    rows=[{'id':str(i),'league':'A','kickoff':f'2024-01-{i+1:02d}', 'odds':[1.7,2.2],'y':i%2,'split':0 if i<8 else 2} for i in range(10)]
    p,c=study.get_predictions(rows)
    changed=[dict(r,y=1-r['y']) if r['split']==2 else r for r in rows]
    q,d=study.get_predictions(changed)
    assert c==d
    for name in study.NAMES:
        for r in rows: assert np.array_equal(p[name][r['id']],q[name][r['id']])

def test_independent_settlement_all_metrics():
    rows=[{'id':'a','odds':[2.,3.],'y':1},{'id':'b','odds':[1.4,4.],'y':0}]
    p={'a':np.array([.4,.6]),'b':np.array([.7,.3])}
    assert np.array_equal(study.observations(rows,p),study.independent(rows,p))
    assert study.FEATURES.isdisjoint({'hit','result','final_odds','posted_at_after_game'})
