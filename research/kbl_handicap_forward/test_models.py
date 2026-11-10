from datetime import datetime
import math
from pathlib import Path

import pytest

from research.kbl_handicap_forward import models


@pytest.fixture
def game():
    return {
        "id": "S48G01N1",
        "kickoff": "2026-11-11T19:00:00+09:00",
        "phase": "playoff",
        "home_id": "10",
        "away_id": "35",
        "home_name": "울산모비",
        "away_name": "서울삼성",
        "home_rest": 8,
        "away_rest": 1,
    }


@pytest.fixture
def quote():
    return {"offer_id": "test:offer", "line": -5.5, "odds": [1.8, 2.1]}


def test_frozen_fit_probabilities_and_selected_prices(game, quote):
    result = models.forecasts(game, quote)
    assert set(result) == {"frozen", "static", "phase_rest"}
    p_market = 2.1 / (1.8 + 2.1)
    offset = math.log(p_market / (1 - p_market))
    adjustments = {
        "static": -0.14679882364477803,
        "phase_rest": -0.12929269795933854 - 0.2869827849126661 + 0.8 * 0.03765706772348242,
    }
    for name, adjustment in adjustments.items():
        assert result[name]["home_probability"] == pytest.approx(1 / (1 + math.exp(-offset - adjustment)))
        assert result[name]["artifact_sha256"] == models.SEED_SHA256
    assert (result["static"]["selection"], result["static"]["odds"]) == ("home", 1.8)
    assert (result["phase_rest"]["selection"], result["phase_rest"]["odds"]) == ("away", 2.1)


def test_rest_cap_and_explicit_missing_rest(game, quote):
    regular = dict(game, phase="regular")
    capped = models.forecasts(dict(regular, home_rest=5), quote)
    assert models.forecasts(regular, quote) == capped
    assert models.forecasts(dict(regular, home_rest=None), quote) == models.forecasts(
        dict(regular, home_rest=1), quote
    )


@pytest.mark.parametrize("line", [-10.5, 10.5])
def test_frozen_matches_current_league_candidate(game, quote, line, monkeypatch):
    monkeypatch.syspath_prepend(str(models.FROZEN_PATH.parents[2] / "src"))
    import league_handicap

    quote["line"] = line
    offers = {
        side: {"배당": price, "selection_id": side, "offer_id": quote["offer_id"]}
        for side, price in zip(("핸디홈", "핸디원정"), quote["odds"], strict=True)
    }
    arm = league_handicap.recommend(
        {"sport": "bk", "league": "KBL", "period": "full_including_extra",
         "settlement_rule": "handicap_half", "market": "handicap", "line": line},
        {"home": game["home_name"], "away": game["away_name"]},
        offers,
        price_time=datetime.fromisoformat(game["kickoff"]),
    )
    home = arm["selection"] == "핸디홈"
    assert models.forecasts(game, quote)["frozen"] == {
        "home_probability": arm["probability"] if home else 1 - arm["probability"],
        "selection": "home" if home else "away",
        "odds": arm["odds"],
        "artifact_sha256": arm["model_artifact_sha256"],
    }


def test_training_cutoff_uses_kst_calendar_day(game, quote):
    with pytest.raises(ValueError, match="training cutoffs"):
        models.forecasts(dict(game, kickoff="2026-05-13T14:59:59Z"), quote)
    assert models.forecasts(dict(game, kickoff="2026-05-13T15:00:00Z"), quote)


@pytest.mark.parametrize(
    ("record", "key", "value"),
    [
        ("game", "kickoff", "2026-11-11T19:00:00"),
        ("game", "phase", "preseason"),
        ("game", "home_id", "35"),
        ("game", "home_id", "50"),
        ("game", "away_id", "unknown"),
        ("game", "home_rest", -1),
        ("game", "away_rest", True),
        ("quote", "line", -5),
        ("quote", "line", math.inf),
        ("quote", "odds", [1.8, 1]),
        ("quote", "odds", [1.8, math.nan]),
        ("quote", "odds", [1.8, 2.1, 3.4]),
        ("quote", "offer_id", ""),
    ],
)
def test_invalid_input_fails_closed(game, quote, record, key, value):
    (game if record == "game" else quote)[key] = value
    with pytest.raises(ValueError):
        models.forecasts(game, quote)


def test_missing_rest_field_fails_closed(game, quote):
    del game["home_rest"]
    with pytest.raises(ValueError, match="rest fields"):
        models.forecasts(game, quote)


@pytest.mark.parametrize("path", [models.SEED_PATH, models.FROZEN_PATH])
def test_artifact_is_verified_on_every_call(game, quote, path, monkeypatch):
    assert models.forecasts(game, quote)
    read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: read_bytes(p) + b" " if p == path else read_bytes(p))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        models.forecasts(game, quote)
