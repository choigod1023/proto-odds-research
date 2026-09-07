from copy import deepcopy
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import OptimizeResult, check_grad
from scipy.stats import poisson

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
import score_replay_model as model

CUTOFF = pd.Timestamp("2026-09-07")


def history(sport="sc", n=160, seed=81):
    rng = np.random.default_rng(seed)
    if sport == "bk":
        scores = np.maximum(0, np.rint(rng.normal([106, 100], 12, (n, 2)))).astype(int)
    elif sport == "bs":
        scores = rng.negative_binomial(2, 2/(2+np.array([5., 4.])), (n, 2))
    else:
        scores = rng.poisson([1.8, 1.3], (n, 2))
    return [dict(kickoff=CUTOFF-pd.Timedelta(days=n-i)+pd.Timedelta(hours=14),
                 sport=sport, league="test", home_team=f"T{i%8}", away_team=f"T{(i+3)%8}",
                 home_score=int(h), away_score=int(a), event=f"event-{i}")
            for i, (h, a) in enumerate(scores)]


def offer(market="1X2", line="", **changes):
    return dict(market=market, n_way=model.WAYS.get(market, 2), market_label=line,
                home_team="T0", away_team="T3", **changes)


@pytest.fixture(scope="module")
def fitted():
    return model.fit_score_model(history(), CUTOFF)


def test_causality_same_day_future_mutation_and_order():
    rows = history()
    expected = model.fit_score_model(rows, CUTOFF)
    extra = [{**rows[0], "kickoff": CUTOFF+pd.Timedelta(hours=h), "event": f"future-{h}"}
             for h in (0, 6, 30)]
    changed = deepcopy(extra)
    for row in changed:
        row.update(home_score=float("nan"), away_score=10**100, sport="vl", league="other",
                   home_team="future-only home", away_team="future-only away",
                   result="changed", actual_score=[999, 888], winner=-1)
    for additions in (extra, changed):
        actual = model.fit_score_model(list(reversed(rows+additions)), CUTOFF+pd.Timedelta(hours=18))
        assert actual.metadata == expected.metadata
        assert actual.predict(offer()) == expected.predict(offer())
        assert "future-only home" not in actual.teams


def test_alternative_outcome_columns_are_never_training_inputs(fitted):
    rows = history()
    for row in rows:
        row.update(actual_score=[float("nan"), float("inf")], winner=999, result="wrong")
    actual = model.fit_score_model(rows, CUTOFF)
    assert actual.metadata == fitted.metadata
    assert actual.predict({**offer(), "actual_score": [999, 888], "winner": -1}) == fitted.predict(offer())


def test_window_minimum_scope_and_duplicates():
    assert model.fit_score_model(history(n=99), CUTOFF) is None
    assert model.fit_score_model(history(n=100), CUTOFF) is not None
    rows = history(n=100)
    rows[0]["kickoff"] = CUTOFF-pd.Timedelta(days=730)
    assert model.fit_score_model(rows, CUTOFF).metadata["observed_events"] == 100
    rows[0]["kickoff"] -= pd.Timedelta(days=1)
    assert model.fit_score_model(rows, CUTOFF) is None
    for key, value in (("league", "other"), ("sport", "bs")):
        rows = history()
        rows[0][key] = value
        with pytest.raises(ValueError, match="one sport and league"):
            model.fit_score_model(rows, CUTOFF)
    rows = history()
    with pytest.raises(ValueError, match="duplicate"):
        model.fit_score_model(rows+[rows[0]], CUTOFF)


def test_unknown_teams_zero_effects_and_scope(fitted):
    x = model._design([("new A", "new B")], fitted.teams).toarray()
    assert np.all(x[:, 2:] == 0)
    a = offer()
    a.update(home_team="new A", away_team="new B")
    p = fitted.predict(a)
    assert p is not None and sum(p) == pytest.approx(1)
    assert fitted.predict({**a, "home_team": "new C", "away_team": "new D"}) == p
    assert fitted.predict({**a, "league": "other"}) is None
    assert fitted.predict({**a, "sport": "bs"}) is None


