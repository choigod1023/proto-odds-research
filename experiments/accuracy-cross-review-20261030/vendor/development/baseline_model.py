"""Research Laplace assumed-density filters; not an exact paper reproduction."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

import numpy as np
from numpy.polynomial.hermite import hermgauss
from scipy.special import expit, logsumexp, softmax
from scipy.stats import skellam

CONFIGS = [('score-static', 'score', 0.), ('score-dynamic', 'score', .0003),
           ('outcome-static', 'outcome', 0.), ('outcome-dynamic', 'outcome', .002)]
NODES, WEIGHTS = hermgauss(7)
WEIGHTS = WEIGHTS / math.sqrt(math.pi)


def timestamp(s):
    d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('aware timestamp required')
    return d.astimezone(timezone.utc)


def due(row):
    return datetime.fromisoformat(row['date']).replace(tzinfo=timezone.utc) + timedelta(days=4)


def quadrature(mean, cov):
    vals, vecs = np.linalg.eigh((cov + cov.T)/2)
    if min(vals) < -1e-7:
        raise ValueError('negative predictive covariance')
    root = vecs @ np.diag(np.sqrt(np.maximum(vals, 0)))
    if len(mean) == 1:
        return mean[None, :] + math.sqrt(2)*NODES[:, None] @ root.T, WEIGHTS
    grid = np.array([(a, b) for a in NODES for b in NODES])
    return mean[None, :] + math.sqrt(2)*grid @ root.T, np.outer(WEIGHTS, WEIGHTS).ravel()


class State:
    def __init__(self, league, kind='score', q=.0003):
        self.league, self.kind, self.q = league, kind, q
        self.soccer = league.startswith('K리그')
        self.index, self.clock, self.updates = {}, None, 0
        self.mean = np.array([math.log(1.4 if self.soccer else 4.3)]*2) if kind == 'score' else np.array([.15, -.4])
        self.cov = np.diag([.03, .03] if kind == 'score' else [.15, .25])

    def clone(self):
        return deepcopy(self)

    def ensure(self, team):
        if team in self.index:
            return
        width = 2 if self.kind == 'score' else 1
        i = len(self.mean)
        self.index[team] = i
        self.mean = np.r_[self.mean, np.zeros(width)]
        c = np.zeros((i+width, i+width))
        c[:i, :i] = self.cov
        c[i:, i:] = np.eye(width)*(.1 if self.kind == 'score' else .5)
        self.cov = c

    def advance(self, at):
        if self.clock is None:
            self.clock = at
            return
        if at < self.clock:
            raise ValueError('state cannot travel backwards')
        days = (at-self.clock).total_seconds()/86400
        phi = math.exp(-days/730)
        scale = np.ones(len(self.mean))
        scale[2:] = phi
        self.mean[2:] *= phi
        self.cov = scale[:, None]*self.cov*scale[None, :]
        ix = np.arange(2, len(self.mean))
        self.cov[ix, ix] += self.q*min(days, 365)
        years = at.year - self.clock.year
        if years:
            s = .7**years
            self.mean[2:] *= s
            scale[2:] = s
            self.cov = scale[:, None]*self.cov*scale[None, :]
            self.cov[ix, ix] += years*(.05 if self.kind == 'score' else .25)
        self.clock = at

    def design(self, home, away):
        if home == away:
            raise ValueError('same team')
        self.ensure(home)
        self.ensure(away)
        h, a = self.index[home], self.index[away]
        width = 2 if self.kind == 'score' or self.soccer else 1
        x = np.zeros((width, len(self.mean)))
        if self.kind == 'score':
            x[0, [0, h, a+1]] = 1
            x[1, [1, a, h+1]] = 1
        else:
            x[0, [0, h, a]] = [1, 1, -1]
            if self.soccer:
                x[1, 1] = 1
        return x

    def likelihood(self, z, y):
        if self.kind == 'score':
            rates = np.exp(z)
            return float(np.sum(rates-y*z)), rates-y, np.diag(rates)
        if self.soccer:
            j = np.array([[.5, 0], [0, 1], [-.5, 0]])
            logits = j@z
            p = softmax(logits)
            return float(logsumexp(logits)-y@logits), j.T@(p-y), j.T@(np.diag(p)-np.outer(p,p))@j
        p = expit(z[0])
        return float(np.logaddexp(0, z[0])-y[0]*z[0]), np.array([p-y[0]]), np.array([[p*(1-p)]])

    def update(self, row, at):
        if at < due(row):
            raise ValueError('result availability embargo')
        if row['league'] != self.league or any(type(row[k]) is not int or row[k] < 0 for k in ('hg', 'ag')):
            raise ValueError('invalid league/score')
        self.advance(at)
        x = self.design(row['home'], row['away'])
        z0 = x@self.mean
        s = x@self.cov@x.T + np.eye(len(x))*1e-10
        precision = np.linalg.inv(s)
        if self.kind == 'score':
            y = np.array([row['hg'], row['ag']])
        elif self.soccer:
            y = np.eye(3)[0 if row['hg']>row['ag'] else 1 if row['hg']==row['ag'] else 2]
        else:
            if row['hg'] == row['ag']:
                return  # Binary decision likelihood has no draw category.
            y = np.array([int(row['hg'] > row['ag'])])
        def obj(z):
            loss, g, h = self.likelihood(z, y)
            d = z-z0
            return loss+.5*d@precision@d, g+precision@d, h+precision
        z = z0.copy()
        for _ in range(50):
            value, gradient, hessian = obj(z)
            if np.max(np.abs(gradient)) < 1e-7:
                break
            step = np.linalg.solve(hessian, gradient)
            alpha = 1.
            while alpha > 2**-20:
                new = z-alpha*step
                if obj(new)[0] <= value-1e-4*alpha*gradient@step + 1e-12:
                    break
                alpha *= .5
            z = z-alpha*step
        if np.max(np.abs(obj(z)[1])) > 1e-5:
            raise RuntimeError('Laplace mode did not converge')
        _, _, observed_h = self.likelihood(z, y)
        v = np.linalg.inv(precision+observed_h)
        gain = self.cov@x.T@precision
        self.mean += gain@(z-z0)
        self.cov += gain@(v-s)@gain.T
        self.cov = (self.cov+self.cov.T)/2
        if not np.all(np.isfinite(self.mean)) or not np.all(np.isfinite(self.cov)) or np.min(np.diag(self.cov)) < -1e-8:
            raise RuntimeError('invalid Gaussian state')
        self.updates += 1

    def predict(self, home, away, at):
        # A read must not age the live filter or change subsequent forecasts.
        temporary = self.clone()
        return temporary._predict_at(home, away, at)

    def _predict_at(self, home, away, at):
        self.advance(at)
        x = self.design(home, away)
        mean, cov = x@self.mean, x@self.cov@x.T
        nodes, w = quadrature(mean, cov)
        if self.kind == 'score':
            rates = np.exp(nodes)
            left = skellam.cdf(-1, rates[:,0], rates[:,1])
            equal = skellam.pmf(0, rates[:,0], rates[:,1])
            p = w@np.column_stack([1-left-equal, equal, left])
            if not self.soccer:
                p = p[[0,2]]/p[[0,2]].sum()
        elif self.soccer:
            p = w@softmax(np.column_stack([nodes[:,0]/2, nodes[:,1], -nodes[:,0]/2]), axis=1)
        else:
            h = w@expit(nodes[:,0])
            p = np.array([h, 1-h])
        p = np.maximum(p, 1e-12)
        p /= p.sum()
        if not np.all(np.isfinite(p)):
            raise RuntimeError('invalid prediction')
        return p.tolist(), {'linear_mean': mean.tolist(), 'linear_covariance': cov.tolist(), 'previous_updates': self.updates}

    def export(self):
        return {'league': self.league, 'kind': self.kind, 'q': self.q, 'index': self.index,
            'mean': self.mean.tolist(), 'covariance': self.cov.tolist(),
            'clock': self.clock.isoformat() if self.clock else None, 'updates': self.updates}

    @classmethod
    def restore(cls, obj):
        s = cls(obj['league'], obj['kind'], obj['q'])
        s.index = dict(obj['index'])
        s.mean = np.array(obj['mean'],dtype=float)
        s.cov = np.array(obj['covariance'],dtype=float)
        s.clock = timestamp(obj['clock']) if obj['clock'] else None
        s.updates = obj['updates']
        return s

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.export(), sort_keys=True).encode()).hexdigest()


class Engine:
    def __init__(self, history, league, kind, q):
        self.history = sorted([r for r in history if r['league']==league], key=lambda r: (due(r), r['home'], r['away']))
        self.state = State(league, kind, q)
        self.cursor = 0

    def advance(self, at, online=True):
        if online:
            while self.cursor < len(self.history) and due(self.history[self.cursor]) <= at:
                r = self.history[self.cursor]
                self.state.update(r, due(r))
                self.cursor += 1

    def predict(self, home, away, at, online=True):
        self.advance(at, online)
        return self.state.predict(home, away, at)
