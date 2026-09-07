from copy import deepcopy
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
import nested_team_style_validation as nested
from team_style_experiment import fit, predict
import team_style_experiment as base_model
from scipy.optimize import OptimizeResult


def protocol():
    p = json.loads((Path(__file__).resolve().parents[1]/"experiments/nested-team-style-20260907/protocol.json").read_text(encoding="utf-8"))
    p.update(minimum_training_markets=5, minimum_inner_markets=8, minimum_inner_events=8,
             minimum_inner_weeks=3, minimum_inner_quarters=3, minimum_quarter_validation_markets=2,
             bootstrap_repeats=30, ridge_grid=[100])
    return p


def records():
    return [{"sport": "bs", "league": "NPB", "market": "승패", "n_way": 2,
             "day": f"{year}-{month:02d}-{day:02d}", "kickoff": f"{year}-{month:02d}-{day:02d}T17:00:00",
             "offer": f"{year}-{month}-{day}", "event": f"{year}-{month}-{day}",
             "winner": (month+day)%2, "q": [.55, .45], "odds": [1.7, 2.],
             "features": {"home_gf10": month/12, "away_gf10": day/28}}
            for year in (2023, 2024, 2025) for month in (1, 4, 7, 10) for day in (1, 10, 20)]


def config(name, count, ridge, loss):
    return {"id": name, "columns": ["x"]*count, "ridge": ridge, "log_loss": loss}


def test_one_se_uses_best_se_and_prefers_no_estimated_coefficients():
    base = config("baseline", 0, None, .70)
    intercept = config("control@100", 0, 100, .70)
    full = config("full@100", 50, 100, .69)
    minimum, simple = nested.choose([full, intercept, base], {full["id"]: .02})
    assert minimum == full and simple == base


def test_one_se_boundary_and_stronger_penalty_tie():
    a, b, best = config("a", 2, 100, .71), config("b", 2, 1000, .71), config("best", 5, 100, .70)
    _, simple = nested.choose([a, b, best], {"best": .01})
    assert simple == b
    _, simple = nested.choose([a, b, best], {"best": .009})
    assert simple == best


def test_block_se_constant_loss_zero_and_reproducible():
    days = ["2024-01-01", "2024-01-02", "2024-12-24"]
    assert nested.block_standard_error(days, [1, 1, 1], 100, 4, 42) == 0
    a = nested.block_standard_error(days, [1, 2, 3], 100, 4, 42)
    assert a == nested.block_standard_error(days, [1, 2, 3], 100, 4, 42) and a > 0


def test_configurations_never_inspect_post_cutoff_features_or_labels():
    rs = records(); p = protocol()
    before = nested.inner_selection(rs, p)
    changed = deepcopy(rs)
    for r in changed:
        if r["day"] >= p["selection_cutoff_exclusive"]:
            r["winner"] = 99
            r["features"] = None
    after = nested.inner_selection(changed, p)
    removed = nested.inner_selection([r for r in rs if r["day"] < p["selection_cutoff_exclusive"]], p)
    assert before == after == removed
    assert before["status"] == "selected_pre2025"
    assert before["folds"][-1]["end_exclusive"] == "2024-12-25"


def test_scope_and_sparse_quarters():
    rs = records(); p = protocol()
    with pytest.raises(ValueError):
        nested.inner_selection(rs+[{**rs[0], "league": "MLB"}], p)
    p["minimum_inner_quarters"] = 5
    choice = nested.inner_selection(rs, p)
    assert choice["status"] == "insufficient_inner_history"
    assert all(c["id"] == "baseline" for c in choice["chosen"].values())


def test_sparse_inner_does_not_drop_outer_rows_or_break_paired_cohort():
    p = protocol(); p.update(minimum_inner_markets=10000, evaluation_end="2025-12-31")
    _, report = nested.run_league((records(), p))
    assert report["evaluation_markets"] == 12
    assert {m["n"] for m in report["metrics"].values()} == {12}
    assert report["metrics"]["tuned_simple"] == report["metrics"]["baseline"]
    for f in report["folds"]:
        assert f["choices"]["tuned_simple"] == "baseline"
        assert f["cutoff_exclusive"] < f["start"]


