"""Conservative market-offset probability pipeline.

The market probability is the operational default.  A residual model may produce a
shadow probability, but it can only replace the market probability when its artifact
contains promotion evidence that passes :func:`evaluate_promotion`.

Artifacts are intentionally JSON-compatible and dependency-free at inference time.
Calibration supports identity, Platt/logistic scaling, and monotone piecewise-linear
(``isotonic``) knots without requiring scikit-learn.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any


ARTIFACT_SCHEMA = "probability-pipeline"
ARTIFACT_VERSION = 1
CALIBRATION_SCHEMA = "probability-calibration"
CALIBRATION_VERSION = 1

DEFAULT_PROMOTION_POLICY = {
    "minimum_pristine_predictions": 300,
    "minimum_average_odds_ratio": 1.0,
    "minimum_coverage_ratio": 1.0,
}

_TOP_LEVEL_KEYS = {"schema", "version", "feature_order", "models", "promotion"}
_MODEL_KEYS = {
    "sport",
    "market",
    "intercept",
    "coefficients",
    "uncertainty_clip",
    "calibration",
}
_UNCERTAINTY_KEYS = {
    "residual_min",
    "residual_max",
    "logit_radius",
    "probability_min",
    "probability_max",
}
_PROMOTION_KEYS = {"status", "passed", "checks", "reasons", "policy", "evidence"}
_PROMOTION_CHECK_KEYS = {
    "pristine_future",
    "minimum_sample",
    "brier_ci_improved",
    "log_loss_ci_improved",
    "average_odds_preserved",
    "coverage_preserved",
}
_PROMOTION_EVIDENCE_KEYS = {
    "pristine_future",
    "n_predictions",
    "brier_improvement_ci95",
    "log_loss_improvement_ci95",
    "baseline_average_odds",
    "candidate_average_odds",
    "baseline_coverage",
    "candidate_coverage",
}
_PROMOTION_POLICY_KEYS = set(DEFAULT_PROMOTION_POLICY)


class ArtifactValidationError(ValueError):
    """Raised when an artifact cannot be interpreted without guessing."""


@dataclass(frozen=True)
class ProbabilityPrediction:
    """Result of applying an artifact.

    ``probability`` is always safe to use operationally.  ``shadow_probability`` and
    ``shadow_interval`` are diagnostic outputs and must not be treated as promoted
    when ``applied`` is false.
    """

    probability: float
    market_probability: float
    shadow_probability: float | None
    shadow_interval: tuple[float, float] | None
    applied: bool
    status: str
    reason: str
    scope: tuple[str, str] | None = None
    raw_residual: float | None = None
    clipped_residual: float | None = None


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ArtifactValidationError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ArtifactValidationError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ArtifactValidationError(f"{name} must be a finite number")
    return number


def _probability(value: object, name: str) -> float:
    number = _finite_number(value, name)
    if not 0.0 < number < 1.0:
        raise ArtifactValidationError(f"{name} must be strictly between 0 and 1")
    return number


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ArtifactValidationError(
            f"{name} has invalid keys (missing={missing}, unknown={unknown})"
        )


def _normalize_scope(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{name} must be a string")
    normalized = " ".join(value.strip().casefold().split())
    if not normalized:
        raise ArtifactValidationError(f"{name} must not be empty")
    return normalized


def _logit(probability: float) -> float:
    return math.log(probability) - math.log1p(-probability)


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def validate_calibration(calibration: Mapping[str, Any] | None) -> None:
    """Validate one optional JSON calibration artifact."""

    if calibration is None:
        return
    if not isinstance(calibration, Mapping):
        raise ArtifactValidationError("calibration must be an object or null")
    method = calibration.get("method")
    if method == "identity":
        _exact_keys(
            calibration,
            {"schema", "version", "method"},
            "calibration",
        )
    elif method in {"platt", "logistic"}:
        _exact_keys(
            calibration,
            {"schema", "version", "method", "slope", "intercept"},
            "calibration",
        )
        slope = _finite_number(calibration["slope"], "calibration.slope")
        _finite_number(calibration["intercept"], "calibration.intercept")
        if slope <= 0.0:
            raise ArtifactValidationError("calibration.slope must be positive")
    elif method in {"isotonic", "piecewise_monotonic"}:
        _exact_keys(
            calibration,
            {"schema", "version", "method", "knots"},
            "calibration",
        )
        knots = calibration["knots"]
        if (
            isinstance(knots, (str, bytes))
            or not isinstance(knots, Sequence)
            or len(knots) < 2
        ):
            raise ArtifactValidationError("calibration.knots needs at least two knots")
        previous_x = previous_y = None
        for index, knot in enumerate(knots):
            if (
                isinstance(knot, (str, bytes))
                or not isinstance(knot, Sequence)
                or len(knot) != 2
            ):
                raise ArtifactValidationError(
                    f"calibration.knots[{index}] must be [input, output]"
                )
            x_value = _finite_number(knot[0], f"calibration.knots[{index}][0]")
            y_value = _finite_number(knot[1], f"calibration.knots[{index}][1]")
            if not 0.0 <= x_value <= 1.0 or not 0.0 <= y_value <= 1.0:
                raise ArtifactValidationError("calibration knots must be inside [0, 1]")
            if previous_x is not None and x_value <= previous_x:
                raise ArtifactValidationError("calibration knot inputs must increase strictly")
            if previous_y is not None and y_value < previous_y:
                raise ArtifactValidationError("calibration knot outputs must be monotone")
            previous_x, previous_y = x_value, y_value
    else:
        raise ArtifactValidationError(f"unsupported calibration method: {method!r}")
    if calibration["schema"] != CALIBRATION_SCHEMA:
        raise ArtifactValidationError("unsupported calibration schema")
    if calibration["version"] != CALIBRATION_VERSION:
        raise ArtifactValidationError("unsupported calibration version")


def apply_calibration(
    probability: object,
    calibration: Mapping[str, Any] | None = None,
) -> float:
    """Apply a validated monotone probability calibration."""

    input_probability = _probability(probability, "probability")
    validate_calibration(calibration)
    if calibration is None or calibration["method"] == "identity":
        return input_probability
    if calibration["method"] in {"platt", "logistic"}:
        return _sigmoid(
            float(calibration["intercept"])
            + float(calibration["slope"]) * _logit(input_probability)
        )

    knots = calibration["knots"]
    if input_probability <= float(knots[0][0]):
        return float(knots[0][1])
    if input_probability >= float(knots[-1][0]):
        return float(knots[-1][1])
    for left, right in zip(knots, knots[1:]):
        left_x, left_y = map(float, left)
        right_x, right_y = map(float, right)
        if input_probability <= right_x:
            fraction = (input_probability - left_x) / (right_x - left_x)
            return left_y + fraction * (right_y - left_y)
    raise AssertionError("validated knots did not cover the probability")


def combine_market_residual(
    market_probability: object,
    residual: object,
    *,
    residual_min: float = -20.0,
    residual_max: float = 20.0,
    probability_min: float = 1e-6,
    probability_max: float = 1.0 - 1e-6,
    calibration: Mapping[str, Any] | None = None,
) -> tuple[float, float]:
    """Return ``(probability, clipped_residual)`` for a logit-offset model."""

    market_value = _probability(market_probability, "market_probability")
    residual_value = _finite_number(residual, "residual")
    lower_residual = _finite_number(residual_min, "residual_min")
    upper_residual = _finite_number(residual_max, "residual_max")
    lower_probability = _finite_number(probability_min, "probability_min")
    upper_probability = _finite_number(probability_max, "probability_max")
    if lower_residual > upper_residual:
        raise ArtifactValidationError("residual_min must not exceed residual_max")
    if not 0.0 < lower_probability < upper_probability < 1.0:
        raise ArtifactValidationError("probability clip must lie strictly inside (0, 1)")
    clipped_residual = _clamp(residual_value, lower_residual, upper_residual)
    combined = _sigmoid(_logit(market_value) + clipped_residual)
    calibrated = apply_calibration(combined, calibration)
    return _clamp(calibrated, lower_probability, upper_probability), clipped_residual


def _strict_policy(policy: Mapping[str, Any] | None) -> dict[str, float | int]:
    if policy is None:
        return dict(DEFAULT_PROMOTION_POLICY)
    if not isinstance(policy, Mapping):
        raise ArtifactValidationError("promotion policy must be an object")
    _exact_keys(policy, _PROMOTION_POLICY_KEYS, "promotion.policy")
    minimum_n = policy["minimum_pristine_predictions"]
    if isinstance(minimum_n, bool) or not isinstance(minimum_n, int) or minimum_n < 300:
        raise ArtifactValidationError(
            "minimum_pristine_predictions must be an integer of at least 300"
        )
    odds_ratio = _finite_number(
        policy["minimum_average_odds_ratio"],
        "promotion.policy.minimum_average_odds_ratio",
    )
    coverage_ratio = _finite_number(
        policy["minimum_coverage_ratio"],
        "promotion.policy.minimum_coverage_ratio",
    )
    if odds_ratio < 1.0 or coverage_ratio < 1.0:
        raise ArtifactValidationError("promotion preservation ratios cannot be below 1.0")
    return {
        "minimum_pristine_predictions": minimum_n,
        "minimum_average_odds_ratio": odds_ratio,
        "minimum_coverage_ratio": coverage_ratio,
    }


def _promotion_interval(value: object, name: str) -> tuple[float, float] | None:
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) != 2
    ):
        return None
    try:
        lower = _finite_number(value[0], f"{name}[0]")
        upper = _finite_number(value[1], f"{name}[1]")
    except ArtifactValidationError:
        return None
    return (lower, upper) if lower <= upper else None


def evaluate_promotion(
    evidence: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the non-bypassable future-holdout promotion gate.

    Improvements are defined as ``market score - candidate score``, so the complete
    confidence interval must be positive.  Missing or malformed evidence never raises;
    it produces a ``shadow_only`` result.  Invalid/weak policy settings do raise.
    """

    checked_policy = _strict_policy(policy)
    checks = {key: False for key in sorted(_PROMOTION_CHECK_KEYS)}
    normalized_evidence: dict[str, Any] | None = None
    invalid_evidence = False
    if evidence is not None:
        if not isinstance(evidence, Mapping) or set(evidence) != _PROMOTION_EVIDENCE_KEYS:
            invalid_evidence = True
        else:
            normalized_evidence = dict(evidence)
            pristine = evidence["pristine_future"] is True
            n_predictions = evidence["n_predictions"]
            valid_n = (
                not isinstance(n_predictions, bool)
                and isinstance(n_predictions, int)
                and n_predictions >= checked_policy["minimum_pristine_predictions"]
            )
            brier_interval = _promotion_interval(
                evidence["brier_improvement_ci95"], "brier_improvement_ci95"
            )
            log_loss_interval = _promotion_interval(
                evidence["log_loss_improvement_ci95"], "log_loss_improvement_ci95"
            )
            checks["pristine_future"] = pristine
            checks["minimum_sample"] = valid_n
            checks["brier_ci_improved"] = bool(
                brier_interval is not None and brier_interval[0] > 0.0
            )
            checks["log_loss_ci_improved"] = bool(
                log_loss_interval is not None and log_loss_interval[0] > 0.0
            )
            try:
                baseline_odds = _finite_number(
                    evidence["baseline_average_odds"], "baseline_average_odds"
                )
                candidate_odds = _finite_number(
                    evidence["candidate_average_odds"], "candidate_average_odds"
                )
                baseline_coverage = _finite_number(
                    evidence["baseline_coverage"], "baseline_coverage"
                )
                candidate_coverage = _finite_number(
                    evidence["candidate_coverage"], "candidate_coverage"
                )
                valid_operating_metrics = (
                    baseline_odds > 1.0
                    and candidate_odds > 1.0
                    and 0.0 < baseline_coverage <= 1.0
                    and 0.0 < candidate_coverage <= 1.0
                )
            except ArtifactValidationError:
                valid_operating_metrics = False
                baseline_odds = candidate_odds = baseline_coverage = candidate_coverage = 0.0
            if valid_operating_metrics:
                checks["average_odds_preserved"] = (
                    candidate_odds
                    >= baseline_odds * checked_policy["minimum_average_odds_ratio"]
                )
                checks["coverage_preserved"] = (
                    candidate_coverage
                    >= baseline_coverage * checked_policy["minimum_coverage_ratio"]
                )

    reason_order = (
        ("pristine_future", "future predictions are not certified pristine"),
        ("minimum_sample", "fewer than the required pristine future predictions"),
        ("brier_ci_improved", "Brier improvement CI is not entirely above zero"),
        ("log_loss_ci_improved", "log-loss improvement CI is not entirely above zero"),
        ("average_odds_preserved", "average odds were not preserved"),
        ("coverage_preserved", "coverage was not preserved"),
    )
    reasons = [message for key, message in reason_order if not checks[key]]
    if evidence is None:
        reasons.insert(0, "promotion evidence is absent")
    elif invalid_evidence:
        reasons.insert(0, "promotion evidence schema is invalid")
    passed = not invalid_evidence and evidence is not None and all(checks.values())
    return {
        "status": "promoted" if passed else "shadow_only",
        "passed": passed,
        "checks": checks,
        "reasons": reasons,
        "policy": checked_policy,
        "evidence": normalized_evidence,
    }


