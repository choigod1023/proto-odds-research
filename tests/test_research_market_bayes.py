import json
from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pytest
from scipy.optimize._numdiff import approx_derivative
from src import research_market_bayes as m


def synthetic_rows(n=120):
    rows=[];start=date(2024,1,1)
    for i in range(n):
        rows.append({'id':f'game-{i}','day':(start+timedelta(days=i)).isoformat(),
          'home_id':f'T{i%4}','away_id':f'T{(i+1)%4}', 'dc_lh':1.4,'dc_la':1.1,
          'market_lh':1.35,'market_la':1.05,'hg':i%3,'ag':(i//3)%3,'y':0})
    return rows


def test_analytic_gradient_matches_central_differences():
    rows=synthetic_rows(32);teams=['T0','T1','T2','T3'];home,away,rates,n=m._design(rows,teams)
    args=(home,away,rates,np.array([r['hg'] for r in rows]),np.array([r['ag'] for r in rows]),n)
    beta=np.array([.1,-.15,.1,-.1,.05,.12,-.08,.06,.03,-.02,.45])
    analytic=m.objective(beta,*args)[1]
    numerical=approx_derivative(lambda b:m.objective(b,*args)[0],beta,method='3-point')
    np.testing.assert_allclose(analytic,numerical,rtol=2e-6,atol=2e-6)


def test_hessian_and_deterministic_json_prediction():
    rows=synthetic_rows();state=m.fit(rows,'2024-05-15');h,a,r,n=m._design(rows,state['teams'])
    args=(h,a,r,np.array([x['hg'] for x in rows]),np.array([x['ag'] for x in rows]),n)
    eigen=np.linalg.eigvalsh(m._hessian(np.array(state['map']),args))
    assert eigen.min()>1e-8 and state['hessian_min_eigenvalue']>1e-8
    x={'id':'future','home_id':'T0','away_id':'T1','dc_lh':1.2,'dc_la':1.1,'market_lh':1.3,'market_la':1.0}
    p,details=m.predict(state,x); p2,d2=m.predict(json.loads(json.dumps(state)),x)
    assert p==p2 and details==d2 and sum(p)==pytest.approx(1,abs=1e-10) and all(v>=0 for v in p)


def test_unknown_team_seed_depends_on_identity_not_outcome():
    state=m.fit(synthetic_rows(),'2024-05-15')
    row={'id':'future','home_id':'new-one','away_id':'new-two','dc_lh':1.3,'dc_la':1.1,'market_lh':1.2,'market_la':1.0,'hg':1,'ag':0,'y':0}
    p,detail=m.predict(state,row); poisoned={**row,'hg':1000,'ag':999,'y':2}
    assert m.predict(state,poisoned)[0]==p and detail['unknown_teams']==['new-one','new-two']
    assert not np.array_equal(m._unknown_draws('future','new-one'),m._unknown_draws('future','new-two'))
    assert np.array_equal(m._unknown_draws('future','new-one'),m._unknown_draws('future','new-one'))
    assert np.allclose(m._unknown_draws('future','new-one').mean(axis=0),0,atol=1e-15)
    assert m.predict(state,{**row,'home_id':'T0','away_id':'new-one'})[1]['unknown_teams']==['new-one']


def test_cutoff_minimum_duplicates_and_invalid_inputs():
    rows=synthetic_rows()
    with pytest.raises(ValueError,match='100 unique'):m.fit(rows[:99],'2024-05-15')
    with pytest.raises(ValueError,match='100 unique'):m.fit(rows+[rows[0]],'2024-05-15')
    with pytest.raises(ValueError,match='embargo'):m.fit(rows,'2024-04-30')
    bad=[dict(r) for r in rows];bad[0]['hg']=-1
    with pytest.raises(ValueError,match='score'):m.fit(bad,'2024-05-15')


def test_2024_only_frozen_training_and_label_poison():
    import hashlib, subprocess
    path='experiments/kleague-three-candidates-20261101/oof.jsonl'
    blob=subprocess.check_output(['git','show','HEAD:'+path])
    manifest=json.loads(Path('experiments/kleague-three-candidates-20261101/input-manifest.json').read_text(encoding='utf8'))
    assert hashlib.sha256(blob).hexdigest()==manifest['features_sha256']
    rows=[r for r in (json.loads(line) for line in blob.decode('utf8').splitlines()) if r['split']=='meta_history_2024']
    assert len(rows)==228
    state=m.fit(rows,'2025-01-01');assert state['training_rows']==228
    row={**rows[0],'id':'poison-test','hg':0,'ag':0,'y':1}
    p,_=m.predict(state,row)
    assert p==m.predict(state,{**row,'hg':500,'ag':400,'y':2})[0]
    assert state['hessian_min_eigenvalue']>1e-8


def test_boundary_and_indefinite_hessian_fail_closed(monkeypatch):
    from types import SimpleNamespace
    rows=synthetic_rows()
    def bounded(fun,x0,**kwargs):
        x=np.array(x0);x[0]=2-1e-6;return SimpleNamespace(success=True,x=x,message='ok',fun=0.)
    monkeypatch.setattr(m,'minimize',bounded)
    with pytest.raises(ValueError,match='near bound'):m.fit(rows,'2024-05-15')
    monkeypatch.undo()
    monkeypatch.setattr(m,'_hessian',lambda beta,args: -np.eye(len(beta)))
    with pytest.raises(ValueError,match='non-positive'):m.fit(rows,'2024-05-15')

def test_predict_does_not_read_target_fields():
    state=m.fit(synthetic_rows(),'2024-05-15')
    class Guard(dict):
        def __getitem__(self,key):
            if key in {'hg','ag','y'}:raise AssertionError('target accessed')
            return super().__getitem__(key)
    row=Guard(id='fixture',home_id='T0',away_id='T1',dc_lh=1.4,dc_la=1.1,market_lh=1.3,market_la=1.0)
    assert sum(m.predict(state,row)[0])==pytest.approx(1)
