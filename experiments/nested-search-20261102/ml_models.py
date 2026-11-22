"""Fixed ML search candidates; array-only, no target outcomes or I/O."""
from itertools import product
import json
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

SEED = 20261102
WIDTHS = {'base': 6, 'disagreement': 11, 'form': 23}
BLENDS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)
FLOOR = 1e-12


def configurations():
    """Return 468 unique JSON-compatible descriptors, including market duplicates."""
    specs = []
    for c, weight in product((0.01, 0.1, 1.0, 10.0), (None, 'balanced')):
        specs.append(('logistic', {'C': c, 'class_weight': weight}))
    for depth, leaf in product((2, 4, None), (5, 15)):
        specs.append(('random_forest', {'max_depth': depth, 'min_samples_leaf': leaf}))
    for leaves, l2, rate in product((3, 7, 15), (0.0, 10.0), (0.05, 0.1)):
        specs.append(('hist_gradient_boosting', {'max_leaf_nodes': leaves, 'l2_regularization': l2, 'learning_rate': rate}))
    out = []
    for feature in WIDTHS:
        for family, params in specs:
            for blend in BLENDS:
                ident = f'ml-{len(out):03d}'
                out.append({'id': ident, 'family': family, 'feature_set': feature,
                            'params': dict(params), 'blend': blend})
    return out


def _probability(p):
    arr = np.asarray(p, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3 or not np.isfinite(arr).all() or (arr < 0).any():
        raise ValueError('probabilities must be finite nonnegative n by 3')
    sums = arr.sum(axis=1)
    if not np.isfinite(sums).all() or (sums <= 0).any():
        raise ValueError('probability rows must have positive finite sums')
    arr = np.maximum(arr, FLOOR)
    return arr / arr.sum(axis=1, keepdims=True)


def _fit_predict(family, params, x, y, target):
    if len(np.unique(y)) == 1:
        prior = (np.bincount(y, minlength=3) + 1.0) / (len(y) + 3.0)
        return np.tile(prior, (len(target), 1))
    if family == 'logistic':
        scaler = StandardScaler().fit(x)
        model = LogisticRegression(C=params['C'], class_weight=params['class_weight'],
                                   solver='lbfgs', max_iter=1000, random_state=SEED)
        train = scaler.transform(x)
        future = scaler.transform(target)
    elif family == 'random_forest':
        model = RandomForestClassifier(n_estimators=64, max_features=1.0, n_jobs=1,
                                       random_state=SEED, **params)
        train, future = x, target
    elif family == 'hist_gradient_boosting':
        model = HistGradientBoostingClassifier(max_iter=100, min_samples_leaf=15,
                                               early_stopping=False, random_state=SEED, **params)
        train, future = x, target
    else:
        raise ValueError('unknown model family')
    model.fit(train, y)
    values = model.predict_proba(future)
    full = np.zeros((len(target), 3), dtype=float)
    for col, label in enumerate(model.classes_):
        full[:, int(label)] = values[:, col]
    return _probability(full)


def predict_all(train_features, train_y, target_features, target_market, configs=None):
    """Train only on supplied training labels; produce fixed blends, never select a model.

    Feature arrays must already be chronologically safe. Probabilities are ordered
    home/draw/away; target outcome labels are deliberately not an argument.
    """
    registry = configurations()
    by_id = {c['id']: c for c in registry}
    selected = registry if configs is None else list(configs)
    seen = set()
    for c in selected:
        if not isinstance(c, dict) or c.get('id') not in by_id or c != by_id[c['id']] or c['id'] in seen:
            raise ValueError('configs must be unique unmodified fixed descriptors')
        seen.add(c['id'])
    if not isinstance(train_features, dict) or not isinstance(target_features, dict):
        raise ValueError('feature sets must be dictionaries')
    if set(train_features) - set(WIDTHS) or set(target_features) - set(WIDTHS):
        raise ValueError('unexpected feature set')
    y = np.asarray(train_y)
    if y.ndim != 1 or len(y) == 0 or y.dtype.kind not in 'iu' or not np.isin(y, [0, 1, 2]).all():
        raise ValueError('training labels must be a nonempty 0/1/2 integer vector')
    y = y.astype(int, copy=False)
    market = np.asarray(target_market, dtype=float)
    if market.ndim != 2 or market.shape[1] != 3 or not np.isfinite(market).all() or (market < 0).any() or not np.allclose(market.sum(axis=1), 1.0, atol=1e-8, rtol=0):
        raise ValueError('target market must be finite normalized n by 3')
    n = len(market)
    arrays = {}
    for feature in {c['feature_set'] for c in selected}:
        if feature not in train_features or feature not in target_features:
            raise ValueError('missing feature set')
        x = np.asarray(train_features[feature], dtype=float)
        t = np.asarray(target_features[feature], dtype=float)
        if x.shape != (len(y), WIDTHS[feature]) or t.shape != (n, WIDTHS[feature]) or not np.isfinite(x).all() or not np.isfinite(t).all():
            raise ValueError('invalid feature shape or nonfinite value')
        arrays[feature] = (x, t)
    if not n:
        return {c['id']: np.empty((0, 3), dtype=float) for c in selected}
    cache = {}
    out = {}
    with threadpool_limits(limits=1):
        for c in selected:
            key = (c['family'], c['feature_set'], json.dumps(c['params'], sort_keys=True))
            if key not in cache:
                x, t = arrays[c['feature_set']]
                cache[key] = _fit_predict(c['family'], c['params'], x, y, t)
            out[c['id']] = _probability((1 - c['blend']) * market + c['blend'] * cache[key])
    return out
