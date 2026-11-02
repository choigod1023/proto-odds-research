import copy
import importlib.util
import inspect
import json
from pathlib import Path
import numpy as np
import pytest

pytest.importorskip('sklearn', reason='optional research dependency is not in production requirements')

P = Path(__file__).resolve().parents[1] / 'experiments' / 'nested-search-20261102' / 'ml_models.py'
spec = importlib.util.spec_from_file_location('nested_ml_models', P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

@pytest.fixture
def example():
    rng = np.random.default_rng(123)
    train = {k: rng.normal(size=(48, width)) for k, width in m.WIDTHS.items()}
    target = {k: rng.normal(size=(7, width)) for k, width in m.WIDTHS.items()}
    y = np.arange(48) % 3
    market = np.tile([.45, .28, .27], (7, 1))
    return train, y, target, market


def test_grid_has_468_serializable_unique_configs():
    configs = m.configurations()
    assert len(configs) == 468 == len({c['id'] for c in configs})
    assert len({(c['family'], c['feature_set'], json.dumps(c['params'], sort_keys=True)) for c in configs}) == 78
    assert sum(c['blend'] == 0 for c in configs) == 78
    assert json.loads(json.dumps(configs)) == configs


def test_all_outputs_normalized_and_market_duplicates(example):
    train, y, target, market = example
    out = m.predict_all(train, y, target, market)
    assert len(out) == 468
    for c in m.configurations():
        p = out[c['id']]
        assert p.shape == (7, 3) and np.isfinite(p).all() and (p > 0).all()
        np.testing.assert_allclose(p.sum(axis=1), 1.0, atol=1e-12)
        if c['blend'] == 0:
            np.testing.assert_allclose(p, market, atol=1e-12)


def test_blends_share_fit_and_scaler_only_sees_training(example, monkeypatch):
    train, y, target, market = example
    subset = [c for c in m.configurations() if c['family'] == 'logistic' and c['feature_set'] == 'base' and c['params'] == {'C': .01, 'class_weight': None}]
    fits = []
    original = m.StandardScaler.fit
    def observe(self, x, *a, **kw):
        fits.append(np.array(x, copy=True))
        return original(self, x, *a, **kw)
    monkeypatch.setattr(m.StandardScaler, 'fit', observe)
    output = m.predict_all(train, y, target, market, subset)
    assert len(fits) == 1
    np.testing.assert_array_equal(fits[0], train['base'])
    model = output[subset[-1]['id']]
    np.testing.assert_allclose(output[subset[2]['id']], 0.75 * market + .25 * model, atol=1e-12)


def test_missing_class_and_single_class_prior(example):
    train, y, target, market = example
    subset = [next(c for c in m.configurations() if c['family'] == family and c['feature_set'] == 'base' and c['blend'] == 1) for family in ('logistic','random_forest','hist_gradient_boosting')]
    for labels in (np.zeros(48, dtype=int), np.arange(48) % 2):
        out = m.predict_all(train, labels, target, market, subset)
        for c in subset:
            p = out[c['id']]
            assert p.shape == (7, 3) and (p > 0).all()
            np.testing.assert_allclose(p.sum(axis=1), 1)
            if len(set(labels)) == 1:
                np.testing.assert_allclose(p, np.tile([49/51, 1/51, 1/51], (7, 1)))
            else:
                assert np.max(p[:,2]) < 1e-10


def test_determinism_and_no_target_label_api(example):
    train,y,target,market = example
    assert 'target_y' not in inspect.signature(m.predict_all).parameters
    with pytest.raises(TypeError):
        m.predict_all(train,y,target,market,target_y=np.zeros(7))
    subset = [next(c for c in m.configurations() if c['family'] == family and c['feature_set'] == 'base' and c['blend'] == .5) for family in ('logistic','random_forest','hist_gradient_boosting')]
    a = m.predict_all(train,y,target,market,subset)
    b = m.predict_all(train,y,target,market,subset)
    for k in a: np.testing.assert_array_equal(a[k], b[k])


def test_reject_bad_inputs_and_config_mutation(example):
    train,y,target,market = example
    subset = m.configurations()[:1]
    bad = copy.deepcopy(subset); bad[0]['params']['C'] = 999
    with pytest.raises(ValueError): m.predict_all(train,y,target,market,bad)
    with pytest.raises(ValueError): m.predict_all(train,y,target,market,subset*2)
    with pytest.raises(ValueError): m.predict_all(train,y+3,target,market,subset)
    with pytest.raises(ValueError): m.predict_all(train,y,{**target,'base':target['base'][:,:5]},market,subset)
    with pytest.raises(ValueError): m.predict_all(train,y,target,market*np.nan,subset)
    with pytest.raises(ValueError): m.predict_all(train,y,target,market*2,subset)
    with pytest.raises(ValueError): m.predict_all({**train,'outcome':np.zeros((48,1))},y,target,market,subset)


def test_empty_target(example):
    train,y,target,market = example
    output = m.predict_all(train,y,{k:v[:0] for k,v in target.items()},market[:0],m.configurations()[:2])
    assert all(p.shape == (0,3) for p in output.values())
