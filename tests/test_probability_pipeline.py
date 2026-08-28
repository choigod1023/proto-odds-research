from __future__ import annotations

import copy
import math
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from probability_pipeline import (  # noqa: E402
    ARTIFACT_SCHEMA,
    ARTIFACT_VERSION,
    CALIBRATION_SCHEMA,
    CALIBRATION_VERSION,
    ArtifactValidationError,
    apply_artifact,
    apply_calibration,
    artifact_hash,
    build_artifact,
    combine_market_residual,
    evaluate_promotion,
    predict_probability,
    resolve_model,
    validate_artifact,
    validate_calibration,
)


IDENTITY = {
    "schema": CALIBRATION_SCHEMA,
    "version": CALIBRATION_VERSION,
    "method": "identity",
}


def model(
    sport: str = "*",
    market: str = "*",
    *,
    intercept: float = 0.0,
    coefficients: list[float] | None = None,
    calibration: dict | None = None,
) -> dict:
    return {
        "sport": sport,
        "market": market,
        "intercept": intercept,
        "coefficients": [0.2, -0.1] if coefficients is None else coefficients,
        "uncertainty_clip": {
            "residual_min": -0.5,
            "residual_max": 0.5,
            "logit_radius": 0.2,
            "probability_min": 0.01,
            "probability_max": 0.99,
        },
        "calibration": IDENTITY if calibration is None else calibration,
    }


def passing_evidence(**overrides) -> dict:
    evidence = {
        "pristine_future": True,
        "n_predictions": 300,
        "brier_improvement_ci95": [0.001, 0.009],
        "log_loss_improvement_ci95": [0.002, 0.012],
        "baseline_average_odds": 1.65,
        "candidate_average_odds": 1.65,
        "baseline_coverage": 0.72,
        "candidate_coverage": 0.72,
    }
    evidence.update(overrides)
    return evidence


def artifact(*, models: list[dict] | None = None, promoted: bool = False) -> dict:
    return build_artifact(
        feature_order=["elo_delta", "rest_delta"],
        models=models or [model()],
        evidence=passing_evidence() if promoted else None,
    )


def test_market_logit_residual_combination_and_clipping():
    probability, residual = combine_market_residual(
        0.60,
        9.0,
        residual_min=-0.4,
        residual_max=0.4,
        probability_min=0.01,
        probability_max=0.99,
    )
    expected = 1.0 / (1.0 + math.exp(-(math.log(0.6 / 0.4) + 0.4)))
    assert residual == 0.4
    assert probability == pytest.approx(expected)


def test_market_logit_residual_default_bounds_are_usable_and_finite():
    probability, residual = combine_market_residual(0.60, 0.25)
    assert 0.60 < probability < 1.0
    assert residual == 0.25


def test_identity_and_platt_logistic_calibration_need_no_sklearn():
    assert apply_calibration(0.62, IDENTITY) == 0.62
    calibration = {
        "schema": CALIBRATION_SCHEMA,
        "version": CALIBRATION_VERSION,
        "method": "platt",
        "slope": 2.0,
        "intercept": -0.3,
    }
    expected = 1.0 / (
        1.0 + math.exp(-(-0.3 + 2.0 * math.log(0.62 / (1.0 - 0.62))))
    )
    assert apply_calibration(0.62, calibration) == pytest.approx(expected)
    calibration["method"] = "logistic"
    assert apply_calibration(0.62, calibration) == pytest.approx(expected)


def test_isotonic_style_knots_interpolate_and_clip_at_endpoints():
    calibration = {
        "schema": CALIBRATION_SCHEMA,
        "version": CALIBRATION_VERSION,
        "method": "isotonic",
        "knots": [[0.2, 0.3], [0.5, 0.55], [0.8, 0.7]],
    }
    assert apply_calibration(0.1, calibration) == 0.3
    assert apply_calibration(0.35, calibration) == pytest.approx(0.425)
    assert apply_calibration(0.9, calibration) == 0.7
    calibration["method"] = "piecewise_monotonic"
    assert apply_calibration(0.35, calibration) == pytest.approx(0.425)


