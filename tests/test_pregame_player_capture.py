from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from src.pregame_player_capture import capture
from src.prediction_runtime import ledger_features
from src.live_market_refresh import refresh_document
from test_live_market_refresh import _live_odds


NOW = "2026-08-30T01:00:00+00:00"
START = "2026-08-30T09:00:00+00:00"


def fixture():
    game = dict(league="KBO", home="KIA", away="SSG")
    doc = {"games": [dict(league="KBO", home_team="KIA", away_team="SSG",
        game_datetime=START, game_id="kbo1", source="official", updated_at=NOW,
        starters={"home": {"name": "A"}, "away": {"name": "B"}},
        lineups={"home": [{"name": "C"}], "away": [{"name": "D"}]})]}
    return game, doc


def test_capture_is_independent_and_not_training_approval():
    game, doc = fixture()
    result = capture(game, doc, observed_at=NOW, kickoff=START)
    assert result["status"] == "captured"
    assert not result["training_eligible"] and not result["affects_probability"]
    doc["games"][0]["starters"]["home"]["name"] = "changed"
    assert result["starters"]["home"]["name"] == "A"
    game["research_player_inputs"] = result
    assert ledger_features(game)["research_player_inputs"] == result


def test_wrong_fixture_ambiguous_future_stale_and_late_rejected():
    game, doc = fixture()
    for field, value, reason in [
        ("home_team", "alias", "missing_or_ambiguous_fixture"),
        ("updated_at", "2026-08-30T02:00:00Z", "missing_or_future_provenance"),
        ("updated_at", "2026-08-29T00:00:00Z", "stale_player_context"),
        ("updated_at", "2026-08-30T01:00:00", "missing_or_future_provenance")]:
        bad = deepcopy(doc)
        bad["games"][0][field] = value
        assert capture(game, bad, observed_at=NOW, kickoff=START)["reason"] == reason
    doc["games"].append(deepcopy(doc["games"][0]))
    assert capture(game, doc, observed_at=NOW, kickoff=START)["status"] == "unavailable"
    assert capture(game, {}, observed_at="2026-08-30T08:30:00Z", kickoff=START)["reason"] == "not_before_t30"


def test_refresh_preserves_operating_decision_and_pinned_capture():
    _, players = fixture()
    plain, _ = refresh_document({"live": []}, _live_odds())
    doc, _ = refresh_document({"live": []}, _live_odds(), player_document=players)
    game = doc["live"][0]
    assert game["decision_snapshot"]["probability"] == plain["live"][0]["decision_snapshot"]["probability"]
    original = deepcopy(game["research_player_inputs"])
    game["prediction_status"] = "recorded_pregame"
    game["prediction_record"] = {"selection": "홈"}
    odds = _live_odds()
    odds["generated_at"] = "2026-08-30T01:10:00+00:00"
    odds["markets"]["102"]["7100"]["odds"] = [1.6, 2.0]
    players["games"][0]["starters"]["home"]["name"] = "new"
    refresh_document(doc, odds, player_document=players)
    assert game["research_player_inputs"] == original
