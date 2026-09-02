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
    assert result["player_delta"] == pytest.approx(.0989)
    assert result["final"] == pytest.approx(.6102)


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


def test_starter_metrics_must_use_the_same_unit():
    game = _game()
    game["선발"]["home_detail"]["stats"] = {"xfip": 2.8}
    game["선발"]["away_detail"]["stats"] = {"fip": 4.8}

    result = internal_probability(game, {
        "market": "승패", "선택": "홈", "시장확률": .50, "모델확률": .60})

    assert result["status"] == "ineligible"
    assert result["reason"] == "comparable_starting_pitcher_metric_required"
    assert result["final"] == .50


def test_availability_confidence_scales_the_probability_delta():
    game = _game()
    game["선발"]["lineups"] = {}
    game["선발"]["home_detail"]["stats"] = {"fip": 3.0}
    game["선발"]["away_detail"]["stats"] = {"fip": 3.0}

    delta, factors = baseball_player_delta(game, {
        "market": "승패", "선택": "홈", "시장확률": .50, "모델확률": .50})

    availability = next(row for row in factors if row["id"] == "availability")
    assert delta == pytest.approx(.0039)
    assert availability["confidence"] == .65
    assert availability["home_probability_delta"] == pytest.approx(.0039)


@pytest.mark.parametrize("status, expected", [
    (True, 1.0),
    ({"state": "confirmed"}, 1.0),
    ({"state": "announced"}, 0.8),
])
def test_starter_status_legacy_and_announced_confidence(status, expected):
    game = _game()
    game["선발"]["starter_status"] = status

    _, factors = baseball_player_delta(game, {
        "market": "승패", "선택": "홈", "시장확률": .50, "모델확률": .50})

    pitcher = next(row for row in factors if row["id"] == "starting_pitcher")
    assert pitcher["confidence"] == expected


@pytest.mark.parametrize("league", ["MLB", "KBO", "NPB"])
def test_supported_baseball_leagues_share_activation_contract(league):
    game = _game()
    game["league"] = league
    result = internal_probability(game, {
        "market": "승패", "선택": "홈", "시장확률": .50, "모델확률": .50})
    assert result["status"] == "operational"
