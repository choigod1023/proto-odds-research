import importlib.util
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pytest

p = Path('experiments/nested-search-20261102/residual_models.py')
spec = importlib.util.spec_from_file_location('nested_residual_models', p)
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)


def fixture():
    rng = np.random.default_rng(843)
    n, m = 28, 3
    market = rng.dirichlet([3, 2, 2], n)
    dc = rng.dirichlet([3, 2, 2], n)
    form = rng.normal(size=(n, 12))
    outcomes = rng.integers(0, 3, n)
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]
    ids = [f'game-{i:03d}' for i in range(n)]
    target_market = rng.dirichlet([3, 2, 2], m)
    target_dc = rng.dirichlet([3, 2, 2], m)
    target_form = rng.normal(size=(m, 12))
    target_days = ['2025-01-20', '2025-02-15', '2025-01-08']
    return [market, dc, form, outcomes, days, ids, target_market, target_dc, target_form, target_days]


def test_grid_counts_and_json_fields():
    import json
    c = r.configurations(); p = r.pooling_configs()
    assert len(c) == len({x['id'] for x in c}) == 3360
    assert len(p) == len({x['id'] for x in p}) == 504
    assert set(c[0]) == {'id','family','space','k','prior','weight','half_life','blend'}
    json.dumps(c); json.dumps(p)


def test_grid_shapes_and_determinism():
    data = fixture(); a = r.predict_all(*data); b = r.predict_all(*data)
    assert len(a) == 3360
    assert all(v.shape == (3, 3) and np.allclose(v.sum(axis=1), 1) and np.isfinite(v).all() for v in a.values())
    assert all(np.array_equal(a[k], b[k]) for k in a)
    pool = r.predict_pool_all(data[6], data[7]); assert len(pool) == 504
    assert all(v.shape == (3, 3) and np.allclose(v.sum(axis=1), 1) for v in pool.values())
    assert np.array_equal(pool[r.pooling_configs()[0]['id']], r.predict_pool_all(data[6], data[7])[r.pooling_configs()[0]['id']])


def test_future_rows_and_same_day_poison_do_not_change_prediction():
    data = fixture(); data[9] = ['2025-01-20'] * 3
    original = r.predict_all(*data)
    corrupt = [x.copy() if hasattr(x, 'copy') else x for x in data]
    cutoff = date.fromisoformat('2025-01-20') - timedelta(days=7)
    excluded = np.array([date.fromisoformat(d) > cutoff for d in data[4]])
    corrupt[0][excluded] = [0.01, 0.01, .98]
    corrupt[1][excluded] = [.98, .01, .01]
    corrupt[2][excluded] = np.nan
    corrupt[3][excluded] = 99
    actual = r.predict_all(*corrupt)
    assert all(np.array_equal(original[k], actual[k]) for k in original)


def test_insufficient_history_and_empty_training_return_exact_market():
    data = fixture(); data[9] = ['2024-01-01'] * 3
    out = r.predict_all(*data)
    assert all(np.array_equal(x, data[6]) for x in out.values())
    empty = [np.empty((0,3)),np.empty((0,3)),np.empty((0,12)),[],[],[],*data[6:]]
    out = r.predict_all(*empty, configs=r.configurations()[:2])
    assert all(np.array_equal(x, data[6]) for x in out.values())


def test_form_transform_training_only_and_manual_oracle():
    data = fixture(); data[9] = ['2025-02-15'] * 3
    cfg = dict(id='manual', family='residual', space='form', k=10, prior=30,
               weight='inverse', half_life=30, blend=.75)
    got = r.predict_all(*data, configs=[cfg])['manual'][0]
    train = np.concatenate((data[0], data[1], data[2]), axis=1)
    target = np.concatenate((data[6][0], data[7][0], data[8][0]))
    mean = train.mean(axis=0); scale = train.std(axis=0); scale[scale == 0] = 1
    dist = np.linalg.norm((train - mean)/scale - (target - mean)/scale, axis=1)
    ix = sorted(range(len(dist)), key=lambda i:(dist[i], data[5][i]))[:10]
    weights = 1/(dist[ix]+.05); weights *= 10/weights.sum()
    ages = np.array([(date.fromisoformat(data[9][0])-date.fromisoformat(data[4][i])).days for i in ix])
    weights *= 2**(-ages/30)
    residual = np.eye(3)[data[3][ix]] - data[0][ix]
    expected = np.maximum(data[6][0] + .75 * (weights @ residual)/(weights.sum()+30), 1e-6)
    expected /= expected.sum()
    assert np.allclose(got, expected, rtol=1e-13, atol=1e-13)
    # Other target features must not be used when fitting target 0's transform.
    data[8][1:] = 1000000
    assert np.array_equal(got, r.predict_all(*data, configs=[cfg])['manual'][0])