@pytest.mark.parametrize(
    "knots",
    (
        [[0.2, 0.2]],
        [[0.2, 0.2], [0.2, 0.4]],
        [[0.2, 0.4], [0.8, 0.3]],
        [[-0.1, 0.2], [0.8, 0.7]],
    ),
)
def test_invalid_isotonic_knots_are_rejected(knots):
    calibration = {
        "schema": CALIBRATION_SCHEMA,
        "version": CALIBRATION_VERSION,
        "method": "isotonic",
        "knots": knots,
    }
    with pytest.raises(ArtifactValidationError):
        validate_calibration(calibration)


def test_artifact_schema_version_and_feature_order_are_strict():
    valid = artifact()
    validate_artifact(valid)

    wrong_schema = copy.deepcopy(valid)
    wrong_schema["schema"] = "probability-pipeline-v2"
    with pytest.raises(ArtifactValidationError):
        validate_artifact(wrong_schema)

    wrong_version = copy.deepcopy(valid)
    wrong_version["version"] = ARTIFACT_VERSION + 1
    with pytest.raises(ArtifactValidationError):
        validate_artifact(wrong_version)

    extra_key = copy.deepcopy(valid)
    extra_key["typo"] = True
    with pytest.raises(ArtifactValidationError):
        validate_artifact(extra_key)

    wrong_order_length = copy.deepcopy(valid)
    wrong_order_length["feature_order"].append("lineup_delta")
    with pytest.raises(ArtifactValidationError):
        validate_artifact(wrong_order_length)


def test_sport_and_market_resolution_uses_documented_fallback_order():
    models = [
        model("*", "*", intercept=0.1),
        model("soccer", "*", intercept=0.2),
        model("*", "moneyline", intercept=0.3),
        model("soccer", "moneyline", intercept=0.4),
    ]
    built = artifact(models=models)
    exact, exact_scope = resolve_model(built, " Soccer ", "MONEYLINE")
    assert exact["intercept"] == 0.4
    assert exact_scope == ("soccer", "moneyline")

    sport_fallback, sport_scope = resolve_model(built, "soccer", "totals")
    assert sport_fallback["intercept"] == 0.2
    assert sport_scope == ("soccer", "*")

    market_fallback, market_scope = resolve_model(built, "baseball", "moneyline")
    assert market_fallback["intercept"] == 0.3
    assert market_scope == ("*", "moneyline")

    global_fallback, global_scope = resolve_model(built, "baseball", "totals")
    assert global_fallback["intercept"] == 0.1
    assert global_scope == ("*", "*")


def test_duplicate_normalized_scope_is_rejected():
    with pytest.raises(ArtifactValidationError):
        artifact(models=[model("Soccer", "moneyline"), model(" soccer ", "MONEYLINE")])


def test_shadow_artifact_calculates_diagnostics_but_retains_market_probability():
    result = apply_artifact(
        0.60,
        sport="soccer",
        market="moneyline",
        features={"elo_delta": 1.0, "rest_delta": 0.0},
        artifact=artifact(),
    )
    assert result.status == "shadow_only"
    assert not result.applied
    assert result.probability == 0.60
    assert result.shadow_probability > 0.60
    assert result.shadow_interval[0] <= result.shadow_probability <= result.shadow_interval[1]


def test_uncertainty_residual_and_probability_are_clipped():
    built = artifact(models=[model(intercept=100.0, coefficients=[0.0, 0.0])])
    result = apply_artifact(
        0.995,
        sport="baseball",
        market="moneyline",
        features={"elo_delta": 0.0, "rest_delta": 0.0},
        artifact=built,
    )
    assert result.raw_residual == 100.0
    assert result.clipped_residual == 0.5
    assert result.shadow_probability == 0.99
    assert result.shadow_interval[1] == 0.99


