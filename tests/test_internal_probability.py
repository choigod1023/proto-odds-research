import pytest

from src.internal_probability import baseball_player_delta, internal_probability


def _player(name, ops):
    return {"name": name, "position": "내야수", "stats": {"ops": ops}}


def _game(lineup_state="official_today"):
    return {
        "sport": "bs",
        "선발": {
            "home_detail": {"name": "홈선발", "stats": {"fip": 2.8}},
            "away_detail": {"name": "원정선발", "stats": {"fip": 4.8}},
            "starter_status": {"state": "confirmed"},
            "lineups": {
                "home": [_player(str(i), .800) for i in range(9)],
                "away": [_player(str(i), .650) for i in range(9)],
            },
            "lineup_status": {"state": lineup_state},
            "unavailable": {"home": [], "away": [{"name": "결장"}]},
        },
    }


def test_confirmed_starter_and_lineup_move_baseball_probability():
    option = {"market": "승패", "선택": "홈", "시장확률": .52, "모델확률": .55}
    result = internal_probability(_game(), option)

    assert result["basis"] == "internal_context_blend_v1"
    assert result["player_delta"] == pytest.approx(.10)
    assert result["final"] == pytest.approx(.611)
    assert [row["id"] for row in result["factors"]] == [
        "starting_pitcher", "batting_lineup", "availability"]


def test_projected_lineup_is_discounted_and_away_delta_is_symmetric():
    home = {"market": "승패", "선택": "홈", "시장확률": .50, "모델확률": .50}
    away = {"market": "승패", "선택": "원정", "시장확률": .50, "모델확률": .50}
    projected = _game("projected_from_recent_official")

    home_delta, _ = baseball_player_delta(projected, home)
    away_delta, _ = baseball_player_delta(projected, away)
    assert home_delta == pytest.approx(-away_delta)
    assert 0 < home_delta < .10


def test_non_baseball_market_uses_structured_model_without_player_guess():
    result = internal_probability({}, {
        "market": "승무패", "선택": "홈", "시장확률": .50, "모델확률": .60})
    assert result["player_delta"] == 0
    assert result["final"] == pytest.approx(.57)