def test_choices_stay_fixed_across_outer_quarters():
    p = protocol(); p["evaluation_end"] = "2025-12-31"
    _, r = nested.run_league((records(), p))
    assert len({f["selection_hash"] for f in r["folds"]}) == 1
    assert len({f["choices"]["tuned_minimum"] for f in r["folds"]}) == 1


def test_custom_fit_backward_compatible_and_validates_columns():
    rs = records()[:10]
    a = fit(rs, "2024-01-01", "scoring", 100)
    b = fit(rs, "2024-01-01", "full", 100, columns=nested.FEATURE_SETS["scoring"])
    np.testing.assert_allclose(predict(rs, a), predict(rs, b))
    with pytest.raises(ValueError): fit(rs, "2024-01-01", "full", 100, columns=["future_result"])
    with pytest.raises(ValueError): fit(rs, "2024-01-01", "full", 100, columns=["home_gf10"]*2)


@pytest.mark.parametrize("classes", [2, 3])
def test_optimizer_failure_retries_same_objective_with_exact_hessian(monkeypatch, classes):
    real_minimize = base_model.minimize
    calls = []
    def minimize_once(fun, x0, **kwargs):
        calls.append(kwargs["method"])
        if len(calls) == 1:
            return OptimizeResult(success=False, x=x0+.001, message="synthetic stalled warm point")
        assert np.all(x0 == 0), "retry must not reuse the stalled point"
        return real_minimize(fun, x0, **kwargs)
    monkeypatch.setattr(base_model, "minimize", minimize_once)
    rs = records()[:10]
    if classes == 3:
        rs = [{**r, "market": "승무패", "n_way": 3, "q": [.4, .3, .3], "winner": i%3} for i,r in enumerate(rs)]
    model = fit(rs, "2024-01-01", "full", 100000, columns=nested.FEATURE_SETS["compact"])
    assert calls == ["L-BFGS-B", "trust-exact"]
    assert np.isfinite(predict(rs, model)).all()


def test_sparse_support_does_not_run_unused_fits(monkeypatch):
    p = protocol(); p["minimum_inner_markets"] = 10000
    monkeypatch.setattr(nested, "fit", lambda *args, **kwargs: pytest.fail("sparse scope must not fit"))
    choice = nested.inner_selection(records(), p)
    assert choice["status"] == "insufficient_inner_history"


def test_report_publication_cannot_overwrite_concurrent_result(tmp_path):
    output = tmp_path/"report.json"
    nested.publish_report(output, {"owner": "first"})
    with pytest.raises(FileExistsError):
        nested.publish_report(output, {"owner": "second"})
    assert json.loads(output.read_text(encoding="utf-8")) == {"owner": "first"}


def test_outer_labels_cannot_change_first_fit_or_configuration():
    p = protocol(); p["evaluation_end"] = "2025-12-31"
    rs = records()
    changed = [{**r, "winner": 1-r["winner"]} if r["day"] >= "2025-01-01" else r for r in rs]
    _, a = nested.run_league((rs, p))
    _, b = nested.run_league((changed, p))
    assert a["selections"] == b["selections"]
    assert a["folds"][0]["models"] == b["folds"][0]["models"]


@pytest.mark.parametrize("key,value", [("production_allowed", True), ("ridge_grid", [0]),
    ("selection_cutoff_exclusive", "2024-12-31"), ("selection_cutoff_exclusive", "2025-01-02"),
    ("minimum_inner_weeks", 0)])
def test_protocol_rejects_unsafe_or_invalid_setting(key, value):
    p = protocol(); p[key] = value
    with pytest.raises(ValueError): nested.validate_protocol(p)
