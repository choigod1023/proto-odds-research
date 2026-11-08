"""Research-only, date-gated residual neighbours and probability pooling.

The seven-day lag is a conservative convention, not evidence of result receipt.
This module neither reads data nor evaluates or changes the operating model.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from itertools import product
import math

import numpy as np


def configurations():
    out = []
    for space, k, prior, weight, half_life, blend in product(
        ('market', 'combined', 'form'), (10, 20, 30, 45, 60, 90, 120),
        (10, 30, 60, 120, 240), ('uniform', 'inverse'), (None, 30, 90, 180),
        (.25, .5, .75, 1.0),
    ):
        ident = f'residual:{space}:k={k}:prior={prior}:weight={weight}:half={half_life}:blend={blend}'
        out.append(dict(id=ident, family='residual', space=space, k=k, prior=prior,
                        weight=weight, half_life=half_life, blend=blend))
    return out


def pooling_configs():
    out = []
    for weight, temperature, home_bias, draw_bias in product(
        (0, .1, .25, .5, .75, 1), (.75, 1, 1.25, 1.5),
        (-.15, -.1, -.05, 0, .05, .1, .15), (-.1, 0, .1),
    ):
        ident = f'pool:w={weight}:temp={temperature}:home={home_bias}:draw={draw_bias}'
        out.append(dict(id=ident, family='pool', weight=weight, temperature=temperature,
                        home_bias=home_bias, draw_bias=draw_bias))
    return out


def _matrix(value, columns, name, rows=None, probabilities=False, check_values=True):
    arr = np.asarray(value, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != columns or (rows is not None and len(arr) != rows):
        raise ValueError(f'{name} must have shape ({rows if rows is not None else "n"}, {columns})')
    if check_values and not np.isfinite(arr).all():
        raise ValueError(f'{name} must be finite')
    if check_values and probabilities and ((arr < 0).any() or (arr > 1).any() or not np.allclose(arr.sum(axis=1), 1, rtol=0, atol=1e-8)):
        raise ValueError(f'{name} must contain probability distributions')
    return arr


def _dates(values, expected, name):
    if len(values) != expected:
        raise ValueError(f'{name} length mismatch')
    result = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f'{name} must contain ISO date strings')
        try:
            parsed = date.fromisoformat(item)
        except ValueError as exc:
            raise ValueError(f'{name} must contain ISO date strings') from exc
        if parsed.isoformat() != item:
            raise ValueError(f'{name} must use YYYY-MM-DD')
        result.append(parsed)
    return result


def _config_list(configs, family):
    configs = list(configs)
    ids = []
    for c in configs:
        if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id'] or c.get('family') != family:
            raise ValueError(f'invalid {family} configuration')
        ids.append(c['id'])
    if len(ids) != len(set(ids)):
        raise ValueError('configuration IDs must be unique')
    return configs


def _residual_config(c):
    try:
        space, k, prior, weight, half, blend = (c[x] for x in ('space', 'k', 'prior', 'weight', 'half_life', 'blend'))
        if space not in ('market', 'combined', 'form') or weight not in ('uniform', 'inverse'):
            raise ValueError('unknown residual space or weight')
        if isinstance(k, bool) or not isinstance(k, (int, np.integer)) or k <= 0:
            raise ValueError('k must be a positive integer')
        if not math.isfinite(prior) or prior < 0 or not math.isfinite(blend) or not 0 <= blend <= 1:
            raise ValueError('invalid residual prior or blend')
        if half is not None and (not math.isfinite(half) or half <= 0):
            raise ValueError('invalid half life')
    except (KeyError, TypeError) as exc:
        raise ValueError('invalid residual configuration') from exc
    return space, int(k), float(prior), weight, half, float(blend)


def predict_all(train_market, train_dc, train_form, train_y, train_days, train_ids,
                target_market, target_dc, target_form, target_days, configs=None):
    """Return {config ID: (target_count, 3) array}; insufficient history uses market.

    Label classes and columns are home, draw, away. Form-space scaling is fitted
    to each target's whole eligible training bank, never to target/future rows.
    Train IDs are unique deterministic tie-breakers, not target IDs.
    """
    tm = _matrix(train_market, 3, 'train_market', probabilities=True, check_values=False)
    n = len(tm)
    td = _matrix(train_dc, 3, 'train_dc', n, probabilities=True, check_values=False)
    tf = _matrix(train_form, 12, 'train_form', n, check_values=False)
    qm = _matrix(target_market, 3, 'target_market', probabilities=True)
    m = len(qm)
    qd = _matrix(target_dc, 3, 'target_dc', m, probabilities=True)
    qf = _matrix(target_form, 12, 'target_form', m)
    y = np.asarray(train_y)
    if y.shape != (n,):
        raise ValueError('train_y must be a vector with classes 0, 1, 2')
    days = _dates(train_days, n, 'train_days')
    target_dates = _dates(target_days, m, 'target_days')
    ids = list(train_ids)
    if len(ids) != n or any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != n:
        raise ValueError('train_ids must be unique nonempty strings')
    cfg = _config_list(configurations() if configs is None else configs, 'residual')
    parsed = [_residual_config(c) for c in cfg]
    results = np.broadcast_to(qm, (len(cfg), m, 3)).copy()
    if not cfg or not m or not n:
        return {c['id']: results[i] for i, c in enumerate(cfg)}

    groups = defaultdict(list)
    for i, (space, k, prior, weight, half, blend) in enumerate(parsed):
        groups[(space, k, weight, half)].append((i, prior, blend))
    min_k = min(c[1] for c in parsed)
    train_ord = np.fromiter((d.toordinal() for d in days), dtype=np.int64, count=n)
    id_array = np.asarray(ids, dtype=str)
    for j, target_day in enumerate(target_dates):
        target_ord = target_day.toordinal()
        eligible = np.flatnonzero(train_ord <= target_ord - 7)
        if len(eligible) < min_k:
            continue
        # Future rows are outside this target's bank, including for validation.
        market = _matrix(tm[eligible], 3, 'eligible train_market', probabilities=True)
        _matrix(td[eligible], 3, 'eligible train_dc', probabilities=True)
        _matrix(tf[eligible], 12, 'eligible train_form')
        try:
            labels = np.asarray(y[eligible], dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError('eligible train_y must have classes 0, 1, 2') from exc
        if not np.isfinite(labels).all() or np.any((labels < 0) | (labels > 2) | (labels != np.floor(labels))):
            raise ValueError('eligible train_y must have classes 0, 1, 2')
        residual = np.eye(3, dtype=np.float64)[labels.astype(np.int64)] - market
        age = target_ord - train_ord[eligible]
        bank = {
            'market': (market, qm[j]),
            'combined': (np.concatenate((market, td[eligible]), axis=1), np.concatenate((qm[j], qd[j]))),
            'form': (np.concatenate((market, td[eligible], tf[eligible]), axis=1), np.concatenate((qm[j], qd[j], qf[j]))),
        }
        orders = {}
        for space in {key[0] for key in groups}:
            x, target = bank[space]
            if space == 'form':
                mean = x.mean(axis=0)
                scale = x.std(axis=0)
                scale[scale == 0] = 1.0
                x, target = (x - mean) / scale, (target - mean) / scale
            dist = np.linalg.norm(x - target, axis=1)
            order = np.lexsort((id_array[eligible], dist))
            orders[space] = (order, dist)
        for (space, k, weight, half), members in groups.items():
            if len(eligible) < k:
                continue
            order, distances = orders[space]
            idx = order[:k]
            w = np.ones(k) if weight == 'uniform' else 1.0 / (distances[idx] + .05)
            w = w * (k / w.sum())
            if half is not None:
                w *= np.exp2(-age[idx] / float(half))
            weighted_residual = w @ residual[idx]
            indices = np.array([z[0] for z in members], dtype=int)
            priors = np.array([z[1] for z in members], dtype=float)
            blends = np.array([z[2] for z in members], dtype=float)
            denom = w.sum() + priors
            # With a zero prior and completely underflowed recency, no information
            # remains; retain the market rather than creating NaNs.
            factors = np.divide(blends, denom, out=np.zeros_like(blends), where=denom > 0)
            p = np.maximum(qm[j] + factors[:, None] * weighted_residual, 1e-6)
            results[indices, j] = p / p.sum(axis=1, keepdims=True)
    return {c['id']: results[i] for i, c in enumerate(cfg)}


def predict_pool_all(target_market, target_dc, configs=None):
    """Return pooled distributions without receiving labels or fitting parameters."""
    market = _matrix(target_market, 3, 'target_market', probabilities=True)
    dc = _matrix(target_dc, 3, 'target_dc', len(market), probabilities=True)
    cfg = _config_list(pooling_configs() if configs is None else configs, 'pool')
    out = {}
    for c in cfg:
        try:
            w, temp, bh, bd = (float(c[x]) for x in ('weight', 'temperature', 'home_bias', 'draw_bias'))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('invalid pooling configuration') from exc
        if not all(math.isfinite(v) for v in (w, temp, bh, bd)) or not 0 <= w <= 1 or temp <= 0:
            raise ValueError('invalid pooling configuration')
        mixed = (1 - w) * market + w * dc
        with np.errstate(divide='ignore'):
            logits = np.log(mixed) / temp + np.array([bh, bd, -bh - bd])
        logits -= np.max(logits, axis=1, keepdims=True)
        probs = np.exp(logits)
        out[c['id']] = probs / probs.sum(axis=1, keepdims=True)
    return out
