"""종목별 스코어 분포와 출전 시나리오를 안전하게 요약한다.

``score_dist`` 는 기대 스코어로부터 결합분포를 만드는 저수준 모듈이다. 이 모듈은
그 위에 운영 계약을 더한다.

* 종목마다 스코어 단위와 정산 방식을 명시한다.
* 배구의 스코어는 **세트 수**이며 포인트 총득점으로 사용하지 못하게 한다.
* 야구처럼 2-way로 정산하는 시장은 무승부 질량을 제외해 승률을 조건화한다.
* 실제 사용한 분포족과 rho를 메타데이터에 기록한다.
* 라인업/출전 시나리오는 난수 추출 없이 가중 결합분포로 정확히 혼합한다.

시나리오 구간은 경기 결과 자체의 예측구간이 아니라, 공급된 라인업 시나리오들
사이에서 예측값이 얼마나 움직이는지를 나타낸다. 난수 seed나 임의의 "미래 상수"는
예측 입력으로 받지 않는다. 불확실성은 이름과 확률이 있는 시나리오로만 표현한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Iterable, Mapping

import numpy as np

from score_dist import distribution_metadata, joint as score_joint


class ScoreForecastError(ValueError):
    """스코어 예측 계약을 지키지 않은 입력."""


@dataclass(frozen=True)
class SportForecastContract:
    """스코어 행렬의 단위와 기본 정산 계약."""

    sport: str
    name: str
    score_unit: str
    score_unit_label: str
    total_market_unit: str
    primary_result_market: str
    condition_on_non_draw: bool
    default_distribution_family: str
    volleyball_sets_to_win: int | None = None

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "name": self.name,
            "score_unit": self.score_unit,
            "score_unit_label": self.score_unit_label,
            "total_market_unit": self.total_market_unit,
            "primary_result_market": self.primary_result_market,
            "condition_on_non_draw": self.condition_on_non_draw,
            "default_distribution_family": self.default_distribution_family,
            "volleyball_sets_to_win": self.volleyball_sets_to_win,
        }


SPORT_CONTRACTS: dict[str, SportForecastContract] = {
    "bs": SportForecastContract(
        sport="bs",
        name="baseball",
        score_unit="runs",
        score_unit_label="득점",
        total_market_unit="runs",
        primary_result_market="two_way",
        condition_on_non_draw=True,
        default_distribution_family="independent_poisson",
    ),
    "sc": SportForecastContract(
        sport="sc",
        name="soccer",
        score_unit="goals",
        score_unit_label="골",
        total_market_unit="goals",
        primary_result_market="1x2",
        condition_on_non_draw=False,
        default_distribution_family="independent_poisson",
    ),
    "bk": SportForecastContract(
        sport="bk",
        name="basketball",
        score_unit="points",
        score_unit_label="득점",
        total_market_unit="points",
        primary_result_market="two_way",
        condition_on_non_draw=True,
        default_distribution_family="independent_poisson_or_normal_approximation",
    ),
    "vl": SportForecastContract(
        sport="vl",
        name="volleyball",
        score_unit="sets",
        score_unit_label="세트",
        total_market_unit="sets",
        primary_result_market="two_way",
        condition_on_non_draw=True,
        default_distribution_family="conditioned_independent_poisson",
        volleyball_sets_to_win=3,
    ),
}

_SPORT_ALIASES = {
    "baseball": "bs",
    "soccer": "sc",
    "football": "sc",
    "basketball": "bk",
    "volleyball": "vl",
}


def get_sport_contract(sport: str) -> SportForecastContract:
    """종목 코드 또는 영문 이름에 대응하는 계약을 반환한다."""

    key = str(sport or "").strip().lower()
    key = _SPORT_ALIASES.get(key, key)
    try:
        return SPORT_CONTRACTS[key]
    except KeyError as exc:
        supported = ", ".join(sorted(SPORT_CONTRACTS))
        raise ScoreForecastError(
            f"지원하지 않는 종목 {sport!r}; 지원 코드: {supported}"
        ) from exc


@dataclass(frozen=True)
class OutcomeProbabilities:
    home_win: float
    draw: float
    away_win: float

    def to_dict(self) -> dict[str, float]:
        return {
            "home_win": self.home_win,
            "draw": self.draw,
            "away_win": self.away_win,
        }


@dataclass(frozen=True)
class ScorelineProbability:
    home_score: int
    away_score: int
    probability: float
    score_unit: str

    def to_dict(self) -> dict:
        return {
            "home_score": self.home_score,
            "away_score": self.away_score,
            "probability": self.probability,
            "score_unit": self.score_unit,
        }


@dataclass(frozen=True)
class MetricSummary:
    """시나리오 가중 점추정과 equal-tail 신용구간."""

    point: float
    median: float
    lower: float
    upper: float
    credible_mass: float

    def to_dict(self) -> dict[str, float]:
        return {
            "point": self.point,
            "median": self.median,
            "lower": self.lower,
            "upper": self.upper,
            "credible_mass": self.credible_mass,
        }


@dataclass(frozen=True)
class ScenarioContribution:
    name: str
    weight: float
    outcomes_1x2: OutcomeProbabilities
    win_probabilities: OutcomeProbabilities
    expected_home_score: float
    expected_away_score: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "weight": self.weight,
            "outcomes_1x2": self.outcomes_1x2.to_dict(),
            "win_probabilities": self.win_probabilities.to_dict(),
            "expected_scores": {
                "home": self.expected_home_score,
                "away": self.expected_away_score,
            },
        }


@dataclass(frozen=True)
class ScoreForecast:
    contract: SportForecastContract
    probability_matrix: np.ndarray = field(repr=False, compare=False)
    outcomes_1x2: OutcomeProbabilities
    win_probabilities: OutcomeProbabilities
    expected_home_score: float
    expected_away_score: float
    top_scorelines: tuple[ScorelineProbability, ...]
    distribution_model: Mapping[str, object] = field(default_factory=dict)
    scenario_contributions: tuple[ScenarioContribution, ...] = ()
    uncertainty: Mapping[str, MetricSummary] = field(default_factory=dict)

    @property
    def expected_total_score(self) -> float:
        return self.expected_home_score + self.expected_away_score

    def to_dict(self, *, include_matrix: bool = False) -> dict:
        result = {
            "contract": self.contract.to_dict(),
            "distribution_model": dict(self.distribution_model),
            "outcomes_1x2": self.outcomes_1x2.to_dict(),
            "win_probabilities": self.win_probabilities.to_dict(),
            "expected_scores": {
                "home": self.expected_home_score,
                "away": self.expected_away_score,
                "total": self.expected_total_score,
                "unit": self.contract.score_unit,
            },
            "top_scorelines": [row.to_dict() for row in self.top_scorelines],
            "scenario_contributions": [
                row.to_dict() for row in self.scenario_contributions
            ],
            "uncertainty": {
                key: summary.to_dict() for key, summary in self.uncertainty.items()
            },
        }
        if include_matrix:
            result["probability_matrix"] = self.probability_matrix.tolist()
        return result


@dataclass(frozen=True)
class ScoreScenario:
    """라인업/출전 상태 하나와 그 상태가 발생할 확률.

    ``lam_home``/``lam_away``를 주면 ``score_dist.joint``를 사용한다. 이미 종목별
    모델이 만든 행렬이 있으면 ``probability_matrix``를 대신 줄 수 있다. 두 표현을
    동시에 주거나 일부 람다만 주는 입력은 거부한다.
    """

    name: str
    weight: float
    lam_home: float | None = None
    lam_away: float | None = None
    rho: float = 0.0
    probability_matrix: np.ndarray | None = field(
        default=None, repr=False, compare=False
    )


def _validate_number(value: float, name: str, *, allow_zero: bool = True) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ScoreForecastError(f"{name}은 유한한 숫자여야 한다") from exc
    if not isfinite(number) or number < 0 or (not allow_zero and number == 0):
        relation = "0보다 커야" if not allow_zero else "0 이상이어야"
        raise ScoreForecastError(f"{name}은 유한하고 {relation} 한다")
    return number


def _volleyball_mask(shape: tuple[int, int], sets_to_win: int) -> np.ndarray:
    if sets_to_win < 1:
        raise ScoreForecastError("volleyball sets_to_win은 1 이상이어야 한다")
    rows, cols = np.indices(shape)
    # 승자는 필요한 세트 수에 정확히 도달하고 패자는 그보다 적어야 한다.
    return ((rows == sets_to_win) & (cols < sets_to_win)) | (
        (cols == sets_to_win) & (rows < sets_to_win)
    )


def _validated_sets_to_win(value: object) -> int:
    if (
        isinstance(value, (bool, np.bool_))
        or not isinstance(value, (int, np.integer))
        or int(value) < 1
    ):
        raise ScoreForecastError("volleyball sets_to_win은 1 이상의 정수여야 한다")
    return int(value)


def normalize_probability_matrix(
    matrix: np.ndarray,
    sport: str,
    *,
    volleyball_sets_to_win: int | None = None,
) -> np.ndarray:
    """행렬을 검증·정규화하고 종목상 불가능한 스코어를 제거한다."""

    contract = get_sport_contract(sport)
    try:
        result = np.asarray(matrix, dtype=float).copy()
    except (TypeError, ValueError) as exc:
        raise ScoreForecastError("probability_matrix는 숫자 행렬이어야 한다") from exc
    if result.ndim != 2 or not result.shape[0] or not result.shape[1]:
        raise ScoreForecastError("probability_matrix는 비어 있지 않은 2차원 행렬이어야 한다")
    if not np.isfinite(result).all():
        raise ScoreForecastError("probability_matrix에 NaN 또는 무한대가 있다")
    if float(result.min()) < -1e-12:
        raise ScoreForecastError("probability_matrix에 음수 확률이 있다")
    # 수치 오차 수준의 음수만 0으로 보정한다.
    result[result < 0] = 0.0

    if contract.sport == "vl":
        sets_to_win = (
            contract.volleyball_sets_to_win
            if volleyball_sets_to_win is None
            else volleyball_sets_to_win
        )
        if sets_to_win is None:
            raise ScoreForecastError("배구 계약에 sets_to_win이 필요하다")
        result *= _volleyball_mask(result.shape, _validated_sets_to_win(sets_to_win))

    total = float(result.sum())
    if not isfinite(total) or total <= 0:
        raise ScoreForecastError("유효한 스코어 확률 질량이 없다")
    result /= total
    # 호출자가 사후에 분포를 바꾸어 요약값과 불일치시키지 못하게 한다.
    result.setflags(write=False)
    return result


def _outcomes(matrix: np.ndarray) -> OutcomeProbabilities:
    home = draw = away = 0.0
    for i, j in np.ndindex(matrix.shape):
        probability = float(matrix[i, j])
        if i > j:
            home += probability
        elif i == j:
            draw += probability
        else:
            away += probability
    total = home + draw + away
    if total <= 0:
        raise ScoreForecastError("승무패 확률을 계산할 수 없다")
    return OutcomeProbabilities(home / total, draw / total, away / total)


def _conditioned_wins(
    outcomes: OutcomeProbabilities,
    condition_on_non_draw: bool,
) -> OutcomeProbabilities:
    if not condition_on_non_draw:
        return outcomes
    decisive = outcomes.home_win + outcomes.away_win
    if decisive <= 0:
        raise ScoreForecastError("무승부만 있는 분포는 2-way 승률로 조건화할 수 없다")
    return OutcomeProbabilities(
        outcomes.home_win / decisive,
        0.0,
        outcomes.away_win / decisive,
    )


def _expected_scores(matrix: np.ndarray) -> tuple[float, float]:
    home_scores = np.arange(matrix.shape[0], dtype=float)[:, None]
    away_scores = np.arange(matrix.shape[1], dtype=float)[None, :]
    return (
        float(np.sum(matrix * home_scores)),
        float(np.sum(matrix * away_scores)),
    )


def _top_scorelines(
    matrix: np.ndarray,
    contract: SportForecastContract,
    top_n: int,
) -> tuple[ScorelineProbability, ...]:
    if isinstance(top_n, bool) or int(top_n) != top_n or top_n <= 0:
        raise ScoreForecastError("top_n은 양의 정수여야 한다")
    candidates = [
        (float(matrix[i, j]), int(i), int(j))
        for i, j in np.ndindex(matrix.shape)
        if matrix[i, j] > 0
    ]
    candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
    return tuple(
        ScorelineProbability(
            home_score=home,
            away_score=away,
            probability=probability,
            score_unit=contract.score_unit,
        )
        for probability, home, away in candidates[: int(top_n)]
    )


def forecast_from_matrix(
    sport: str,
    probability_matrix: np.ndarray,
    *,
    top_n: int = 5,
    condition_on_non_draw: bool | None = None,
    volleyball_sets_to_win: int | None = None,
    distribution_model: Mapping[str, object] | None = None,
) -> ScoreForecast:
    """검증된 결합분포에서 승무패·승률·예상 스코어를 만든다."""

    contract = get_sport_contract(sport)
    matrix = normalize_probability_matrix(
        probability_matrix,
        contract.sport,
        volleyball_sets_to_win=volleyball_sets_to_win,
    )
    outcomes = _outcomes(matrix)
    condition = (
        contract.condition_on_non_draw
        if condition_on_non_draw is None
        else bool(condition_on_non_draw)
    )
    wins = _conditioned_wins(outcomes, condition)
    expected_home, expected_away = _expected_scores(matrix)
    model = dict(distribution_model or {"family": "provided_joint_distribution"})
    if contract.sport == "vl":
        model.setdefault("normalization", "valid_best_of_five_scorelines")
    return ScoreForecast(
        contract=contract,
        probability_matrix=matrix,
        outcomes_1x2=outcomes,
        win_probabilities=wins,
        expected_home_score=expected_home,
        expected_away_score=expected_away,
        top_scorelines=_top_scorelines(matrix, contract, top_n),
        distribution_model=model,
    )


def _matrix_from_lambdas(
    sport: str,
    lam_home: float,
    lam_away: float,
    *,
    rho: float = 0.0,
    volleyball_sets_to_win: int | None = None,
) -> tuple[SportForecastContract, np.ndarray, dict[str, object]]:
    """λ 입력을 검증하고 종목 계약이 적용된 행렬과 실제 모델 메타를 만든다."""

    contract = get_sport_contract(sport)
    home = _validate_number(lam_home, "lam_home")
    away = _validate_number(lam_away, "lam_away")
    try:
        correlation = float(rho)
    except (TypeError, ValueError) as exc:
        raise ScoreForecastError("rho는 유한한 숫자여야 한다") from exc
    if not isfinite(correlation):
        raise ScoreForecastError("rho는 유한한 숫자여야 한다")
    if contract.sport != "sc" and abs(correlation) > 1e-15:
        raise ScoreForecastError("Dixon-Coles rho는 축구 분포에만 사용할 수 있다")
    matrix = normalize_probability_matrix(
        score_joint(home, away, contract.sport, rho=correlation),
        contract.sport,
        volleyball_sets_to_win=volleyball_sets_to_win,
    )
    model: dict[str, object] = dict(
        distribution_metadata(home, away, contract.sport, correlation)
    )
    if contract.sport == "vl":
        model["normalization"] = "valid_best_of_five_scorelines"
    return contract, matrix, model


def probability_matrix_from_lambdas(
    sport: str,
    lam_home: float,
    lam_away: float,
    *,
    rho: float = 0.0,
    volleyball_sets_to_win: int | None = None,
) -> np.ndarray:
    """운영 마켓과 스코어 요약이 함께 써야 하는 정규화 결합분포."""

    _, matrix, _ = _matrix_from_lambdas(
        sport,
        lam_home,
        lam_away,
        rho=rho,
        volleyball_sets_to_win=volleyball_sets_to_win,
    )
    return matrix


def forecast_from_lambdas(
    sport: str,
    lam_home: float,
    lam_away: float,
    *,
    rho: float = 0.0,
    top_n: int = 5,
    condition_on_non_draw: bool | None = None,
    volleyball_sets_to_win: int | None = None,
) -> ScoreForecast:
    """``score_dist.joint``로 만든 분포에 종목 계약을 적용한다."""

    contract, matrix, model = _matrix_from_lambdas(
        sport,
        lam_home,
        lam_away,
        rho=rho,
        volleyball_sets_to_win=volleyball_sets_to_win,
    )
    return forecast_from_matrix(
        contract.sport,
        matrix,
        top_n=top_n,
        condition_on_non_draw=condition_on_non_draw,
        volleyball_sets_to_win=volleyball_sets_to_win,
        distribution_model=model,
    )


_FORBIDDEN_RANDOM_FIELDS = {
    "seed",
    "random_seed",
    "future_constant",
    "future_random",
    "random_feature",
}
_SCENARIO_FIELDS = {
    "name",
    "label",
    "weight",
    "probability",
    "lam_home",
    "lambda_home",
    "lam_away",
    "lambda_away",
    "rho",
    "probability_matrix",
    "matrix",
}


def _one_alias(row: Mapping, primary: str, alias: str, *, required: bool = False):
    present = [key for key in (primary, alias) if key in row]
    if len(present) > 1:
        raise ScoreForecastError(f"{primary}와 {alias}를 동시에 줄 수 없다")
    if not present:
        if required:
            raise ScoreForecastError(f"시나리오에 {primary}가 필요하다")
        return None
    return row[present[0]]


def _coerce_scenario(value: ScoreScenario | Mapping) -> ScoreScenario:
    if isinstance(value, ScoreScenario):
        return value
    if not isinstance(value, Mapping):
        raise ScoreForecastError("시나리오는 ScoreScenario 또는 mapping이어야 한다")
    random_fields = set(value) & _FORBIDDEN_RANDOM_FIELDS
    if random_fields:
        fields = ", ".join(sorted(random_fields))
        raise ScoreForecastError(
            f"난수/미래상수는 예측 입력이 아니다: {fields}; 확률 시나리오로 표현해야 한다"
        )
    unknown = set(value) - _SCENARIO_FIELDS
    if unknown:
        fields = ", ".join(sorted(str(field) for field in unknown))
        raise ScoreForecastError(f"알 수 없는 시나리오 필드: {fields}")
    return ScoreScenario(
        name=_one_alias(value, "name", "label", required=True),
        weight=_one_alias(value, "weight", "probability", required=True),
        lam_home=_one_alias(value, "lam_home", "lambda_home"),
        lam_away=_one_alias(value, "lam_away", "lambda_away"),
        rho=value.get("rho", 0.0),
        probability_matrix=_one_alias(value, "probability_matrix", "matrix"),
    )


def _scenario_matrix(
    scenario: ScoreScenario,
    contract: SportForecastContract,
    *,
    volleyball_sets_to_win: int | None,
) -> np.ndarray:
    has_matrix = scenario.probability_matrix is not None
    has_home = scenario.lam_home is not None
    has_away = scenario.lam_away is not None
    if has_matrix and (has_home or has_away):
        raise ScoreForecastError(
            f"시나리오 {scenario.name!r}: 행렬과 lambda를 동시에 줄 수 없다"
        )
    if has_matrix:
        if abs(float(scenario.rho)) > 1e-15:
            raise ScoreForecastError(
                f"시나리오 {scenario.name!r}: 기존 행렬에는 rho를 다시 적용할 수 없다"
            )
        return normalize_probability_matrix(
            scenario.probability_matrix,
            contract.sport,
            volleyball_sets_to_win=volleyball_sets_to_win,
        )
    if not (has_home and has_away):
        raise ScoreForecastError(
            f"시나리오 {scenario.name!r}: lam_home과 lam_away가 모두 필요하다"
        )
    return forecast_from_lambdas(
        contract.sport,
        scenario.lam_home,
        scenario.lam_away,
        rho=scenario.rho,
        top_n=1,
        volleyball_sets_to_win=volleyball_sets_to_win,
    ).probability_matrix


def _weighted_quantile(
    values: Iterable[float],
    weights: Iterable[float],
    quantile: float,
) -> float:
    value_array = np.asarray(tuple(values), dtype=float)
    weight_array = np.asarray(tuple(weights), dtype=float)
    order = np.argsort(value_array, kind="stable")
    sorted_values = value_array[order]
    sorted_weights = weight_array[order]
    cumulative = np.cumsum(sorted_weights)
    index = int(np.searchsorted(cumulative, quantile, side="left"))
    return float(sorted_values[min(index, len(sorted_values) - 1)])


def _metric_summary(
    point: float,
    values: list[float],
    weights: list[float],
    credible_mass: float,
) -> MetricSummary:
    tail = (1.0 - credible_mass) / 2.0
    return MetricSummary(
        point=float(point),
        median=_weighted_quantile(values, weights, 0.5),
        lower=_weighted_quantile(values, weights, tail),
        upper=_weighted_quantile(values, weights, 1.0 - tail),
        credible_mass=credible_mass,
    )


def _pad_matrix(matrix: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    result = np.zeros(shape, dtype=float)
    result[: matrix.shape[0], : matrix.shape[1]] = matrix
    return result


def forecast_scenarios(
    sport: str,
    scenarios: Iterable[ScoreScenario | Mapping],
    *,
    credible_mass: float = 0.90,
    top_n: int = 5,
    condition_on_non_draw: bool | None = None,
    volleyball_sets_to_win: int | None = None,
) -> ScoreForecast:
    """라인업/출전 시나리오들의 결합분포를 정확한 가중합으로 혼합한다.

    Monte Carlo 표본을 뽑지 않으므로 같은 입력은 항상 비트 수준에서 같은 결과를
    만든다. ``weight``는 합이 1일 필요는 없지만 각 값은 음수가 아니어야 하며,
    함수 안에서 정규화된다.
    """

    contract = get_sport_contract(sport)
    try:
        mass = float(credible_mass)
    except (TypeError, ValueError) as exc:
        raise ScoreForecastError("credible_mass는 0과 1 사이여야 한다") from exc
    if not isfinite(mass) or not 0 < mass < 1:
        raise ScoreForecastError("credible_mass는 0과 1 사이여야 한다")

    rows = [_coerce_scenario(row) for row in scenarios]
    if not rows:
        raise ScoreForecastError("시나리오가 하나 이상 필요하다")
    names = [str(row.name).strip() for row in rows]
    if any(not name for name in names):
        raise ScoreForecastError("시나리오 이름은 비어 있을 수 없다")
    if len(set(names)) != len(names):
        raise ScoreForecastError("시나리오 이름은 중복될 수 없다")

    raw_weights = [
        _validate_number(row.weight, f"시나리오 {name!r} weight")
        for row, name in zip(rows, names)
    ]
    total_weight = sum(raw_weights)
    if total_weight <= 0:
        raise ScoreForecastError("시나리오 weight 합은 0보다 커야 한다")
    weights = [weight / total_weight for weight in raw_weights]

    matrices = [
        _scenario_matrix(
            row,
            contract,
            volleyball_sets_to_win=volleyball_sets_to_win,
        )
        for row in rows
    ]
    max_shape = (
        max(matrix.shape[0] for matrix in matrices),
        max(matrix.shape[1] for matrix in matrices),
    )
    mixture = np.zeros(max_shape, dtype=float)
    for weight, matrix in zip(weights, matrices):
        mixture += weight * _pad_matrix(matrix, max_shape)

    result = forecast_from_matrix(
        contract.sport,
        mixture,
        top_n=top_n,
        condition_on_non_draw=condition_on_non_draw,
        volleyball_sets_to_win=volleyball_sets_to_win,
    )
    scenario_forecasts = [
        forecast_from_matrix(
            contract.sport,
            matrix,
            top_n=1,
            condition_on_non_draw=condition_on_non_draw,
            volleyball_sets_to_win=volleyball_sets_to_win,
        )
        for matrix in matrices
    ]
    contributions = tuple(
        ScenarioContribution(
            name=name,
            weight=weight,
            outcomes_1x2=forecast.outcomes_1x2,
            win_probabilities=forecast.win_probabilities,
            expected_home_score=forecast.expected_home_score,
            expected_away_score=forecast.expected_away_score,
        )
        for name, weight, forecast in zip(names, weights, scenario_forecasts)
    )

    metric_values = {
        "home_win_probability": [
            forecast.win_probabilities.home_win for forecast in scenario_forecasts
        ],
        "draw_probability": [
            forecast.outcomes_1x2.draw for forecast in scenario_forecasts
        ],
        "away_win_probability": [
            forecast.win_probabilities.away_win for forecast in scenario_forecasts
        ],
        "expected_home_score": [
            forecast.expected_home_score for forecast in scenario_forecasts
        ],
        "expected_away_score": [
            forecast.expected_away_score for forecast in scenario_forecasts
        ],
        "expected_total_score": [
            forecast.expected_total_score for forecast in scenario_forecasts
        ],
    }
    points = {
        "home_win_probability": result.win_probabilities.home_win,
        "draw_probability": result.outcomes_1x2.draw,
        "away_win_probability": result.win_probabilities.away_win,
        "expected_home_score": result.expected_home_score,
        "expected_away_score": result.expected_away_score,
        "expected_total_score": result.expected_total_score,
    }
    uncertainty = {
        key: _metric_summary(points[key], values, weights, mass)
        for key, values in metric_values.items()
    }
    mixture_model: dict[str, object] = {
        "family": "deterministic_scenario_mixture",
        "scenario_count": len(rows),
        "random_sampling": False,
    }
    if contract.sport == "vl":
        mixture_model["normalization"] = "valid_best_of_five_scorelines"
    return ScoreForecast(
        contract=result.contract,
        probability_matrix=result.probability_matrix,
        outcomes_1x2=result.outcomes_1x2,
        win_probabilities=result.win_probabilities,
        expected_home_score=result.expected_home_score,
        expected_away_score=result.expected_away_score,
        top_scorelines=result.top_scorelines,
        distribution_model=mixture_model,
        scenario_contributions=contributions,
        uncertainty=uncertainty,
    )


def over_probability(forecast: ScoreForecast, line: float, *, unit: str) -> float:
    """명시된 단위의 총합 오버 확률.

    ``unit``를 필수로 받아 배구 세트 분포를 포인트 언더오버에 실수로 넣는 일을
    막는다. 배구 포인트 총득점은 별도의 랠리/포인트 모델이 필요하다.
    """

    expected_unit = forecast.contract.total_market_unit
    if str(unit).strip().lower() != expected_unit:
        raise ScoreForecastError(
            f"{forecast.contract.name} 분포의 총합 단위는 {expected_unit!r}이며 "
            f"{unit!r} 마켓에는 사용할 수 없다"
        )
    try:
        total_line = float(line)
    except (TypeError, ValueError) as exc:
        raise ScoreForecastError("line은 유한한 숫자여야 한다") from exc
    if not isfinite(total_line):
        raise ScoreForecastError("line은 유한한 숫자여야 한다")
    probability = 0.0
    for i, j in np.ndindex(forecast.probability_matrix.shape):
        if i + j > total_line:
            probability += float(forecast.probability_matrix[i, j])
    return probability


__all__ = [
    "MetricSummary",
    "OutcomeProbabilities",
    "SPORT_CONTRACTS",
    "ScenarioContribution",
    "ScoreForecast",
    "ScoreForecastError",
    "ScoreScenario",
    "ScorelineProbability",
    "SportForecastContract",
    "forecast_from_lambdas",
    "forecast_from_matrix",
    "forecast_scenarios",
    "get_sport_contract",
    "normalize_probability_matrix",
    "probability_matrix_from_lambdas",
    "over_probability",
]