def _validate_promotion(promotion: object) -> None:
    if not isinstance(promotion, Mapping):
        raise ArtifactValidationError("promotion must be an object")
    _exact_keys(promotion, _PROMOTION_KEYS, "promotion")
    expected = evaluate_promotion(promotion["evidence"], promotion["policy"])
    if dict(promotion) != expected:
        raise ArtifactValidationError("promotion result does not match its evidence")


def _validate_uncertainty(uncertainty: object, index: int) -> None:
    name = f"models[{index}].uncertainty_clip"
    if not isinstance(uncertainty, Mapping):
        raise ArtifactValidationError(f"{name} must be an object")
    _exact_keys(uncertainty, _UNCERTAINTY_KEYS, name)
    residual_min = _finite_number(uncertainty["residual_min"], f"{name}.residual_min")
    residual_max = _finite_number(uncertainty["residual_max"], f"{name}.residual_max")
    radius = _finite_number(uncertainty["logit_radius"], f"{name}.logit_radius")
    probability_min = _finite_number(
        uncertainty["probability_min"], f"{name}.probability_min"
    )
    probability_max = _finite_number(
        uncertainty["probability_max"], f"{name}.probability_max"
    )
    if residual_min > residual_max:
        raise ArtifactValidationError(f"{name} residual bounds are reversed")
    if radius < 0.0:
        raise ArtifactValidationError(f"{name}.logit_radius cannot be negative")
    if not 0.0 < probability_min < probability_max < 1.0:
        raise ArtifactValidationError(f"{name} probability bounds must be inside (0, 1)")


