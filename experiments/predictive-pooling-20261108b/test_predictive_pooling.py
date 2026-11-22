import importlib.util
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('pool',HERE/'run.py'); pool=importlib.util.module_from_spec(spec);spec.loader.exec_module(pool)
spec=importlib.util.spec_from_file_location('source_common',HERE.parent/'all-sports-underdog-20261031'/'common.py'); source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)

def test_beta_boundaries_and_threeway():
    x=np.array([[[0,0,1]]*3,[[1,0,0]]*3,[[.3,.3,.4]]*3],float)
    for a,b in ((.2,3),(1,1),(3,.2)):
        p=pool.beta_pool(x,a,b)
        assert np.isfinite(p).all() and (p>=0).all() and np.allclose(p.sum(axis=1),1)
        assert np.allclose(p[0],[0,0,1]) and np.allclose(p[1],[1,0,0])

def test_delayed_weekend_and_month_boundary():
    def row(d,y,i):return {'day':d,'kickoff':d+'T00:00:00','id':str(i),'sport':'bs','league':'L','home':'H','away':'A','y':y}
    # Friday result becomes available Tuesday, which crosses the month boundary.
    rows=[row('2025-01-31',0,1),row('2025-02-01',0,2),row('2025-02-03',0,3),row('2025-02-04',0,4)]
    a,x=source.causal_features(rows); altered=[dict(r) for r in rows];altered[0]['y']=1;_,z=source.causal_features(altered)
    assert np.allclose(x[:3],z[:3]);assert not np.allclose(x[3],z[3])
    assert len(pool.eligible_fit(rows,'2025-02-03'))==3

def test_convex_correlation_and_freeze():
    x=np.array([[[.8,.2],[.8,.2],[.3,.7]],[[.7,.3],[.7,.3],[.2,.8]],[[.3,.7],[.3,.7],[.8,.2]],[[.2,.8],[.2,.8],[.7,.3]]]);y=np.array([0,0,1,1]);s=pool.fit(x,y)
    assert min(s['weights'])>=0 and abs(sum(s['weights'])-1)<1e-8
    assert s['error_covariance'][0][1]>0
    for model in pool.NAMES:
        p=pool.prob(x,model,s);assert np.allclose(p.sum(axis=1),1) and (p>=0).all()
    r=[{'day':'2025-12-28'},{'day':'2025-12-29'},{'day':'2026-01-01'}]
    assert pool.eligible_fit(r,'2025-12-28')==r[:1]

def test_decimal_settlement_and_paired():
    r={'event_id':'x','day':'2026-01-01','y':0,'p':[.6,.4],'hit':True,'net':'0.85'}
    b={**r,'p':[.5,.5],'hit':False,'net':'-1'}
    assert pool.metrics([r])['roi']==.85
    out=pool.paired([r],[b]);assert out['metrics']['roi']['delta']==1.85 and out['metrics']['roi']['ci95']==[1.85,1.85]
