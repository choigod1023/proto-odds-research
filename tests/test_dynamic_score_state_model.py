import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

P = Path(__file__).resolve().parents[1]/'experiments/dynamic-score-state-model-20261030'
spec = importlib.util.spec_from_file_location('new_dynamic_model',P/'model.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def row(day='2024-01-01', home=4, away=2, league='KBO'):
    return {'date':day,'hg':home,'ag':away,'league':league,'home':'A','away':'B'}


@pytest.mark.parametrize('kind',['score','outcome'])
@pytest.mark.parametrize('league',['KBO','K리그1'])
def test_normalized_finite_forecast_and_query_does_not_mutate(kind,league):
    s=m.State(league,kind,.001)
    before=s.fingerprint()
    p,_=s.predict('A','B',datetime(2024,2,1,tzinfo=timezone.utc))
    assert np.all(np.isfinite(p)) and all(v>0 for v in p) and abs(sum(p)-1)<1e-12
    assert len(p)==(3 if league=='K리그1' else 2)
    assert s.fingerprint()==before


@pytest.mark.parametrize('kind',['score','outcome'])
def test_game_update_moves_mean_and_preserves_psd(kind):
    s=m.State('KBO',kind,.001)
    t=m.due(row())
    p,_=s.predict('A','B',t)
    s.update(row(home=8,away=1),t)
    after,_=s.predict('A','B',t)
    assert after[0]>p[0]
    assert np.linalg.eigvalsh(s.cov).min()>-1e-8


@pytest.mark.parametrize('kind',['score','outcome'])
def test_query_frequency_never_changes_future_forecast(kind):
    s=m.State('KBO',kind,.001);s.update(row(),m.due(row()))
    other=s.clone()
    for day in range(1,90):
        s.predict('A','B',m.due(row())+timedelta(days=day))
    at=m.due(row())+timedelta(days=100)
    assert s.predict('A','B',at)==other.predict('A','B',at)
    assert s.fingerprint()==other.fingerprint()


def test_no_early_result_and_no_time_reversal():
    s=m.State('KBO');r=row()
    with pytest.raises(ValueError,match='embargo'):
        s.update(r,m.due(r)-timedelta(seconds=1))
    s.update(r,m.due(r))
    with pytest.raises(ValueError,match='backwards'):
        s.advance(m.due(r)-timedelta(days=1))


def test_symmetric_score_model_has_symmetric_home_away_probabilities():
    s=m.State('K리그1','score',0.)
    p,_=s.predict('A','B',datetime(2024,1,1,tzinfo=timezone.utc))
    assert abs(p[0]-p[2])<1e-12 and p[1]>0


@pytest.mark.parametrize('kind',['score','outcome'])
def test_roundtrip_state_preserves_probabilities(kind):
    s=m.State('KBO',kind,.001);s.update(row(),m.due(row()))
    restored=m.State.restore(json.loads(json.dumps(s.export())))
    t=m.due(row())+timedelta(days=200)
    assert s.predict('A','B',t)==restored.predict('A','B',t)


def test_future_and_current_results_cannot_change_current_prequential_prediction():
    hs=[row(f'2024-01-{i:02}',home=3+i%5,away=i%4) for i in range(1,28)]
    at=datetime(2024,1,18,tzinfo=timezone.utc)
    poison=copy.deepcopy(hs)
    for r in poison:
        if m.due(r)>at:r['hg'],r['ag']=r['ag'],r['hg']
    a=m.Engine(hs,'KBO','score',.0003);b=m.Engine(poison,'KBO','score',.0003)
    assert a.predict('A','B',at)==b.predict('A','B',at)
    assert a.state.fingerprint()==b.state.fingerprint()


def test_frozen_forecasts_ignore_all_test_labels_on_real_history():
    import gzip
    history=[json.loads(l) for l in gzip.open(P/'inputs/history.jsonl.gz','rt',encoding='utf-8')]
    history=[r for r in history if r['league']=='KBO']
    poison=copy.deepcopy(history)
    for r in poison:
        if r['date']>='2026-01-01':r['hg'],r['ag']=r['ag'],r['hg']
    freeze=datetime(2026,1,5,tzinfo=timezone.utc)
    for _,kind,q in m.CONFIGS:
        a=m.Engine(history,'KBO',kind,q);b=m.Engine(poison,'KBO',kind,q)
        a.advance(freeze);b.advance(freeze)
        assert a.state.fingerprint()==b.state.fingerprint()
        for day in ['2026-03-01','2026-06-01','2026-10-28']:
            at=datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
            assert a.predict('LG','KT',at,online=False)==b.predict('LG','KT',at,online=False)


def test_forecast_api_does_not_accept_odds_or_outcome():
    s=m.State('KBO')
    with pytest.raises(TypeError):
        s.predict('A','B',datetime.now(timezone.utc),odds=[2,3],outcome=1)


def test_unknown_team_and_large_score_remain_finite():
    s=m.State('KBO');r=row(home=28,away=0);s.update(r,m.due(r))
    p,_=s.predict('Unknown','Other',m.due(r)+timedelta(days=400))
    assert np.isfinite(p).all() and abs(sum(p)-1)<1e-12


def test_negative_scores_rejected():
    s=m.State('KBO');r=row(home=-1)
    with pytest.raises(ValueError):s.update(r,m.due(r))


def test_season_change_shrinks_team_mean_and_adds_uncertainty():
    s=m.State('KBO',q=0);r=row('2024-12-01');s.update(r,m.due(r))
    before=s.mean.copy();v=s.cov.copy()
    s.advance(datetime(2025,1,5,tzinfo=timezone.utc))
    assert np.max(np.abs(s.mean[2:]))<np.max(np.abs(before[2:]))
    assert np.mean(np.diag(s.cov)[2:])>np.mean(np.diag(v)[2:])


def test_threeway_tie_updates_without_becoming_a_binary_loss():
    s=m.State('K리그1','outcome',0);r=row(home=1,away=1,league='K리그1')
    at=m.due(r);before,_=s.predict('A','B',at);s.update(r,at);after,_=s.predict('A','B',at)
    assert after[1]>before[1]


def test_score_difference_probabilities_match_independent_poisson_summation():
    from scipy.stats import poisson,skellam
    a,b=3.5,2.9
    x=np.arange(100)
    joint=np.outer(poisson.pmf(x,a),poisson.pmf(x,b))
    direct=np.array([np.tril(joint,-1).sum(),np.trace(joint),np.triu(joint,1).sum()])
    away=skellam.cdf(-1,a,b);draw=skellam.pmf(0,a,b)
    assert np.max(np.abs(direct-np.array([1-away-draw,draw,away])))<1e-12


def test_persisted_forecast_keeps_identity_separate_from_home_control():
    import gzip
    with gzip.open(P/'results/predictions.jsonl.gz','rt',encoding='utf-8') as f:
        for _ in range(20):
            r=json.loads(next(f))
            assert isinstance(r['home'],str) and isinstance(r['away'],str)
            assert isinstance(r['home_constant'],dict)


@pytest.mark.parametrize('kind,league,z,y',[
    ('score','KBO',[1.2,.9],[3.,1.]),
    ('outcome','KBO',[.2],[1.]),
    ('outcome','K리그1',[.2,-.3],[0.,1.,0.]),
])
def test_analytic_likelihood_gradient_and_hessian(kind,league,z,y):
    s=m.State(league,kind);z=np.array(z);y=np.array(y);eps=1e-5
    _,g,h=s.likelihood(z,y)
    dg=[];dh=[]
    for i in range(len(z)):
        v=np.eye(len(z))[i]*eps
        plus=s.likelihood(z+v,y);minus=s.likelihood(z-v,y)
        dg.append((plus[0]-minus[0])/(2*eps));dh.append((plus[1]-minus[1])/(2*eps))
    assert np.max(np.abs(g-np.array(dg)))<1e-7
    assert np.max(np.abs(h-np.array(dh).T))<1e-7


def test_selection_floor_counts_nonvoid_validation_games(monkeypatch):
    monkeypatch.setitem(sys.modules,'model',m)
    sp=importlib.util.spec_from_file_location('new_dynamic_run_for_tests',P/'run.py')
    run=importlib.util.module_from_spec(sp);sp.loader.exec_module(run)
    games=[{'void':False} for _ in range(49)]+[{'void':True}]
    assert not run.selection_eligible(100,games)
    assert not run.selection_eligible(99,[{'void':False}]*50)
    assert run.selection_eligible(100,[{'void':False}]*50)