def validate_artifact(artifact: Mapping[str, Any]) -> None:
    """Strictly validate a v1 probability artifact or raise."""

    if not isinstance(artifact, Mapping):
        raise ArtifactValidationError("artifact must be an object")
    _exact_keys(artifact, _TOP_LEVEL_KEYS, "artifact")
    if artifact["schema"] != ARTIFACT_SCHEMA:
        raise ArtifactValidationError("unsupported artifact schema")
    if artifact["version"] != ARTIFACT_VERSION:
        raise ArtifactValidationError("unsupported artifact version")

    feature_order = artifact["feature_order"]
    if isinstance(feature_order, (str, bytes)) or not isinstance(feature_order, Sequence):
        raise ArtifactValidationError("feature_order must be an array")
    if any(not isinstance(name, str) or not name.strip() for name in feature_order):
        raise ArtifactValidationError("feature_order entries must be non-empty strings")
    if len(set(feature_order)) != len(feature_order):
        raise ArtifactValidationError("feature_order entries must be unique")

    models = artifact["models"]
    if isinstance(models, (str, bytes)) or not isinstance(models, Sequence):
        raise ArtifactValidationError("models must be an array")
    scopes: set[tuple[str, str]] = set()
    for index, model in enumerate(models):
        if not isinstance(model, Mapping):
            raise ArtifactValidationError(f"models[{index}] must be an object")
        _exact_keys(model, _MODEL_KEYS, f"models[{index}]")
        scope = (
            _normalize_scope(model["sport"], f"models[{index}].sport"),
            _normalize_scope(model["market"], f"models[{index}].market"),
        )
        if scope in scopes:
            raise ArtifactValidationError(f"duplicate model scope: {scope}")
        scopes.add(scope)
        _finite_number(model["intercept"], f"models[{index}].intercept")
        coefficients = model["coefficients"]
        if (
            isinstance(coefficients, (str, bytes))
            or not isinstance(coefficients, Sequence)
            or len(coefficients) != len(feature_order)
        ):
            raise ArtifactValidationError(
                f"models[{index}].coefficients must match feature_order exactly"
            )
        for coefficient_index, coefficient in enumerate(coefficients):
            _finite_number(
                coefficient,
                f"models[{index}].coefficients[{coefficient_index}]",
            )
        _validate_uncertainty(model["uncertainty_clip"], index)
        validate_calibration(model["calibration"])
    _validate_promotion(artifact["promotion"])


