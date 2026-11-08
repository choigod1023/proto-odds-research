from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from featured_pick import VERSION, performance
from prediction_runtime import PredictionRuntime


def record(sport, market, result, odds="1.72", source="market_fallback"):
    grade = None if result is None else {"result": result, "net_units": {"hit": "0.72", "miss": "-1", "void": "0"}[result]}
    return {"fixture": {"sport": sport, "league": "test"}, "pick": {"market": market, "odds": float(odds)},
            "selection_source": source, "result": grade}


def test_all_breakdowns_preserve_the_whole_ledger_cohort_and_refunds():
    records = {"a": record("bk", "승패", "hit"), "b": record("bk", "언더오버", "miss"),
               "c": record("vl", "승패", "void"), "d": record("bs", "핸디캡", None),
               "legacy": {"pick": {}, "result": None}}
    before = deepcopy(records)
    result = performance(records)
    assert (result["captured"], result["settled"], result["decided"], result["refunds"]) == (5, 3, 2, 1)
    assert result["hit_rate"] == .5
    assert abs(result["roi"] - (-.28 / 3)) < 1e-12
    for field in ("by_sport", "by_league", "by_market", "by_selection_source", "by_policy"):
        assert sum(group["captured"] for group in result[field].values()) == result["captured"]
        assert sum(group["decided"] for group in result[field].values()) == result["decided"]
    assert result["by_sport"]["unknown"]["pending"] == 1
    assert result["by_sport"]["bk"]["hit_rate"] == .5
    assert result["by_sport"]["vl"]["hit_rate"] is None
    assert result["by_sport"]["vl"]["roi"] == 0
    assert result["selection_rule"]["validated_edge"] is False
    assert records == before


def test_empty_record_set_does_not_invent_zero_accuracy():
    result = performance({})
    assert result["hit_rate"] is None and result["roi"] is None
    assert result["by_sport"] == {} and result["by_market"] == {}


def test_projection_uses_saved_source_and_exact_product_without_rewriting_history(tmp_path, monkeypatch):
    runtime = PredictionRuntime(tmp_path / "ledger.jsonl")
    pick = dict(version=VERSION, selection_id="s", offer_id="o", market="언더오버",
                label="U 9.5", line=9.5, odds=1.8, settlement_rule="total_half")
    prediction = dict(snapshot_id="saved", kickoff="2026-11-08T10:00:00+00:00",
        captured_at="2026-11-08T08:00:00+00:00", as_of="2026-11-08T08:00:00+00:00",
        market_observed_at="2026-11-08T08:00:00+00:00",
        features={"fixture": {"sport": "bs", "league": "KBO"}}, model={"selection_policy": "tipster-vote-v1"},
        predictions={"featured_pick": pick, "market_recommendations": {"items": [
            dict(pick, model="market-baseline-v1", selection_source="market_fallback_no_exact_line_votes")
        ]}})
    monkeypatch.setattr(runtime, "_latest_for_events", lambda events: [("event", prediction)])
    monkeypatch.setattr(runtime, "_cached_settlements", lambda: {})
    before = deepcopy(prediction)
    projected = runtime.featured_records()["event"]
    assert projected["fixture"] == {"sport": "bs", "league": "KBO"}
    assert projected["selection_source"] == "market_fallback_no_exact_line_votes"
    assert projected["selection_model"] == "market-baseline-v1"
    assert prediction == before
    prediction["predictions"]["market_recommendations"]["items"][0]["line"] = 8.5
    assert runtime.featured_records()["event"]["selection_source"] == "unknown"
    # Version/status alone cannot make an authorless fallback a tipster vote.
    pick["market"] = "승패"
    prediction["features"]["tipster_hybrid"] = dict(status="applied", authors=0, selection_source="market_fallback")
    assert runtime.featured_records()["event"]["selection_source"] == "market_fallback"
    prediction["features"]["tipster_hybrid"] = dict(status="applied", authors=1)
    assert runtime.featured_records()["event"]["selection_source"] == "tipster_vote"
    prediction["features"]["tipster_hybrid"]["authors"] = None
    assert runtime.featured_records()["event"]["selection_source"] == "unknown"
    prediction["features"]["tipster_hybrid"]["authors"] = 1
    prediction["model"]["selection_policy"] = "market90-tipster10-v2"
    assert runtime.featured_records()["event"]["selection_source"] == "unknown"