def test_pooling_formula_and_zeros():
    market=np.array([[.5,.3,.2],[0,1,0]])
    dc=np.array([[.3,.2,.5],[0,1,0]])
    c=dict(id='p',family='pool',weight=.25,temperature=1.25,home_bias=.1,draw_bias=-.1)
    actual=r.predict_pool_all(market,dc,[c])['p']
    logits=np.log(.75*market[0]+.25*dc[0])/1.25+np.array([.1,-.1,0])
    expected=np.exp(logits-logits.max());expected/=expected.sum()
    assert np.allclose(actual[0],expected) and np.array_equal(actual[1],[0,1,0])


def test_reject_duplicate_ids_invalid_dates_and_invalid_probabilities():
    data=fixture(); data[5][1]=data[5][0]
    with pytest.raises(ValueError,match='unique'):r.predict_all(*data,configs=[])
    data=fixture(); data[4][0]='2025-1-1'
    with pytest.raises(ValueError,match='ISO|YYYY'):r.predict_all(*data,configs=[])
    data=fixture();data[6][0]=[.1,.1,.1]
    with pytest.raises(ValueError,match='probability'):r.predict_all(*data,configs=[])


def test_equal_distances_use_id_not_input_order():
    data=fixture(); data[0][:]=[.4,.3,.3]; data[1][:]=[.4,.3,.3];data[6][:]=[.4,.3,.3]
    data[9]=['2025-02-15']*3
    cfg=dict(id='tie',family='residual',space='market',k=10,prior=10,weight='uniform',half_life=None,blend=1.)
    a=r.predict_all(*data,configs=[cfg])['tie']
    idx=np.arange(len(data[0]))[::-1]
    rev=[data[0][idx],data[1][idx],data[2][idx],data[3][idx],[data[4][i] for i in idx],[data[5][i] for i in idx],*data[6:]]
    assert np.array_equal(a,r.predict_all(*rev,configs=[cfg])['tie'])

def test_seven_day_boundary_and_target_label_absent():
    import inspect
    assert 'target_y' not in inspect.signature(r.predict_all).parameters
    cfg=dict(id='boundary',family='residual',space='market',k=1,prior=0,weight='uniform',half_life=None,blend=1.)
    market=np.array([[.3,.4,.3],[.3,.4,.3],[.3,.4,.3]])
    dc=market.copy();form=np.zeros((3,12));y=np.array([0,1,2]);days=['2025-01-01','2025-01-02','2025-01-08'];ids=['a','b','c']
    target=np.array([[.3,.4,.3]]);tform=np.zeros((1,12))
    out=r.predict_all(market,dc,form,y,days,ids,target,target,tform,['2025-01-08'],[cfg])['boundary'][0]
    expected=np.array([1.,0.,0.]);expected=np.maximum(expected,1e-6);expected/=expected.sum()
    assert np.allclose(out,expected)


def test_prior_weight_and_temporal_decay_against_small_independent_calculation():
    cfg=dict(id='weights',family='residual',space='market',k=2,prior=10,weight='inverse',half_life=30,blend=.5)
    market=np.array([[.5,.2,.3],[.2,.3,.5]])
    dc=market.copy(); form=np.zeros((2,12)); target=np.array([[.4,.3,.3]]); tf=np.zeros((1,12))
    y=np.array([0,2]);days=['2025-01-01','2025-01-11'];date_out='2025-02-01'
    got=r.predict_all(market,dc,form,y,days,['b','a'],target,target,tf,[date_out],[cfg])['weights'][0]
    dist=np.sqrt(((market-target)**2).sum(axis=1)); w=1/(dist+.05);w=2*w/w.sum();w*=np.exp2(-np.array([31,21])/30)
    expected=target[0]+.5*((w[:,None]*(np.eye(3)[y]-market)).sum(axis=0))/(w.sum()+10)
    expected=np.maximum(expected,1e-6);expected/=expected.sum()
    assert np.allclose(got,expected,rtol=1e-14,atol=1e-14)

def test_invalid_eligible_data_is_rejected():
    data=fixture(); data[2][0,0]=np.nan; data[9]=['2025-02-15']*3
    with pytest.raises(ValueError,match='eligible train_form'):
        r.predict_all(*data,configs=r.configurations()[:1])
