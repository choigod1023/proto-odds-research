import pytest

from src.internal_probability import baseball_player_delta, internal_probability


def _player(name, ops):
    return {"name": name, "position": "내야수", "stats": {"ops": ops}}


def _game(lineup_state="official_today"):
    return {
        "sport": "bs", "league": "KBO",
        "선발": {
            "source": "공식 경기 정보", "updated_at": "2026-08-27T08:00:00+09:00",
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
    result = internal_probability(_game(), {
        "market": "승패", "선택": "홈", "시장확률": .52, "모델확률": .55})
    assert result["basis"] == "internal-context-blend-v2"
    assert result["player_delta"] == pytest.approx(.10)
    assert result["final"] == pytest.approx(.611)


def test_projected_lineup_is_discounted_and_away_delta_is_symmetric():
    home = {"market": "승패", "선택": "홈", "시장확률": .50, "모델확률": .50}
    away = {"market": "승패", "선택": "원정", "시장확률": .50, "모델확률": .50}
    game = _game("projected_from_recent_official")
    home_delta, _ = baseball_player_delta(game, home)
    away_delta, _ = baseball_player_delta(game, away)
    assert home_delta == pytest.approx(-away_delta)
    assert 0 < home_delta < .10


def test_missing_provenance_or_pitcher_metrics_fail_closed():
    game = _game()
    game["선발"].pop("updated_at")
    result = internal_probability(game, {
        "market": "승패", "선택": "홈", "시장확률": .50, "모델확률": .60})
    assert result["status"] == "ineligible"
    assert result["reason"] == "player_provenance_missing"
    assert result["final"] == .50


@pytest.mark.parametrize("league", ["MLB", "KBO", "NPB"])
def test_supported_baseball_leagues_share_activation_contract(league):
    game = _game()
    game["league"] = league
    result = internal_probability(game, {
        "market": "승패", "선택": "홈", "시장확률": .50, "모델확률": .50})
    assert result["status"] == "operational"
