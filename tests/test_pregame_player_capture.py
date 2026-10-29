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
    odds["generated_at"] = "2026-08-30T08:30:00+00:00"
    odds["markets"]["102"]["7100"]["odds"] = [1.6, 2.0]
    players["games"][0]["starters"]["home"]["name"] = "new"
    refresh_document(doc, odds, player_document=players)
    assert game["research_player_inputs"] == original


def test_unchanged_kbo_context_does_not_retimestamp_old_decision_inputs():
    from datetime import datetime
    _,players=fixture()
    doc,_=refresh_document({'live':[]},_live_odds(),player_document=players,
                           now=datetime.fromisoformat(NOW))
    original=deepcopy(doc['live'][0]['research_player_inputs'])
    decision=deepcopy(doc['live'][0]['decision_snapshot'])
    doc,changed=refresh_document(doc,_live_odds(),player_document=players,
                                now=datetime.fromisoformat('2026-08-30T01:01:00+00:00'))
    assert changed==0
    assert doc['live'][0]['research_player_inputs']==original
    assert doc['live'][0]['decision_snapshot']==decision


def test_capture_finishing_later_advances_new_asof_and_preserves_market_clock(monkeypatch):
    from datetime import datetime
    import pregame_player_capture as module
    real=module.capture
    def later(*args,**kwargs):
        result=real(*args,**kwargs)
        result['observed_at']='2026-08-30T01:00:01+00:00'
        return result
    monkeypatch.setattr(module,'capture',later)
    _,players=fixture()
    doc,_=refresh_document({'live':[]},_live_odds(),player_document=players,
                           now=datetime.fromisoformat(NOW))
    game=doc['live'][0]
    assert datetime.fromisoformat(game['decision_snapshot']['as_of'])>=datetime.fromisoformat(game['research_player_inputs']['observed_at'])
    assert game['live_market_observed_at']==NOW


def test_candidate_source_is_propagated_only_for_exact_available_fixture():
    game,doc=fixture()
    doc['games'][0]['game_id']='123'
    inp=dict(status='captured',gamePk=123,start_at=START,observed_at=NOW,
             response_sha256='raw-sha',sides={'home':{},'away':{}})
    doc['games'][0]['research_candidate_input']=inp
    result=capture(game,doc,observed_at=NOW,kickoff=START)
    assert result['candidate_input']==inp
    for key,value in [('gamePk',124),('observed_at','2026-08-30T01:01:00Z'),('start_at','2026-08-31T09:00:00Z')]:
        bad=deepcopy(doc);bad['games'][0]['research_candidate_input'][key]=value
        out=capture(game,bad,observed_at=NOW,kickoff=START)
        assert 'candidate_input' not in out


def test_new_mlb_candidate_input_refreshes_with_unchanged_prices_but_keeps_freeze():
    from datetime import datetime
    odds=_live_odds()
    for row in odds['markets']['102'].values():row['league']='MLB'
    _,players=fixture()
    players['games'][0].update(league='MLB',game_id='123')
    players['games'][0]['research_candidate_input']=dict(status='captured',gamePk=123,start_at=START,
                                                        observed_at=NOW,response_sha256='one')
    doc,_=refresh_document({'live':[]},odds,player_document=players,now=datetime.fromisoformat(NOW))
    first=doc['live'][0]['decision_snapshot']['decision_id']
    players['games'][0]['research_candidate_input'].update(observed_at='2026-08-30T01:01:00Z',response_sha256='two')
    doc,changed=refresh_document(doc,odds,player_document=players,
                                now=datetime.fromisoformat('2026-08-30T01:01:01+00:00'))
    assert changed==1
    assert doc['live'][0]['decision_snapshot']['decision_id']!=first
    assert doc['live'][0]['research_player_inputs']['candidate_input']['response_sha256']=='two'
    frozen=deepcopy(doc['live'][0])
    players['games'][0]['research_candidate_input']['response_sha256']='three'
    doc,changed=refresh_document(doc,odds,player_document=players,
                                now=datetime.fromisoformat('2026-08-30T08:30:00+00:00'))
    assert changed==0 and doc['live'][0]==frozen
