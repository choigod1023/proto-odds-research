from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lotto645 import (  # noqa: E402
    COMBINATION_COUNT,
    KST,
    UNIFORM_INCLUSION,
    UNIFORM_LOG_PROBABILITY,
    DrawRecord,
    generate_portfolio,
    inclusion_probabilities,
    joint_log_probability,
    make_preregistration,
    popularity_risk,
    randomness_audit,
    sample_product_weighted_subset,
    walk_forward_backtest,
    write_preregistration,
)
from lotto_collect import parse_official_item  # noqa: E402


def synthetic_draws(count: int, seed: int = 42) -> list[DrawRecord]:
    rng = np.random.default_rng(seed)
    draws = []
    for index in range(count):
        numbers = tuple(sorted(int(x) + 1 for x in rng.choice(45, 6, replace=False)))
        bonus = next(x for x in range(1, 46) if x not in numbers)
        draws.append(DrawRecord(
            draw_no=index + 1,
            draw_date=f"2020-01-{(index % 28) + 1:02d}",
            numbers_sorted=numbers,
            bonus_number=bonus,
            source_url="fixture",
            raw_data_hash=f"hash-{index + 1}",
        ))
    return draws


def test_uniform_subset_math_is_exact():
    eta = np.zeros(45)
    probabilities = inclusion_probabilities(eta)
    assert probabilities.sum() == pytest.approx(6.0, abs=1e-12)
    assert probabilities == pytest.approx(np.full(45, UNIFORM_INCLUSION), abs=1e-12)
    assert joint_log_probability((1, 2, 3, 4, 5, 6), eta) == pytest.approx(UNIFORM_LOG_PROBABILITY)
    assert math.exp(UNIFORM_LOG_PROBABILITY) == pytest.approx(1 / COMBINATION_COUNT)


def test_weighted_subset_favors_high_score_without_duplicates():
    eta = np.zeros(45)
    eta[0] = 1.0
    assert joint_log_probability((1, 2, 3, 4, 5, 6), eta) > joint_log_probability((2, 3, 4, 5, 6, 7), eta)
    rng = np.random.default_rng(7)
    samples = [sample_product_weighted_subset(rng, eta) for _ in range(100)]
    assert all(len(row) == 6 and len(set(row)) == 6 for row in samples)


def test_draw_validation_rejects_duplicate_and_bad_bonus():
    with pytest.raises(ValueError):
        DrawRecord(1, "2020-01-01", (1, 1, 2, 3, 4, 5), 6)
    with pytest.raises(ValueError):
        DrawRecord(1, "2020-01-01", (1, 2, 3, 4, 5, 6), 6)


def test_official_parser_does_not_invent_draw_order():
    item = {
        "ltEpsd": 1238, "ltRflYmd": "20260822",
        "tm1WnNo": 2, "tm2WnNo": 13, "tm3WnNo": 18,
        "tm4WnNo": 32, "tm5WnNo": 38, "tm6WnNo": 42, "bnsWnNo": 22,
        "wholEpsdSumNtslAmt": 114537798000,
        **{f"rnk{i}WnNope": i for i in range(1, 6)},
        **{f"rnk{i}WnAmt": i * 1000 for i in range(1, 6)},
    }
    row = parse_official_item(item, source_url="official", collected_at="now")
    assert row.numbers_sorted == (2, 13, 18, 32, 38, 42)
    assert row.numbers_draw_order is None
    assert row.verification_status["numbers_draw_order"] == "unavailable_not_inferred"


def test_walk_forward_and_uniform_fallback_portfolio():
    draws = synthetic_draws(105)
    backtest = walk_forward_backtest(draws, min_train=70)
    # 이 고정 시드의 공정 표본에서 엄격한 동시 관문은 통과하지 않아야 한다.
    assert backtest["chosen_model"] == "uniform"
    portfolio = generate_portfolio(
        draws, backtest, target_draw_no=106, budget_won=10_000,
        seed_text="fixed-public-seed", candidate_pool=350, uncertainty_samples=20,
    )
    assert portfolio["model_status"] == "uniform_fallback"
    combos = [tuple(row["numbers"]) for row in portfolio["combinations"]]
    assert len(combos) == len(set(combos)) == 10
    assert all(row["inclusion_probability"] == pytest.approx(UNIFORM_INCLUSION)
               for row in portfolio["number_weights"])


def test_randomness_audit_controls_maximum_statistics():
    report = randomness_audit(synthetic_draws(80), simulations=20, seed=9)
    assert set(report["familywise_monte_carlo_p"]) == {
        "max_abs_frequency_z", "max_abs_pair_z", "max_abs_lag1"
    }
    assert all(0 < p <= 1 for p in report["familywise_monte_carlo_p"].values())


def test_preregistration_is_hashed_and_idempotent(tmp_path: Path):
    draws = synthetic_draws(12)
    portfolio = {
        "target_draw_no": 13, "model_status": "uniform_fallback", "chosen_model": "uniform",
        "seed": "public", "budget_won": 2000,
        "combinations": [{"numbers": [1, 8, 19, 32, 37, 44]},
                         {"numbers": [4, 12, 22, 33, 41, 45]}],
    }
    backtest = {"chosen_model": "uniform"}
    audit = {"uniform_compatible": True}
    when = datetime(2026, 8, 22, 18, 0, tzinfo=KST)
    prereg = make_preregistration(draws, portfolio, backtest, audit, generated_at=when)
    assert len(prereg["prediction_hash"]) == 64
    path = write_preregistration(prereg, tmp_path)
    assert write_preregistration(prereg, tmp_path) == path
    assert json.loads(path.read_text(encoding="utf-8"))["prediction_hash"] == prereg["prediction_hash"]


def test_popularity_risk_is_separate_from_win_probability():
    popular = popularity_risk((1, 2, 3, 4, 5, 6))
    dispersed = popularity_risk((4, 17, 26, 33, 41, 45))
    assert popular["score"] > dispersed["score"]
    assert popular["reasons"]