@pytest.mark.parametrize("market,label", [("win2", ""), ("1X2", ""), ("totals2", "U 2.5"),
    ("totals2", "U 3"), ("handicap2", "H -1"), ("handicap3", "H -1"), ("handicap3", "H -1.5")])
def test_probabilities_and_offer_labels_ignored(fitted, market, label):
    row = offer(market, label)
    p = fitted.predict(row)
    assert len(p) == row["n_way"] and sum(p) == pytest.approx(1)
    assert np.all(np.isfinite(p)) and np.all(np.array(p) >= 0)
    assert fitted.predict({**row, "winner": 900, "result": "wrong", "home_score": 999}) == p


def test_ties_pushes_and_consistent_joint(fitted):
    h, d, a = fitted.predict(offer())
    assert d > 0
    assert fitted.predict(offer("win2")) == pytest.approx([h/(h+a), a/(h+a)])
    assert fitted.predict(offer("handicap3", "H 0")) == pytest.approx([h, d, a])
    assert fitted.predict(offer("handicap2", "H 0")) == pytest.approx([h/(h+a), a/(h+a)])
    hw, hd, ha = fitted.predict(offer("handicap3", "H -1"))
    assert hd > 0
    assert fitted.predict(offer("handicap2", "H -1")) == pytest.approx([hw/(hw+ha), ha/(hw+ha)])
    under2 = fitted.predict(offer("totals2", "U 1.5"))[0]
    over2 = fitted.predict(offer("totals2", "U 2.5"))[1]
    assert fitted.predict(offer("totals2", "U 2")) == pytest.approx([under2/(under2+over2), over2/(under2+over2)])


def test_monotonic_lines(fitted):
    unders = [fitted.predict(offer("totals2", f"U {line}"))[0] for line in np.arange(.5, 10, .5)]
    homes = [fitted.predict(offer("handicap2", f"H {line}"))[0] for line in np.arange(-5, 5, .5)]
    assert np.all(np.diff(unders) >= -1e-12)
    assert np.all(np.diff(homes) >= -1e-12)


@pytest.mark.parametrize("change", [{"market": "1H win2"}, {"period": "1H"}, {"market_period": "Q1"},
    {"market_label": "first half"}, {"n_way": 2.5}, {"n_way": float("inf")}, {"market": "승⑤패"},
    {"market": "totals2", "n_way": 2, "market_label": "U NaN"},
    {"market": "totals2", "n_way": 2, "market_label": "U -2.5"},
    {"market": "totals2", "n_way": 2, "market_label": "U 2.25"},
    {"market": "handicap2", "n_way": 2, "market_label": "H -1.5 extra"},
    {"market": "handicap2", "n_way": 2, "market_label": "U 2.5"}, {"home_team": ""}])
def test_malformed_and_unsupported(fitted, change):
    assert fitted.predict({**offer(), **change}) is None


@pytest.mark.parametrize("sport,family", [("sc", "poisson"), ("bs", "negative_binomial"), ("bk", "discrete_normal")])
def test_sport_families_and_metadata(sport, family):
    rows = history(sport)
    before = deepcopy(rows)
    fitted = model.fit_score_model(pd.DataFrame(rows), CUTOFF)
    assert rows == before
    assert fitted.family == family
    assert sum(fitted.predict(offer("win2"))) == pytest.approx(1)
    meta = fitted.metadata
    assert "Independent" in meta["approximation"]
    assert meta["observed_events"] == 160 and meta["train_through"] == "2026-09-06"
    assert meta["parameters_hash"] == model._digest(meta["parameters"])
    assert len(meta["hash"]) == 64
    if sport == "bk":
        x = model._design([(r["home_team"], r["away_team"]) for r in rows], fitted.teams)
        residuals = np.array([(r["home_score"], r["away_score"]) for r in rows]).ravel()-(fitted.offset+x@fitted.coefficients)
        w = np.repeat(2.**(-np.arange(160, 0, -1)/180), 2)
        assert fitted.residual_sd == pytest.approx(np.sqrt(np.average(residuals**2, weights=w)))


