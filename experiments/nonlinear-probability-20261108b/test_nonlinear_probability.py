import importlib.util, json, gzip, sys
from decimal import Decimal
from pathlib import Path
import numpy as np
P=Path(__file__).parent
spec=importlib.util.spec_from_file_location('nonlinear',P/'run.py'); mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

def test_day_four_weekend_month_end():
    def row(day,y,i): return {'day':day,'kickoff':day+'T12:00:00','id':str(i),'sport':'bs','league':'L','home':'H','away':'A','y':y}
    # Saturday month end; first permitted state use is Wednesday, not Sunday/Tuesday.
    original=[row('2026-01-31',0,0),row('2026-02-01',0,1),row('2026-02-03',0,2),row('2026-02-04',0,3)]
    changed=[dict(r) for r in original];changed[0]['y']=1
    _,a=mod.causal_features(original);_,b=mod.causal_features(changed)
    assert np.allclose(a[:3],b[:3]); assert not np.allclose(a[3],b[3])

def test_artifact_alignment_settlement():
    by={}
    with gzip.open(P/'predictions.jsonl.gz','rt',encoding='utf8') as f:
        for line in f:
            r=json.loads(line);key=(r['sport'],r['event_id']); canonical=(r['day'],r['league'],r['y'],r['odds'],r['market_p'])
            if key in by: assert by[key]==canonical
            else: by[key]=canonical
            assert abs(sum(r['p'])-1)<1e-10 and all(0<=p<=1 for p in r['p'])
            assert r['pick']==int(np.argmax(r['p'])) and r['hit']==(r['pick']==r['y'])
            expected=Decimal(str(r['odds'][r['pick']]))-1 if r['hit'] else Decimal(-1)
            assert Decimal(r['net'])==expected
    summary=json.loads((P/'summary.json').read_text(encoding='utf8'))
    assert len(by)==sum(s['evaluation'] for s in summary['counts'].values())
    assert sum(v['overall']['n'] for sport in summary['models'].values() for v in sport.values())==4*len(by)

def test_no_training_future_and_reproducible_fit():
    rng=np.random.default_rng(4);x=rng.normal(size=(100,6));q=np.tile([.55,.45],(100,1));y=rng.integers(0,2,100);tr=np.arange(60);te=np.arange(80,100)
    for kind in ('linear','rff','spline'):
        p,_=mod.fit_predict(x,q,y,tr,te,kind);y2=y.copy();y2[60:]=1-y2[60:];p2,_=mod.fit_predict(x,q,y2,tr,te,kind)
        assert np.allclose(p,p2) and np.allclose(p.sum(axis=1),1)
