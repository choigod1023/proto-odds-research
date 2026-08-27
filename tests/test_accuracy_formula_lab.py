import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from accuracy_formula_lab import (  # noqa: E402
    apply_design,
    date_block_interval,
    fit_design,
    fit_market_offset,
    predict_market_offset,
)
from bets import _WINNER  # noqa: E402
from generate_picks import (  # noqa: E402
    _is_recommendable_now,
    _sanitize_existing_document as sanitize_legacy_document,
)
from generate_v2 import (  # noqa: E402
    _hit,
    _remove_hindsight_prediction,
    _sanitize_prediction_document,
)


def test_large_ridge_returns_to_market_probability():
    market = np.array([0.55, 0.60, 0.45, 0.70])
    outcome = np.array([1.0, 0.0, 1.0, 1.0])
    design = np.column_stack([np.ones(4), np.array([1.0, -1.0, 2.0, -2.0])])

    beta = fit_market_offset(design, outcome, market, ridge=1e14)
    predicted = predict_market_offset(design, market, beta)

    np.testing.assert_allclose(predicted, market, atol=1e-10)


def test_design_uses_training_statistics_only():
    train = pd.DataFrame({"signal": [0.0, 2.0, np.nan]})
    future = pd.DataFrame({"signal": [1000.0, np.nan]})

    x_train, state = fit_design(train, ["signal"])
    x_future = apply_design(future, state)

    assert x_train.shape == (3, 3)
    assert state.medians == (1.0,)
    assert x_future[1, 1] == 0.0
    assert x_future[1, 2] == 1.0


def test_date_bootstrap_resamples_whole_days():
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02"]),
        "delta": [1.0, 1.0, -2.0],
    })
    interval = date_block_interval(frame, ["delta"], repeats=2000, seed=7)

    assert interval["delta"][0] <= frame["delta"].mean()
    assert interval["delta"][1] >= frame["delta"].mean()


def test_generate_v2_uses_the_canonical_winner_mapping():
    for (n_way, result), winner in _WINNER.items():
        for index in range(n_way):
            assert _hit(n_way, result, index) is (index == winner)


def test_unknown_result_is_never_marked_as_a_win():
    assert _hit(2, "알수없음", 0) is None
    assert _hit(2, "알수없음", 1) is None


def test_legacy_generator_never_recommends_finished_games():
    now = pd.Timestamp("2026-08-26T12:00:00+09:00").to_pydatetime()
    past = pd.Timestamp("2026-08-26T11:59:00+09:00").to_pydatetime()
    future = pd.Timestamp("2026-08-26T12:01:00+09:00").to_pydatetime()
    assert not _is_recommendable_now(None, past, now)
    assert not _is_recommendable_now("홈승", future, now)
    assert not _is_recommendable_now(None, None, now)
    assert _is_recommendable_now(None, future, now)


def test_legacy_artifact_sanitizer_keeps_only_future_games():
    doc = {
        "picks": [
            {"round": 100, "kickoff": "2026-08-27T00:30:00+09:00",
             "result": None, "hit": None},
            {"round": 101, "kickoff": "2026-08-27T02:30:00+09:00",
             "result": None, "hit": None},
        ],
        "tally": {"n": 1},
    }
    now = pd.Timestamp("2026-08-27T01:00:00+09:00").to_pydatetime()

    sanitize_legacy_document(doc, now)

    assert [pick["round"] for pick in doc["picks"]] == [101]
    assert doc["rounds"] == [101]
    assert doc["tally"] is None


def test_settled_game_without_ledger_cannot_keep_hindsight_recommendation():
    game = {
        "추천": {"선택": "홈"}, "홈승률": 0.7, "해설": "현재 자료 해설",
        "해설기본": "현재 자료 해설",
        "options": [{"모델확률": 0.7, "예상손익": 0.1, "괴리": 0.2,
                     "추천점수": 0.1, "제외": "없음", "시장확률": 0.6,
                     "적중": True}],
    }

    _remove_hindsight_prediction(game)

    assert game["추천"] is None
    assert game["prediction_status"] == "prediction_ledger_required"
    assert game["options"][0]["모델확률"] is None
    assert game["options"][0]["시장확률"] == 0.6
    assert game["options"][0]["적중"] is True


def test_started_game_is_expired_without_deleting_future_recommendation():
    def game(date: str) -> dict:
        return {
            "year": 2026, "round": 101, "date": date, "status": "경기전",
            "추천": {"선택": "홈"}, "홈승률": 0.6, "해설": "사전 해설",
            "해설기본": "사전 해설",
            "options": [{"모델확률": 0.6, "예상손익": -0.1, "괴리": 0.0,
                         "추천점수": 0.1, "제외": "없음"}],
        }

    started = game("08.27(목) 00:30")
    future = game("08.27(목) 02:30")
    doc = {"live": [started, future], "past": [], "tally": {"n": 1}}

    _sanitize_prediction_document(
        doc, pd.Timestamp("2026-08-27T01:00:00+09:00"))

    assert started["status"] == "결과확인"
    assert started["추천"] is None
    assert future["status"] == "경기전"
    assert future["추천"] == {"선택": "홈"}
    assert doc["tally"] is None
    assert doc["tally_status"] == "prediction_ledger_required"