@pytest.mark.parametrize(
    "overrides,failed_check",
    (
        ({"pristine_future": False}, "pristine_future"),
        ({"n_predictions": 299}, "minimum_sample"),
        ({"brier_improvement_ci95": [0.0, 0.01]}, "brier_ci_improved"),
        ({"log_loss_improvement_ci95": [-0.001, 0.01]}, "log_loss_ci_improved"),
        ({"candidate_average_odds": 1.64}, "average_odds_preserved"),
        ({"candidate_coverage": 0.71}, "coverage_preserved"),
    ),
)
def test_promotion_requires_every_future_validation_condition(overrides, failed_check):
    gate = evaluate_promotion(passing_evidence(**overrides))
    assert gate["status"] == "shadow_only"
    assert not gate["passed"]
    assert not gate["checks"][failed_check]


def test_promotion_defaults_shadow_only_and_passes_only_complete_evidence():
    default_gate = evaluate_promotion()
    assert default_gate["status"] == "shadow_only"
    assert not default_gate["passed"]

    gate = evaluate_promotion(passing_evidence())
    assert gate["status"] == "promoted"
    assert gate["passed"]
    assert all(gate["checks"].values())


def test_policy_cannot_weaken_minimum_sample_or_preservation():
    weak_policy = {
        "minimum_pristine_predictions": 299,
        "minimum_average_odds_ratio": 0.99,
        "minimum_coverage_ratio": 0.99,
    }
    with pytest.raises(ArtifactValidationError):
        evaluate_promotion(passing_evidence(), weak_policy)


def test_promoted_artifact_applies_shadow_probability():
    built = artifact(promoted=True)
    result = apply_artifact(
        0.60,
        sport="soccer",
        market="moneyline",
        features={"elo_delta": 1.0, "rest_delta": 0.0},
        artifact=built,
    )
    assert result.status == "promoted"
    assert result.applied
    assert result.probability == result.shadow_probability
    assert result.probability > result.market_probability
    assert predict_probability(
        0.60,
        sport="soccer",
        market="moneyline",
        features={"elo_delta": 1.0, "rest_delta": 0.0},
        artifact=built,
    ) == result.probability


@pytest.mark.parametrize("failure", ("missing_feature", "bad_schema", "forged_promotion"))
def test_application_fails_closed_to_market_probability(failure):
    built = artifact(promoted=True)
    features = {"elo_delta": 1.0, "rest_delta": 0.0}
    if failure == "missing_feature":
        features.pop("rest_delta")
    elif failure == "bad_schema":
        built["schema"] = "unknown"
    else:
        built["promotion"] = evaluate_promotion()
        built["promotion"]["status"] = "promoted"
        built["promotion"]["passed"] = True

    result = apply_artifact(
        0.61,
        sport="soccer",
        market="moneyline",
        features=features,
        artifact=built,
    )
    assert result.probability == 0.61
    assert not result.applied
    assert result.status == "market_fallback"
    assert result.shadow_probability is None


def test_missing_scope_fails_closed_without_guessing():
    built = artifact(models=[model("soccer", "moneyline")])
    result = apply_artifact(
        0.54,
        sport="baseball",
        market="totals",
        features={"elo_delta": 1.0, "rest_delta": 0.0},
        artifact=built,
    )
    assert result.probability == 0.54
    assert result.reason == "no model matches sport and market"


def test_schema_constants_are_stable_for_serialized_artifacts():
    assert ARTIFACT_SCHEMA == "probability-pipeline"
    assert ARTIFACT_VERSION == 1


def test_artifact_hash_is_canonical_and_changes_with_model_content():
    built = artifact()
    reordered = {key: built[key] for key in reversed(list(built))}
    assert artifact_hash(reordered) == artifact_hash(built)
    changed = copy.deepcopy(built)
    changed["models"][0]["intercept"] = 0.1
    assert artifact_hash(changed) != artifact_hash(built)