def artifact_hash(artifact: Mapping[str, Any]) -> str:
    """Return the canonical SHA-256 used by the code-review allowlist."""

    validate_artifact(artifact)
    payload = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_model(
    artifact: Mapping[str, Any],
    sport: object,
    market: object,
) -> tuple[Mapping[str, Any], tuple[str, str]] | None:
    """Resolve exact, sport fallback, market fallback, then global fallback."""

    validate_artifact(artifact)
    sport_name = _normalize_scope(sport, "sport")
    market_name = _normalize_scope(market, "market")
    indexed = {
        (
            _normalize_scope(model["sport"], "model.sport"),
            _normalize_scope(model["market"], "model.market"),
        ): model
        for model in artifact["models"]
    }
    for scope in (
        (sport_name, market_name),
        (sport_name, "*"),
        ("*", market_name),
        ("*", "*"),
    ):
        if scope in indexed:
            return indexed[scope], scope
    return None


def _fallback(market_probability: float, reason: str) -> ProbabilityPrediction:
    return ProbabilityPrediction(
        probability=market_probability,
        market_probability=market_probability,
        shadow_probability=None,
        shadow_interval=None,
        applied=False,
        status="market_fallback",
        reason=reason,
    )


def apply_artifact(
    market_probability: object,
    *,
    sport: object,
    market: object,
    features: Mapping[str, object],
    artifact: Mapping[str, Any] | None,
) -> ProbabilityPrediction:
    """Apply an artifact, failing closed to the market probability on any defect."""

    market_value = _probability(market_probability, "market_probability")
    if artifact is None:
        return _fallback(market_value, "artifact is absent")
    if not isinstance(features, Mapping):
        return _fallback(market_value, "features must be an object")
    try:
        validate_artifact(artifact)
        resolved = resolve_model(artifact, sport, market)
        if resolved is None:
            return _fallback(market_value, "no model matches sport and market")
        model, scope = resolved
        ordered_features = [
            _finite_number(features[name], f"features.{name}")
            for name in artifact["feature_order"]
        ]
        raw_residual = float(model["intercept"]) + sum(
            float(coefficient) * feature
            for coefficient, feature in zip(model["coefficients"], ordered_features)
        )
        uncertainty = model["uncertainty_clip"]
        shadow, clipped_residual = combine_market_residual(
            market_value,
            raw_residual,
            residual_min=float(uncertainty["residual_min"]),
            residual_max=float(uncertainty["residual_max"]),
            probability_min=float(uncertainty["probability_min"]),
            probability_max=float(uncertainty["probability_max"]),
            calibration=model["calibration"],
        )
        radius = float(uncertainty["logit_radius"])
        lower, _ = combine_market_residual(
            market_value,
            clipped_residual - radius,
            residual_min=float(uncertainty["residual_min"]),
            residual_max=float(uncertainty["residual_max"]),
            probability_min=float(uncertainty["probability_min"]),
            probability_max=float(uncertainty["probability_max"]),
            calibration=model["calibration"],
        )
        upper, _ = combine_market_residual(
            market_value,
            clipped_residual + radius,
            residual_min=float(uncertainty["residual_min"]),
            residual_max=float(uncertainty["residual_max"]),
            probability_min=float(uncertainty["probability_min"]),
            probability_max=float(uncertainty["probability_max"]),
            calibration=model["calibration"],
        )
    except (ArtifactValidationError, KeyError, TypeError, ValueError, OverflowError) as exc:
        return _fallback(market_value, f"artifact application failed: {exc}")

    promoted = (
        artifact["promotion"]["status"] == "promoted"
        and artifact["promotion"]["passed"] is True
    )
    return ProbabilityPrediction(
        probability=shadow if promoted else market_value,
        market_probability=market_value,
        shadow_probability=shadow,
        shadow_interval=(min(lower, upper), max(lower, upper)),
        applied=promoted,
        status="promoted" if promoted else "shadow_only",
        reason=(
            "promoted artifact applied"
            if promoted
            else "promotion gate not passed; market probability retained"
        ),
        scope=scope,
        raw_residual=raw_residual,
        clipped_residual=clipped_residual,
    )


