"""로또 6/45 검증·확률모델·조합 최적화 핵심 모듈.

이 모듈의 최우선 규칙은 복잡한 모델이 아니라 *균등 복귀*다. 번호 가중치는
워크포워드 미공개 구간에서 Brier score와 조합 로그점수를 동시에 개선하고,
최근 절반에서도 같은 방향이 재현될 때만 운영에 쓰인다.

`미래상수`는 미래를 알려주는 숫자가 아니다. 추정 가능한 지속 상태와 추정할 수
없는 다음 회차 충격을 분리하고, 후자는 조합의 불확실성/강건성 계산에만 쓴다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np


NUMBER_COUNT = 45
DRAW_SIZE = 6
UNIFORM_INCLUSION = DRAW_SIZE / NUMBER_COUNT
PAIR_INCLUSION = DRAW_SIZE * (DRAW_SIZE - 1) / (NUMBER_COUNT * (NUMBER_COUNT - 1))
COMBINATION_COUNT = math.comb(NUMBER_COUNT, DRAW_SIZE)
UNIFORM_LOG_PROBABILITY = -math.log(COMBINATION_COUNT)
MODEL_VERSION = "lotto-fc-v1.0"
KST = timezone(timedelta(hours=9))
FIRST_DRAW_DATE = date(2002, 12, 7)


def canonical_json(value: Any) -> str:
    """해시/사전등록에 쓰는 결정적 JSON 직렬화."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DrawRecord:
    draw_no: int
    draw_date: str
    numbers_sorted: tuple[int, ...]
    bonus_number: int
    numbers_draw_order: tuple[int, ...] | None = None
    machine_id: str | None = None
    ball_set_id: str | None = None
    location: str | None = None
    draw_time: str | None = None
    procedure_version: str | None = None
    sales_amount: int | None = None
    winner_count_by_rank: dict[str, int] | None = None
    prize_by_rank: dict[str, int] | None = None
    source_url: str = ""
    collected_at: str = ""
    raw_data_hash: str = ""
    verification_status: dict[str, str] | None = None

    def __post_init__(self) -> None:
        nums = tuple(int(x) for x in self.numbers_sorted)
        if len(nums) != DRAW_SIZE or len(set(nums)) != DRAW_SIZE:
            raise ValueError(f"{self.draw_no}회 번호는 서로 다른 6개여야 합니다: {nums}")
        if tuple(sorted(nums)) != nums or not all(1 <= x <= NUMBER_COUNT for x in nums):
            raise ValueError(f"{self.draw_no}회 정렬 번호가 유효하지 않습니다: {nums}")
        if not 1 <= int(self.bonus_number) <= NUMBER_COUNT or self.bonus_number in nums:
            raise ValueError(f"{self.draw_no}회 보너스 번호가 유효하지 않습니다")
        if self.numbers_draw_order is not None:
            order = tuple(int(x) for x in self.numbers_draw_order)
            if len(order) != DRAW_SIZE or set(order) != set(nums):
                raise ValueError("실제 추첨순서는 정렬 번호와 같은 6개로 구성되어야 합니다")

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["numbers_sorted"] = list(self.numbers_sorted)
        if self.numbers_draw_order is not None:
            out["numbers_draw_order"] = list(self.numbers_draw_order)
        return out

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DrawRecord":
        value = dict(value)
        value["numbers_sorted"] = tuple(value["numbers_sorted"])
        if value.get("numbers_draw_order") is not None:
            value["numbers_draw_order"] = tuple(value["numbers_draw_order"])
        return cls(**value)


def validate_draws(draws: Sequence[DrawRecord], *, require_contiguous: bool = True) -> list[DrawRecord]:
    ordered = sorted(draws, key=lambda d: d.draw_no)
    if len({d.draw_no for d in ordered}) != len(ordered):
        raise ValueError("중복 회차가 있습니다")
    if require_contiguous and ordered:
        expected = list(range(ordered[0].draw_no, ordered[-1].draw_no + 1))
        actual = [d.draw_no for d in ordered]
        if expected != actual:
            missing = sorted(set(expected) - set(actual))
            raise ValueError(f"회차가 연속적이지 않습니다. 누락: {missing[:20]}")
    return ordered