def test_volleyball_and_baseball_poisson_fallback():
    assert model.fit_score_model(history("vl"), CUTOFF) is None
    rows = history("bs")
    for r in rows:
        r.update(home_score=4, away_score=4)
    fitted = model.fit_score_model(rows, CUTOFF)
    assert fitted.family == "poisson" and fitted.dispersion == 0


@pytest.mark.parametrize("sport,score,line", [("sc", 15, 30.5), ("bs", 35, 70.5), ("bk", 1000, 2000.5)])
def test_large_scores_finite_and_adaptive(sport, score, line):
    rows = history(sport)
    for r in rows:
        r.update(home_score=score, away_score=score)
    fitted = model.fit_score_model(rows, CUTOFF)
    p = fitted.predict(offer("totals2", f"U {line}"))
    assert p is not None and np.all(np.isfinite(p)) and sum(p) == pytest.approx(1)
    assert .2 < p[0] < .9
    if sport != "bk":
        assert p[0] == pytest.approx(poisson.cdf(int(line), 2*score), abs=1e-8)


def test_tail_accuracy_and_bounded_refusal():
    for mean, family, alpha, sd in [(15, "poisson", 0, 0), (35, "negative_binomial", .8, 0),
                                    (140, "discrete_normal", 0, 25)]:
        scores, mass = model._marginal(mean, family, alpha, sd)
        assert len(scores) > 20 and mass.sum() == pytest.approx(1)
        assert np.dot(scores, mass) == pytest.approx(mean, abs=1e-6)
    assert model._marginal(10_000, "negative_binomial", 20, 0) is None
    rows = history()
    rows[0]["home_score"] = 10**100
    assert model.fit_score_model(rows, CUTOFF) is None


def test_zero_scores_and_invalid_inputs():
    rows = history()
    for r in rows:
        r.update(home_score=0, away_score=0)
    fitted = model.fit_score_model(rows, CUTOFF)
    assert np.all(np.isfinite(fitted.predict(offer())))
    assert fitted.predict(offer("totals2", "U 0")) == pytest.approx([0, 1])
    for ridge in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            model.fit_score_model(rows, CUTOFF, ridge=ridge)
    rows[0]["home_score"] = -1
    with pytest.raises(ValueError, match="scores"):
        model.fit_score_model(rows, CUTOFF)


def test_poisson_summed_objective_analytic_gradient():
    x = model._design([("a", "b"), ("b", "a")], {"a": 0, "b": 1})
    y, w = np.array([2., 1., 4., 0.]), np.array([1., 1., .5, .5])
    beta, offset, ridge = np.arange(6)*.01, np.log(2), 20
    def fun(b):
        return model._poisson_objective(b, x, y, w, offset, ridge)
    assert check_grad(lambda b: fun(b)[0], lambda b: fun(b)[1], beta) < 1e-5
    eta = offset+x@beta
    assert fun(beta)[0] == pytest.approx(np.sum(w*(np.exp(eta)-y*eta))+ridge*np.sum(beta**2)/2)


def test_korean_market_aliases(fitted):
    for (market, nw), canonical in model.ALIASES.items():
        line = "U 2.5" if canonical == "totals2" else "H -1" if canonical.startswith("handicap") else ""
        assert fitted.predict({**offer(canonical, line), "market": market, "n_way": nw}) == fitted.predict(offer(canonical, line))


def test_trust_retry_recovers_failed_first_optimizer(monkeypatch, fitted):
    real_minimize = model.minimize
    methods = []
    starts, bounds = [], []

    def fail_first(*args, **kwargs):
        methods.append(kwargs["method"])
        starts.append(args[1].copy())
        bounds.append(kwargs["bounds"])
        result = real_minimize(*args, **kwargs)
        if len(methods) == 1:
            result.success = False
            result.x[:] = 19.  # Failed iterate must never initialize the retry.
        return result

    monkeypatch.setattr(model, "minimize", fail_first)
    retried = model.fit_score_model(history(), CUTOFF)
    assert methods == ["L-BFGS-B", "trust-constr"]
    assert all(np.all(start == 0) for start in starts)
    assert bounds[0] == bounds[1] == [(-20., 20.)]*len(starts[0])
    assert retried is not None
    assert retried.predict(offer()) == pytest.approx(fitted.predict(offer()), abs=1e-6)