def predict_probability(
    market_probability: object,
    *,
    sport: object,
    market: object,
    features: Mapping[str, object],
    artifact: Mapping[str, Any] | None,
) -> float:
    """Convenience wrapper returning only the operational probability."""

    return apply_artifact(
        market_probability,
        sport=sport,
        market=market,
        features=features,
        artifact=artifact,
    ).probability


def build_artifact(
    *,
    feature_order: Sequence[str],
    models: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Any] | None = None,
    promotion_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate a JSON-compatible v1 artifact."""

    artifact = {
        "schema": ARTIFACT_SCHEMA,
        "version": ARTIFACT_VERSION,
        "feature_order": list(feature_order),
        "models": [dict(model) for model in models],
        "promotion": evaluate_promotion(evidence, promotion_policy),
    }
    validate_artifact(artifact)
    return artifact


__all__ = [
    "ARTIFACT_SCHEMA",
    "ARTIFACT_VERSION",
    "CALIBRATION_SCHEMA",
    "CALIBRATION_VERSION",
    "DEFAULT_PROMOTION_POLICY",
    "ArtifactValidationError",
    "ProbabilityPrediction",
    "apply_artifact",
    "apply_calibration",
    "artifact_hash",
    "build_artifact",
    "combine_market_residual",
    "evaluate_promotion",
    "predict_probability",
    "resolve_model",
    "validate_artifact",
    "validate_calibration",
]
