"""Bounded, pure offline full-time score model; no archive or runtime I/O.

One league per fit. Predictions are ordered home/draw/away (draw omitted
for two-way markets), or under/over. Pushes are conditioned out, not losses.
Invalid training data raises ValueError; numerical fitting failure raises
RuntimeError. Insufficient/unsupported fits and unpriceable offers return None.
Calendar dates use the timestamps' local day.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import minimize
from scipy.sparse.linalg import spsolve
from scipy.stats import nbinom, norm, poisson

TAIL = 1e-11
MAX_CELLS = 4_000_000
MAX_BINS = 4096
MAX_SCORE = 10_000
ALIASES = {("승패", 2): "win2", ("승무패", 3): "1X2",
           ("언더오버", 2): "totals2", ("핸디캡", 2): "handicap2",
           ("핸디캡", 3): "handicap3"}
WAYS = {"win2": 2, "1X2": 3, "totals2": 2, "handicap2": 2, "handicap3": 3}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _day(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("missing kickoff/cutoff")
    return stamp.tz_localize(None).normalize() if stamp.tzinfo else stamp.normalize()


def _design(pairs, teams):
    """Intercept correction, centered home effect, attack, defensive strength."""
    rows, cols, values = [], [], []
    n = len(teams)
    for i, (home, away) in enumerate(pairs):
        for side, (own, other) in enumerate(((home, away), (away, home))):
            r = 2*i+side
            entries = [(0, 1.), (1, .5 if side == 0 else -.5)]
            if own in teams:
                entries.append((2+teams[own], 1.))
            if other in teams:
                entries.append((2+n+teams[other], -1.))
            for col, value in entries:
                rows.append(r)
                cols.append(col)
                values.append(value)
    return sparse.csr_matrix((values, (rows, cols)), shape=(2*len(pairs), 2+2*n))


def _poisson_objective(beta, x, y, weights, offset, ridge):
    # Bounds on coefficients make exp finite even during optimizer line search.
    eta = offset+x@beta
    mu = np.exp(eta)
    loss = np.dot(weights, mu-y*eta)+ridge*np.dot(beta, beta)/2
    grad = np.asarray(x.T@(weights*(mu-y))).ravel()+ridge*beta
    return float(loss), grad


def _fit_counts(x, y, weights, offset, ridge):
    args = (x, y, weights, offset, ridge)
    initial = np.zeros(x.shape[1])
    bounds = [(-20., 20.)]*len(initial)

    def hessian(beta, *unused):
        mu = np.exp(offset+x@beta)
        return x.T@x.multiply((weights*mu)[:, None])+ridge*sparse.eye(x.shape[1])

    failures = []
    for method, options in (
        ("L-BFGS-B", {"maxiter": 500, "ftol": 1e-13, "gtol": 1e-6, "maxls": 40}),
        ("trust-constr", {"maxiter": 300, "gtol": 1e-6}),
    ):
        try:
            # Both attempts start at deterministic zero with identical bounds.
            extra = {"hess": hessian} if method == "trust-constr" else {}
            result = minimize(_poisson_objective, initial.copy(), args=args, jac=True,
                              method=method, bounds=bounds, options=options, **extra)
            if result.success and np.isfinite(result.fun) and np.all(np.isfinite(result.x)):
                return result.x
            failures.append(f"{method}: success={result.success}, objective={result.fun}, "
                            f"message={result.message}")
        except (ValueError, RuntimeError, ArithmeticError, np.linalg.LinAlgError) as exc:
            failures.append(f"{method}: {type(exc).__name__}: {exc}")
    raise RuntimeError("Poisson score fit failed after both optimizers: " + "; ".join(failures))


def _marginal(mean, family, dispersion, sd):
    """Adaptive integer support; refuse allocation rather than drop a fat tail."""
    if not np.isfinite(mean) or not 0 <= mean <= MAX_SCORE:
        return None
    if family == "discrete_normal":
        distribution = norm(mean, sd)
        # Round to integers and censor negative scores at zero.
        low = max(0, int(np.floor(distribution.ppf(TAIL/4)-.5)))
        high = max(0, int(np.ceil(distribution.isf(TAIL/4)+.5)))
    else:
        distribution = (nbinom(1/dispersion, 1/(1+dispersion*mean))
                        if family == "negative_binomial" and mean > 0 else poisson(mean))
        quantiles = [distribution.ppf(TAIL/4), distribution.isf(TAIL/4)]
        if not np.all(np.isfinite(quantiles)):
            return None
        low, high = max(0, int(quantiles[0])-1), int(quantiles[1])+1
    if high-low+1 > MAX_BINS:
        return None
    scores = np.arange(low, high+1)
    if family == "discrete_normal":
        upper, lower = scores+.5, scores-.5
        # SF subtraction prevents cancellation in the right-hand tail.
        mass = np.where(scores >= mean, distribution.sf(lower)-distribution.sf(upper),
                        distribution.cdf(upper)-distribution.cdf(lower))
        if low == 0:
            mass[0] = distribution.cdf(.5)
    else:
        mass = distribution.pmf(scores)
    if not np.all(np.isfinite(mass)) or abs(float(mass.sum())-1) > TAIL:
        return None
    return scores, mass/mass.sum()


@dataclass
class FittedScoreModel:
    sport: str
    league: str
    teams: dict
    coefficients: np.ndarray
    offset: float
    family: str
    dispersion: float
    residual_sd: float
    metadata: dict

    def predict(self, row):
        """Return probabilities, or None for invalid scope/period/market/grid."""
        if row.get("sport", self.sport) != self.sport or row.get("league", self.league) != self.league:
            return None
        for key in ("period", "market_period"):
            if str(row.get(key, "")).strip().lower() not in ("", "ft", "fulltime", "full-time", "full_time"):
                return None
        home, away = row.get("home_team"), row.get("away_team")
        if not isinstance(home, str) or not isinstance(away, str) or not home or not away or home == away:
            return None
        try:
            raw_n = row.get("n_way")
            nw = int(raw_n)
            if isinstance(raw_n, bool) or float(raw_n) != nw:
                return None
        except (ValueError, TypeError, OverflowError):
            return None
        market = row.get("market")
        if not isinstance(market, str):
            return None
        market = ALIASES.get((market, nw), market)
        if WAYS.get(market) != nw:
            return None
        label = row.get("market_label", "")
        if not isinstance(label, str):
            return None
        label = label.strip()
        line = 0.
        if market in ("win2", "1X2"):
            if label:
                return None
        else:
            prefix = "U" if market == "totals2" else "H"
            if not re.fullmatch(prefix+r" [+-]?\d+(?:\.\d+)?", label):
                return None
            line = float(label[2:])
            # Quarter lines require split-stake settlement, which is unsupported.
            if not np.isfinite(line) or abs(line) > 2*MAX_SCORE or line*2 != round(line*2):
                return None
            if market == "totals2" and line < 0:
                return None
        eta = self.offset+_design([(home, away)], self.teams)@self.coefficients
        means = np.maximum(eta, 0) if self.sport == "bk" else np.exp(eta)
        marginals = [_marginal(float(m), self.family, self.dispersion, self.residual_sd) for m in means]
        if any(m is None for m in marginals):
            return None
        (hs, hp), (aws, ap) = marginals
        if len(hs)*len(aws) > MAX_CELLS:
            return None
        joint = hp[:, None]*ap[None, :]
        if market == "totals2":
            total = hs[:, None]+aws[None, :]
            p = [joint[total < line].sum(), joint[total > line].sum()]
        else:
            margin = hs[:, None]-aws[None, :]+line
            p = [joint[margin > 0].sum(), joint[margin < 0].sum()]
            if nw == 3:
                p.insert(1, joint[margin == 0].sum())
        mass = float(sum(p))
        # Conditioning on retained mass smaller than the tail bound is unsafe.
        return [float(v/mass) for v in p] if mass > 10*TAIL else None


def fit_score_model(matches, cutoff, half_life_days=180, ridge=20):
    """Fit 100+ unique events within [cutoff-730 days, cutoff), by local day.

    Decay weights are unnormalized exp(-log(2)*age/half_life_days).
    Counts minimize weighted summed Poisson NLL + ridge*beta²/2 (omitting
    the constant log-factorial); a fixed log league-mean offset normalizes
    the intercept correction. All corrections, including home, are penalized.
    Baseball uses residual method-of-moments NB2 dispersion, or Poisson when
    residual variance does not exceed the fitted mean. Basketball uses weighted
    ridge means and pooled training residual SD, with a 0.5 score-unit floor.
    Resource limits: 50,000 events, 512 teams, scores <=10,000, bounded count
    coefficients, and adaptive grids <=4M cells; exceeding a limit returns None.
    Numerical fitting failures raise RuntimeError and must abort the caller's run.
    """
    if not np.isfinite(half_life_days) or half_life_days <= 0 or not np.isfinite(ridge) or ridge <= 0:
        raise ValueError("half_life_days and ridge must be finite and positive")
    cutoff = _day(cutoff)
    lower = cutoff-pd.Timedelta(days=730)
    source = matches.to_dict("records") if isinstance(matches, pd.DataFrame) else matches
    rows = []
    for row in source:
        day = _day(row["kickoff"])
        if lower <= day < cutoff:
            rows.append((day, row))
    scopes = {(r["sport"], r["league"]) for _, r in rows}
    if len(scopes) > 1:
        raise ValueError("fit requires exactly one sport and league")
    if not rows:
        return None
    sport, league = next(iter(scopes))
    if sport not in ("sc", "bs", "bk"):
        return None  # Volleyball set scores cannot price full-time point totals.
    events = [r["event"] for _, r in rows]
    if len(set(events)) != len(events):
        raise ValueError("duplicate events must be normalized before fitting")
    if len(rows) < 100 or len(rows) > 50_000:
        return None
    rows.sort(key=lambda item: (item[0], str(item[1]["event"])))
    pairs = [(r["home_team"], r["away_team"]) for _, r in rows]
    if any(not isinstance(t, str) or not t for pair in pairs for t in pair) or any(h == a for h, a in pairs):
        raise ValueError("invalid team names")
    teams = {t: i for i, t in enumerate(sorted({t for pair in pairs for t in pair}))}
    if len(teams) > 512:
        return None
    y = np.array([(r["home_score"], r["away_score"]) for _, r in rows], dtype=float).ravel()
    if not np.all(np.isfinite(y)) or np.any(y < 0) or np.any(y != np.floor(y)):
        raise ValueError("scores must be finite nonnegative integers")
    if np.any(y > MAX_SCORE):
        return None
    ages = np.array([(cutoff-day).days for day, _ in rows])
    weights = np.repeat(np.exp(-np.log(2)*ages/half_life_days), 2)
    if weights.sum() < 1e-12:
        raise RuntimeError("Score fit has numerically negligible training weights")
    x = _design(pairs, teams)
    average = float(np.average(y, weights=weights))
    offset = average if sport == "bk" else float(np.log(max(average, 1e-6)))
    if sport == "bk":
        lhs = x.T@x.multiply(weights[:, None])+ridge*sparse.eye(x.shape[1])
        try:
            beta = spsolve(lhs.tocsc(), x.T@(weights*(y-offset)))
        except (ValueError, RuntimeError, ArithmeticError, np.linalg.LinAlgError) as exc:
            raise RuntimeError("Basketball score ridge solve failed") from exc
    else:
        beta = _fit_counts(x, y, weights, offset, ridge)
    if beta is None or not np.all(np.isfinite(beta)):
        raise RuntimeError(f"{sport} score fit returned nonfinite coefficients")
    mu = np.maximum(offset+x@beta, 0) if sport == "bk" else np.exp(offset+x@beta)
    residual2 = (y-mu)**2
    dispersion = max(0., float(np.dot(weights, residual2-mu)/max(np.dot(weights, mu**2), 1e-12))) if sport == "bs" else 0.
    if dispersion < 1e-8:
        dispersion = 0.
    sd = max(.5, float(np.sqrt(np.average(residual2, weights=weights)))) if sport == "bk" else 0.
    family = "discrete_normal" if sport == "bk" else "negative_binomial" if dispersion else "poisson"
    parameters = dict(teams=list(teams), coefficients=beta.tolist(), offset=offset,
                      dispersion=dispersion, residual_sd=sd, family=family)
    metadata = dict(model="offline-score-v1", sport=sport, league=league,
                    approximation="Independent home/away full-time scores; score-history only",
                    cutoff=cutoff.date().isoformat(), train_from=rows[0][0].date().isoformat(),
                    train_through=rows[-1][0].date().isoformat(), observed_events=len(rows),
                    half_life_days=float(half_life_days), ridge=float(ridge), training_max_days=730,
                    marginal_tail_tolerance=TAIL, max_grid_cells=MAX_CELLS,
                    max_marginal_bins=MAX_BINS, max_score=MAX_SCORE, count_coefficient_bound=20,
                    basketball_sd_floor=.5, parameters=parameters, parameters_hash=_digest(parameters))
    metadata["hash"] = _digest(metadata)
    return FittedScoreModel(sport, league, teams, beta, offset, family, dispersion, sd, metadata)