def load_draws_jsonl(path: str | Path, *, require_contiguous: bool = True) -> list[DrawRecord]:
    rows: list[DrawRecord] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(DrawRecord.from_dict(json.loads(line)))
            except Exception as exc:  # pragma: no cover - 메시지 품질 분기
                raise ValueError(f"{path}:{line_no} 파싱 실패: {exc}") from exc
    return validate_draws(rows, require_contiguous=require_contiguous)


def save_draws_jsonl(draws: Sequence[DrawRecord], path: str | Path) -> None:
    """완성된 원장을 임시 파일에서 교체해 중간 실패로 원본이 깨지지 않게 한다."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in validate_draws(draws):
            handle.write(canonical_json(row.to_dict()) + "\n")
    temp.replace(target)


def data_hash(draws: Sequence[DrawRecord]) -> str:
    essentials = [
        {"draw_no": d.draw_no, "draw_date": d.draw_date,
         "numbers_sorted": list(d.numbers_sorted), "bonus_number": d.bonus_number,
         "raw_data_hash": d.raw_data_hash}
        for d in validate_draws(draws)
    ]
    return sha256_json(essentials)


def estimated_latest_draw(today: date | None = None) -> int:
    """토요일 추첨이라는 공식 일정으로 후보를 만들고 수집기가 API로 다시 확인한다."""
    today = today or datetime.now(KST).date()
    if today < FIRST_DRAW_DATE:
        return 0
    return 1 + (today - FIRST_DRAW_DATE).days // 7


def indicator_matrix(draws: Sequence[DrawRecord]) -> np.ndarray:
    matrix = np.zeros((len(draws), NUMBER_COUNT), dtype=np.float64)
    for row, draw in enumerate(draws):
        matrix[row, np.asarray(draw.numbers_sorted, dtype=int) - 1] = 1.0
    return matrix


def elementary_symmetric(weights: Sequence[float], degree: int) -> float:
    """e_k(w): 독립 6회 추출이 아닌 비복원 부분집합 확률의 정규화 상수."""
    dp = np.zeros(degree + 1, dtype=np.float64)
    dp[0] = 1.0
    seen = 0
    for value in weights:
        seen += 1
        for k in range(min(degree, seen), 0, -1):
            dp[k] += float(value) * dp[k - 1]
    return float(dp[degree])


def _weights_from_eta(eta: Sequence[float]) -> np.ndarray:
    arr = np.asarray(eta, dtype=np.float64)
    if arr.shape != (NUMBER_COUNT,):
        raise ValueError("eta는 번호 45개의 점수여야 합니다")
    arr = np.clip(arr - np.mean(arr), -2.0, 2.0)
    return np.exp(arr)


def joint_log_probability(combo: Sequence[int], eta: Sequence[float]) -> float:
    combo = tuple(sorted(int(x) for x in combo))
    if len(combo) != DRAW_SIZE or len(set(combo)) != DRAW_SIZE:
        raise ValueError("조합은 서로 다른 6개 번호여야 합니다")
    weights = _weights_from_eta(eta)
    normalizer = elementary_symmetric(weights, DRAW_SIZE)
    return float(np.log(weights[np.asarray(combo) - 1]).sum() - math.log(normalizer))


def inclusion_probabilities(eta: Sequence[float]) -> np.ndarray:
    """P(i 포함)=w_i e_5(w_-i)/e_6(w)를 정확히 계산한다."""
    weights = _weights_from_eta(eta)
    normalizer = elementary_symmetric(weights, DRAW_SIZE)
    result = np.zeros(NUMBER_COUNT, dtype=np.float64)
    for i in range(NUMBER_COUNT):
        others = np.delete(weights, i)
        result[i] = weights[i] * elementary_symmetric(others, DRAW_SIZE - 1) / normalizer
    return result


def sample_product_weighted_subset(rng: np.random.Generator, eta: Sequence[float]) -> tuple[int, ...]:
    """P(S) ∝ product(w_i)인 정확한 가중 비복원 부분집합 샘플."""
    weights = _weights_from_eta(eta)
    suffix = np.zeros((NUMBER_COUNT + 1, DRAW_SIZE + 1), dtype=np.float64)
    suffix[:, 0] = 1.0
    for i in range(NUMBER_COUNT - 1, -1, -1):
        suffix[i] = suffix[i + 1]
        for k in range(1, DRAW_SIZE + 1):
            suffix[i, k] += weights[i] * suffix[i + 1, k - 1]

    chosen: list[int] = []
    need = DRAW_SIZE
    for i in range(NUMBER_COUNT):
        if need == 0:
            break
        left = NUMBER_COUNT - i
        if left == need:
            chosen.extend(range(i + 1, NUMBER_COUNT + 1))
            break
        denom = suffix[i, need]
        include_p = weights[i] * suffix[i + 1, need - 1] / denom
        if rng.random() < include_p:
            chosen.append(i + 1)
            need -= 1
    return tuple(chosen)


def _logit(probability: np.ndarray | float) -> np.ndarray | float:
    p = np.clip(probability, 1e-9, 1 - 1e-9)
    return np.log(p / (1 - p))


def beta_posterior(draws: Sequence[DrawRecord], prior_strength: float = 180.0) -> dict[str, np.ndarray]:
    """번호별 빈도를 전체 평균으로 축소하는 Beta-Binomial 주변모형."""
    if prior_strength <= 0:
        raise ValueError("prior_strength는 양수여야 합니다")
    counts = indicator_matrix(draws).sum(axis=0)
    alpha0 = UNIFORM_INCLUSION * prior_strength
    beta0 = (1 - UNIFORM_INCLUSION) * prior_strength
    alpha = alpha0 + counts
    beta = beta0 + len(draws) - counts
    mean = alpha / (alpha + beta)
    eta = np.asarray(_logit(mean) - _logit(UNIFORM_INCLUSION), dtype=np.float64)
    eta = np.clip(eta - eta.mean(), -1.5, 1.5)
    return {"alpha": alpha, "beta": beta, "mean": mean, "eta": eta, "counts": counts}


def persistent_state(draws: Sequence[DrawRecord], decay: float = 0.85) -> dict[str, Any]:
    """관측 가능한 C_t 후보만 추정한다. 잡음 범위의 rho는 정확히 0으로 축소한다."""
    matrix = indicator_matrix(draws)
    if len(matrix) < 30:
        return {"rho_raw": 0.0, "rho": 0.0, "state": np.zeros(NUMBER_COUNT), "noise_floor": 1.0}
    centered = matrix - matrix.mean(axis=0, keepdims=True)
    before, after = centered[:-1], centered[1:]
    denom = float(np.square(before).sum())
    raw = float((before * after).sum() / denom) if denom > 0 else 0.0
    noise_floor = 1.96 / math.sqrt(before.size)
    shrunk = math.copysign(max(0.0, abs(raw) - noise_floor), raw)
    shrunk = float(np.clip(shrunk, -0.25, 0.25))

    recent = matrix[-min(64, len(matrix)):] - UNIFORM_INCLUSION
    powers = decay ** np.arange(len(recent) - 1, -1, -1)
    powers /= powers.sum()
    state = np.average(recent, axis=0, weights=powers)
    state -= state.mean()
    return {"rho_raw": raw, "rho": shrunk, "state": state, "noise_floor": noise_floor}


def fit_candidate_model(draws: Sequence[DrawRecord], prior_strength: float = 180.0) -> dict[str, Any]:
    posterior = beta_posterior(draws, prior_strength)
    persistence = persistent_state(draws)
    scale = UNIFORM_INCLUSION * (1 - UNIFORM_INCLUSION)
    future_adjustment = persistence["rho"] * persistence["state"] / scale
    future_eta = np.clip(posterior["eta"] + future_adjustment, -1.5, 1.5)
    return {
        "bayes_eta": posterior["eta"],
        "future_eta": future_eta,
        "posterior": posterior,
        "rho_raw": persistence["rho_raw"],
        "rho": persistence["rho"],
        "rho_noise_floor": persistence["noise_floor"],
        "latent_state": persistence["state"],
    }


def _mean_ci(values: Sequence[float]) -> dict[str, float]:
    """확장창 예측손익의 시계열 의존을 Newey-West HAC 표준오차로 보정."""
    arr = np.asarray(values, dtype=np.float64)
    mean = float(arr.mean()) if len(arr) else 0.0
    if len(arr) <= 1:
        se, lag = float("inf"), 0
    else:
        residual = arr - mean
        lag = max(1, int(round(len(arr) ** (1 / 3))))
        long_run = float(np.dot(residual, residual) / len(arr))
        for offset in range(1, min(lag, len(arr) - 1) + 1):
            weight = 1.0 - offset / (lag + 1)
            covariance = float(np.dot(residual[offset:], residual[:-offset]) / len(arr))
            long_run += 2.0 * weight * covariance
        se = math.sqrt(max(long_run, 0.0) / len(arr))
    return {
        "mean": mean, "se": se, "hac_lag": lag,
        "lower_95": mean - 1.96 * se, "upper_95": mean + 1.96 * se,
    }


def walk_forward_backtest(
    draws: Sequence[DrawRecord], *, min_train: int = 300, prior_strength: float = 180.0,
) -> dict[str, Any]:
    """t-1까지만 보고 t를 평가한다. 통과하지 못한 모델은 운영에서 0 가중치다."""
    draws = validate_draws(draws)
    if len(draws) <= min_train:
        raise ValueError(f"워크포워드에는 최소 {min_train + 1}회가 필요합니다")
    zero_eta = np.zeros(NUMBER_COUNT, dtype=np.float64)
    uniform_inc = np.full(NUMBER_COUNT, UNIFORM_INCLUSION)
    series: dict[str, dict[str, list[float]]] = {
        "bayesian_bias": {"brier_gain": [], "log_gain": []},
        "future_constant": {"brier_gain": [], "log_gain": []},
    }
    eval_draws: list[int] = []

    for target_index in range(min_train, len(draws)):
        training = draws[:target_index]
        actual_draw = draws[target_index]
        actual = np.zeros(NUMBER_COUNT)
        actual[np.asarray(actual_draw.numbers_sorted) - 1] = 1.0
        baseline_brier = float(np.mean(np.square(uniform_inc - actual)))
        baseline_log = joint_log_probability(actual_draw.numbers_sorted, zero_eta)
        fitted = fit_candidate_model(training, prior_strength)
        for name, eta_key in (("bayesian_bias", "bayes_eta"), ("future_constant", "future_eta")):
            eta = fitted[eta_key]
            inc = inclusion_probabilities(eta)
            model_brier = float(np.mean(np.square(inc - actual)))
            model_log = joint_log_probability(actual_draw.numbers_sorted, eta)
            series[name]["brier_gain"].append(baseline_brier - model_brier)
            series[name]["log_gain"].append(model_log - baseline_log)
        eval_draws.append(actual_draw.draw_no)

    model_reports: dict[str, Any] = {}
    for name, values in series.items():
        brier = _mean_ci(values["brier_gain"])
        log_score = _mean_ci(values["log_gain"])
        middle = len(values["brier_gain"]) // 2
        recent_brier = float(np.mean(values["brier_gain"][middle:]))
        recent_log = float(np.mean(values["log_gain"][middle:]))
        passed = bool(
            brier["lower_95"] > 0 and log_score["lower_95"] > 0
            and recent_brier > 0 and recent_log > 0
        )
        model_reports[name] = {
            "brier_gain": brier,
            "joint_log_gain": log_score,
            "recent_half_brier_gain": recent_brier,
            "recent_half_log_gain": recent_log,
            "gate_passed": passed,
            "evaluations": len(values["brier_gain"]),
        }

    passed = [name for name, report in model_reports.items() if report["gate_passed"]]
    chosen = max(passed, key=lambda name: model_reports[name]["joint_log_gain"]["mean"]) if passed else "uniform"
    return {
        "method": "expanding_window_walk_forward",
        "data_cutoff_draw_no": draws[-1].draw_no,
        "evaluation_draw_range": [eval_draws[0], eval_draws[-1]],
        "min_train": min_train,
        "models": model_reports,
        "chosen_model": chosen,
        "operational_edge_weight": 1.0 if chosen != "uniform" else 0.0,
        "gate_rule": (
            "Brier와 조합 로그점수 개선의 95% 하한이 모두 0 초과이고 "
            "평가기간 후반부에서도 두 지표가 모두 같은 방향일 때만 채택"
        ),
    }


def _pair_counts(matrix: np.ndarray) -> np.ndarray:
    counts = matrix.T @ matrix
    np.fill_diagonal(counts, 0)
    return counts


def _lag1_correlations(matrix: np.ndarray) -> np.ndarray:
    if len(matrix) < 3:
        return np.zeros(NUMBER_COUNT)
    result = np.zeros(NUMBER_COUNT)
    for i in range(NUMBER_COUNT):
        x, y = matrix[:-1, i], matrix[1:, i]
        if x.std() > 0 and y.std() > 0:
            result[i] = float(np.corrcoef(x, y)[0, 1])
    return result


def _audit_statistics(matrix: np.ndarray) -> dict[str, float]:
    n = len(matrix)
    freq_z = (matrix.sum(axis=0) - n * UNIFORM_INCLUSION) / math.sqrt(
        n * UNIFORM_INCLUSION * (1 - UNIFORM_INCLUSION)
    )
    pair = _pair_counts(matrix)
    pair_z = (pair - n * PAIR_INCLUSION) / math.sqrt(n * PAIR_INCLUSION * (1 - PAIR_INCLUSION))
    triangle = np.triu_indices(NUMBER_COUNT, 1)
    lag = _lag1_correlations(matrix)
    return {
        "max_abs_frequency_z": float(np.max(np.abs(freq_z))),
        "max_abs_pair_z": float(np.max(np.abs(pair_z[triangle]))),
        "max_abs_lag1": float(np.max(np.abs(lag))),
    }


def randomness_audit(
    draws: Sequence[DrawRecord], *, simulations: int = 250, seed: int = 645,
) -> dict[str, Any]:
    """45개/990쌍을 본 뒤 가장 큰 값만 고르는 선택효과까지 Monte Carlo로 보정."""
    draws = validate_draws(draws)
    if simulations < 20:
        raise ValueError("무작위성 감사 시뮬레이션은 최소 20회가 필요합니다")
    matrix = indicator_matrix(draws)
    observed = _audit_statistics(matrix)
    rng = np.random.default_rng(seed)
    simulated = {key: [] for key in observed}
    for _ in range(simulations):
        null = np.zeros_like(matrix)
        for row in range(len(matrix)):
            null[row, rng.choice(NUMBER_COUNT, DRAW_SIZE, replace=False)] = 1.0
        stats = _audit_statistics(null)
        for key, value in stats.items():
            simulated[key].append(value)
    corrected_p = {
        key: (1 + sum(x >= observed[key] for x in values)) / (simulations + 1)
        for key, values in simulated.items()
    }
    compatible = all(value >= 0.05 for value in corrected_p.values())
    return {
        "null_model": "45개 중 균등 비복원 6개 추출",
        "draw_count": len(draws),
        "observed": observed,
        "familywise_monte_carlo_p": corrected_p,
        "simulations": simulations,
        "seed": seed,
        "uniform_compatible": compatible,
        "decision": (
            "균등모델과 구별되는 재현 가능한 증거 없음" if compatible
            else "탐색 신호 있음 — 독립된 미래 회차 재현 전에는 운영 반영 금지"
        ),
    }


def popularity_risk(combo: Sequence[int]) -> dict[str, Any]:
    """당첨확률이 아니라 다른 구매자와 번호가 겹칠 위험의 투명한 휴리스틱."""
    nums = tuple(sorted(int(x) for x in combo))
    reasons: list[str] = []
    score = 0.0
    birthday = sum(x <= 31 for x in nums)
    if birthday == DRAW_SIZE:
        score += 2.5
        reasons.append("전부 1~31 생일형")
    elif birthday >= 5:
        score += 0.8
        reasons.append("1~31 번호 편중")

    consecutive = sum(b == a + 1 for a, b in zip(nums, nums[1:]))
    if consecutive:
        score += 0.55 * consecutive
        reasons.append(f"연속 인접 {consecutive}쌍")
    run, longest = 1, 1
    for a, b in zip(nums, nums[1:]):
        run = run + 1 if b == a + 1 else 1
        longest = max(longest, run)
    if longest >= 3:
        score += 1.2
        reasons.append(f"{longest}개 연속수")

    endings = [sum(x % 10 == digit for x in nums) for digit in range(10)]
    if max(endings) >= 3:
        score += 0.7 * (max(endings) - 2)
        reasons.append("동일 끝수 과다")
    diffs = [b - a for a, b in zip(nums, nums[1:])]
    if len(set(diffs)) == 1:
        score += 2.0
        reasons.append("보기 좋은 등간격")

    rows = {(x - 1) // 7 for x in nums}
    cols = {(x - 1) % 7 for x in nums}
    if len(rows) == 1 or len(cols) == 1:
        score += 1.8
        reasons.append("용지 직선형")
    lucky_count = sum(x in {3, 7, 8, 9, 11, 12} for x in nums)
    if lucky_count >= 3:
        score += 0.35 * (lucky_count - 2)
        reasons.append("행운·기념 숫자 편중")
    return {"score": round(score, 4), "reasons": reasons}


def posterior_eta_samples(
    fitted: dict[str, Any], *, model_name: str, count: int, rng: np.random.Generator,
    shock_sd: float = 0.04,
) -> np.ndarray:
    """미래 충격은 평균 0으로만 샘플링한다. 예측 평균을 임의로 바꾸지 않는다."""
    posterior = fitted["posterior"]
    probability = rng.beta(posterior["alpha"], posterior["beta"], size=(count, NUMBER_COUNT))
    eta = _logit(probability) - _logit(UNIFORM_INCLUSION)
    eta -= eta.mean(axis=1, keepdims=True)
    if model_name == "future_constant":
        base_delta = fitted["future_eta"] - fitted["bayes_eta"]
        eta += base_delta
    # R + U: 예측 신호가 아니라 민감도/강건성만 흔든다.
    eta += rng.normal(0.0, shock_sd, size=eta.shape)
    eta -= eta.mean(axis=1, keepdims=True)
    return np.clip(eta, -2.0, 2.0)


def _score_candidates(
    candidates: Sequence[tuple[int, ...]], eta_samples: np.ndarray, *, edge_enabled: bool,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    if edge_enabled:
        normalizers = np.asarray([
            math.log(elementary_symmetric(_weights_from_eta(eta), DRAW_SIZE)) for eta in eta_samples
        ])
        weights_log = np.asarray([np.log(_weights_from_eta(eta)) for eta in eta_samples])
    for combo in candidates:
        if edge_enabled:
            logp = weights_log[:, np.asarray(combo) - 1].sum(axis=1) - normalizers
            robust_gain = float(np.quantile(logp - UNIFORM_LOG_PROBABILITY, 0.10))
            mean_gain = float(np.mean(logp - UNIFORM_LOG_PROBABILITY))
        else:
            robust_gain = mean_gain = 0.0
        popularity = popularity_risk(combo)
        scored.append({
            "numbers": list(combo),
            "robust_log_gain": robust_gain,
            "mean_log_gain": mean_gain,
            "popularity_risk": popularity,
        })
    return scored


def _select_diverse(scored: list[dict[str, Any]], ticket_count: int) -> list[dict[str, Any]]:
    remaining = sorted(
        scored,
        key=lambda row: row["robust_log_gain"] - 0.30 * row["popularity_risk"]["score"],
        reverse=True,
    )[: max(800, ticket_count * 80)]
    selected: list[dict[str, Any]] = []
    while remaining and len(selected) < ticket_count:
        best_index, best_value = 0, -float("inf")
        for idx, row in enumerate(remaining):
            nums = set(row["numbers"])
            min_distance = min((DRAW_SIZE - len(nums & set(old["numbers"])) for old in selected), default=DRAW_SIZE)
            value = (
                row["robust_log_gain"]
                - 0.30 * row["popularity_risk"]["score"]
                + 0.45 * min_distance
            )
            if value > best_value:
                best_index, best_value = idx, value
        chosen = remaining.pop(best_index)
        chosen["min_distance_from_previous"] = min(
            (DRAW_SIZE - len(set(chosen["numbers"]) & set(old["numbers"])) for old in selected),
            default=DRAW_SIZE,
        )
        selected.append(chosen)
    return selected


def generate_portfolio(
    draws: Sequence[DrawRecord], backtest: dict[str, Any], *, target_draw_no: int,
    budget_won: int = 5_000, seed_text: str, candidate_pool: int = 5_000,
    uncertainty_samples: int = 192,
) -> dict[str, Any]:
    draws = validate_draws(draws)
    ticket_count = budget_won // 1_000
    if not 1 <= ticket_count <= 100:
        raise ValueError("예산은 1,000원 이상 100,000원 이하의 1,000원 단위여야 합니다")
    if budget_won % 1_000:
        raise ValueError("예산은 1,000원 단위여야 합니다")
    seed_hex = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    rng = np.random.default_rng(int(seed_hex[:16], 16))
    fitted = fit_candidate_model(draws)
    chosen_model = backtest.get("chosen_model", "uniform")
    edge_enabled = chosen_model in {"bayesian_bias", "future_constant"}
    operational_eta = fitted["future_eta"] if chosen_model == "future_constant" else fitted["bayes_eta"]
    if not edge_enabled:
        operational_eta = np.zeros(NUMBER_COUNT)

    candidates: set[tuple[int, ...]] = set()
    attempts = 0
    while len(candidates) < candidate_pool and attempts < candidate_pool * 20:
        candidates.add(sample_product_weighted_subset(rng, operational_eta))
        attempts += 1
    if len(candidates) < ticket_count:
        raise RuntimeError("고유 후보 조합을 충분히 만들지 못했습니다")

    eta_samples = posterior_eta_samples(
        fitted, model_name=chosen_model, count=uncertainty_samples, rng=rng
    ) if edge_enabled else np.zeros((1, NUMBER_COUNT))
    # 균등복귀 때 모든 모델점수가 같으므로 사전식 정렬을 하면 낮은 번호가
    # 체계적으로 먼저 뽑힌다. 공개 시드 RNG로 동률만 해소한다.
    candidate_list = list(candidates)
    rng.shuffle(candidate_list)
    scored = _score_candidates(candidate_list, eta_samples, edge_enabled=edge_enabled)
    selected = _select_diverse(scored, ticket_count)
    inclusion = inclusion_probabilities(operational_eta)
    number_weights = [
        {"number": i + 1, "inclusion_probability": float(inclusion[i]),
         "uniform_probability": UNIFORM_INCLUSION}
        for i in range(NUMBER_COUNT)
    ]
    number_weights.sort(key=lambda row: row["inclusion_probability"], reverse=True)
    return {
        "target_draw_no": target_draw_no,
        "budget_won": budget_won,
        "ticket_price_won": 1_000,
        "ticket_count": ticket_count,
        "model_status": "validated_weighting" if edge_enabled else "uniform_fallback",
        "chosen_model": chosen_model,
        "operational_edge_weight": 1.0 if edge_enabled else 0.0,
        "rho_raw": float(fitted["rho_raw"]),
        "rho_after_noise_shrinkage": float(fitted["rho"]),
        "combinations": selected,
        "number_weights": number_weights,
        "jackpot_probability_if_fair": ticket_count / COMBINATION_COUNT,
        "seed": seed_text,
        "seed_sha256": seed_hex,
        "candidate_pool_size": len(candidates),
        "uncertainty_samples": int(len(eta_samples)),
        "notes": [
            "고유 조합 수만큼 1등 확률이 늘며 조합 간 거리 자체가 1등 확률을 더 올리지는 않습니다.",
            "인기 조합 회피는 공동당첨 위험을 낮추려는 휴리스틱이며 당첨확률을 높이지 않습니다.",
            "미래상수의 독립 충격 R과 미관측 상태 U는 예측점수가 아니라 불확실성 계산에만 사용합니다.",
        ],
    }


def make_preregistration(
    draws: Sequence[DrawRecord], portfolio: dict[str, Any], backtest: dict[str, Any],
    audit: dict[str, Any], *, generated_at: datetime | None = None, code_commit: str | None = None,
    model_code_hash: str | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(KST)
    payload: dict[str, Any] = {
        "target_draw_no": portfolio["target_draw_no"],
        "data_cutoff_draw_no": max(d.draw_no for d in draws),
        "model_version": MODEL_VERSION,
        "model_status": portfolio["model_status"],
        "chosen_model": portfolio["chosen_model"],
        "future_constant_seed": portfolio["seed"],
        "budget_won": portfolio["budget_won"],
        "combinations": [row["numbers"] for row in portfolio["combinations"]],
        "generated_at": generated_at.astimezone(KST).isoformat(timespec="seconds"),
        "data_hash": data_hash(draws),
        "backtest_hash": sha256_json(backtest),
        "audit_hash": sha256_json(audit),
        "code_commit": code_commit,
        "model_code_hash": model_code_hash,
        "prediction_hash": None,
    }
    payload["prediction_hash"] = sha256_json({k: v for k, v in payload.items() if k != "prediction_hash"})
    return payload


def write_preregistration(payload: dict[str, Any], directory: str | Path) -> Path:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(target_dir.glob(f"draw-{int(payload['target_draw_no']):04d}-*.json"))
    if existing:
        old = json.loads(existing[0].read_text(encoding="utf-8"))
        identity_fields = (
            "target_draw_no", "data_cutoff_draw_no", "model_version", "model_status",
            "chosen_model", "future_constant_seed", "budget_won", "combinations", "data_hash",
            "model_code_hash",
        )
        if all(old.get(key) == payload.get(key) for key in identity_fields):
            return existing[0]
        raise FileExistsError(
            f"{payload['target_draw_no']}회 사전등록이 이미 있습니다. "
            "실패 기록을 보존하기 위해 다른 예측으로 교체하지 않습니다: " + str(existing[0])
        )
    target = target_dir / f"draw-{int(payload['target_draw_no']):04d}-{payload['prediction_hash'][:12]}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def self_test() -> None:
    zero = np.zeros(NUMBER_COUNT)
    inc = inclusion_probabilities(zero)
    assert np.allclose(inc, UNIFORM_INCLUSION, atol=1e-12)
    assert math.isclose(joint_log_probability((1, 2, 3, 4, 5, 6), zero), UNIFORM_LOG_PROBABILITY)
    rng = np.random.default_rng(42)
    for _ in range(30):
        combo = sample_product_weighted_subset(rng, zero)
        assert len(combo) == DRAW_SIZE and len(set(combo)) == DRAW_SIZE
    assert popularity_risk((1, 2, 3, 4, 5, 6))["score"] > popularity_risk((4, 17, 26, 33, 41, 45))["score"]
    print("PASS lotto645: 정확한 비복원 확률 · 균등복귀 · 고유조합")


if __name__ == "__main__":
    self_test()
