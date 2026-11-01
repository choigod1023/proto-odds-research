"""Offline single-parameter FL-GLM; no operating or outcome-side effects.

Goto et al., arXiv:2604.17194v1, Algorithm 6 is a preprint. This
implementation fits one global exponent from prior outcomes; it is not
the per-market power conversion and has no category-specific intercepts.
"""
from __future__ import annotations

from datetime import date, timedelta
from numbers import Integral
from collections.abc import Mapping
import math

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp

LOWER = -5.0
UPPER = 10.0


def _prices(row):
    if not isinstance(row, Mapping):
        raise ValueError('row must be a mapping')
    prices = row.get('prices')
    if not isinstance(prices, (list, tuple)) or len(prices) != 3:
        raise ValueError('prices must contain exactly three outcomes')
    try:
        if any(isinstance(value, bool) for value in prices):
            raise ValueError('boolean price')
        values = np.asarray([float(value) for value in prices], dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('prices must be finite numbers greater than 1') from exc
    if not np.isfinite(values).all() or np.any(values <= 1):
        raise ValueError('prices must be finite numbers greater than 1')
    return values


def _day(value):
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('day must be an ISO date') from exc
    if result.isoformat() != value:
        raise ValueError('day must be an ISO date')
    return result


def loss_gradient_curvature(beta, log_prices, labels):
    """Summed categorical NLL, analytic first and second beta derivatives."""
    if not math.isfinite(beta):
        raise ValueError('beta must be finite')
    x = np.asarray(log_prices, dtype=float)
    y = np.asarray(labels)
    if (x.ndim != 2 or x.shape[1] != 3 or not len(x)
            or not np.isfinite(x).all() or np.any(x <= 0)
            or y.ndim != 1 or len(y) != len(x)
            or y.dtype.kind not in 'iu' or np.any((y < 0) | (y > 2))):
        raise ValueError('invalid log prices or outcomes')
    logits = -beta * x
    normalizer = logsumexp(logits, axis=1)
    probs = np.exp(logits - normalizer[:, None])
    means = np.sum(probs * x, axis=1)
    loss = np.sum(normalizer + beta * x[np.arange(len(x)), y])
    gradient = np.sum(x[np.arange(len(x)), y] - means)
    curvature = np.sum(probs * (x - means[:, None]) ** 2)
    if not np.isfinite([loss, gradient, curvature]).all():
        raise ValueError('nonfinite optimization state')
    return float(loss), float(gradient), float(curvature)


def fit(rows, reference_day):
    """Fit one beta from >=100 distinct prior rows, with seven-day embargo."""
    reference = _day(reference_day)
    cutoff = reference - timedelta(days=7)
    if not isinstance(rows, (list, tuple)) or len(rows) < 100:
        raise ValueError('need at least 100 unique training rows')
    identifiers, prices, labels = set(), [], []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError('row must be a mapping')
        identifier = row.get('id')
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise ValueError('training ids must be unique nonempty strings')
        identifiers.add(identifier)
        if _day(row.get('day')) > cutoff:
            raise ValueError('future or embargoed training row')
        label = row.get('y')
        if isinstance(label, bool) or not isinstance(label, Integral) or label not in (0, 1, 2):
            raise ValueError('training outcome must be 0, 1 or 2')
        prices.append(_prices(row))
        labels.append(int(label))
    x = np.log(np.asarray(prices))
    y = np.asarray(labels, dtype=int)
    grad_low = loss_gradient_curvature(LOWER, x, y)[1]
    grad_high = loss_gradient_curvature(UPPER, x, y)[1]
    # Convex loss: no interior root means a boundary optimum or nonidentifiability.
    if not (grad_low < 0 < grad_high):
        raise ValueError('optimum at bound or beta is not identifiable')
    beta = float(brentq(lambda b: loss_gradient_curvature(b, x, y)[1],
                        LOWER, UPPER, xtol=1e-12, rtol=1e-14, maxiter=200))
    loss, gradient, curvature = loss_gradient_curvature(beta, x, y)
    if not LOWER < beta < UPPER:
        raise ValueError('invalid interior optimum')
    if curvature <= 0:
        raise ValueError('beta is not identifiable')
    return {'beta': beta, 'reference_day': reference.isoformat(),
            'cutoff_inclusive': cutoff.isoformat(), 'training_rows': len(rows),
            'objective': loss, 'gradient': gradient, 'curvature': curvature,
            'method': 'fl_glm_global_beta_v1'}


def predict(state, row):
    """Predict from prices only: y, hg and ag in the current row are ignored."""
    if not isinstance(state, Mapping) or isinstance(state.get('beta'), bool):
        raise ValueError('invalid fitted state')
    try:
        beta = float(state['beta'])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError('invalid fitted beta') from exc
    if not math.isfinite(beta) or not LOWER < beta < UPPER:
        raise ValueError('invalid fitted beta')
    logits = -beta * np.log(_prices(row))
    probabilities = np.exp(logits - logsumexp(logits))
    if not np.isfinite(probabilities).all() or np.any(probabilities <= 0):
        raise ValueError('probability underflow for input prices')
    return probabilities.tolist(), {'beta': beta}