@pytest.mark.parametrize("failure", ["unsuccessful", "nonfinite_objective", "nonfinite_coefficients", "exception"])
def test_both_optimizer_failures_raise_diagnostic(monkeypatch, failure):
    methods = []

    def fail(fun, start, **kwargs):
        methods.append(kwargs["method"])
        assert np.all(start == 0)
        if failure == "exception":
            raise FloatingPointError("forced numerical failure")
        return OptimizeResult(success=failure != "unsuccessful", message="forced failure",
                              fun=np.nan if failure == "nonfinite_objective" else 0.,
                              x=np.full_like(start, np.nan if failure == "nonfinite_coefficients" else 19.))

    monkeypatch.setattr(model, "minimize", fail)
    with pytest.raises(RuntimeError, match="L-BFGS-B.*trust-constr"):
        model.fit_score_model(history(), CUTOFF)
    assert methods == ["L-BFGS-B", "trust-constr"]


@pytest.mark.parametrize("failure", ["nonfinite", "exception"])
def test_basketball_numerical_failure_raises(monkeypatch, failure):
    def fail(lhs, rhs):
        if failure == "exception":
            raise np.linalg.LinAlgError("forced numerical failure")
        return np.full_like(rhs, np.nan)

    monkeypatch.setattr(model, "spsolve", fail)
    with pytest.raises(RuntimeError, match="ridge solve failed|nonfinite coefficients"):
        model.fit_score_model(history("bk"), CUTOFF)


def test_numerically_negligible_training_weights_raise():
    with pytest.raises(RuntimeError, match="training weights"):
        model.fit_score_model(history(), CUTOFF, half_life_days=.001)


def test_recency_weights_and_ridge_change_predictions():
    rows = history(n=200)
    for i, row in enumerate(rows):
        row.update(home_score=1 if i < 100 else 6, away_score=1 if i < 100 else 4)
    old_weight = model.fit_score_model(rows, CUTOFF, half_life_days=1000)
    recent_weight = model.fit_score_model(rows, CUTOFF, half_life_days=30)
    assert recent_weight.predict(offer("totals2", "U 7.5"))[1] > old_weight.predict(offer("totals2", "U 7.5"))[1]
    shrunk = model.fit_score_model(rows, CUTOFF, ridge=1e6)
    assert np.linalg.norm(shrunk.coefficients) < np.linalg.norm(old_weight.coefficients)
    assert shrunk.metadata["parameters_hash"] != old_weight.metadata["parameters_hash"]


def test_asymmetric_extreme_scores_and_gradient_finite():
    rows = history()
    for row in rows:
        row.update(home_score=1000, away_score=0)
    fitted = model.fit_score_model(rows, CUTOFF)
    assert fitted is not None
    p = fitted.predict(offer())
    assert p is not None and np.all(np.isfinite(p)) and sum(p) == pytest.approx(1)
    x = model._design([("a", "b")], {"a": 0, "b": 1})
    value, grad = model._poisson_objective(np.full(6, 20.), x, np.array([1000., 0.]),
                                         np.ones(2), np.log(500), 20)
    assert np.isfinite(value) and np.all(np.isfinite(grad))


def test_local_timezone_day_cutoff():
    rows = history()
    expected = model.fit_score_model(rows, CUTOFF)
    for row in rows:
        row["kickoff"] = row["kickoff"].tz_localize("Asia/Seoul")
    rows.append({**rows[0], "event": "same-day", "kickoff": CUTOFF.tz_localize("Asia/Seoul")})
    actual = model.fit_score_model(rows, CUTOFF.tz_localize("Asia/Seoul"))
    assert actual.metadata == expected.metadata
