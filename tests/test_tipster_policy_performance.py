import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tipster_policy_performance import audit, classify

NOW = "2026-11-04T02:00:00+00:00"


def prediction(event="a", sequence=1, odds=2.0):
    return dict(record_type="prediction", event_id=event, snapshot_id=f"s{sequence}",
        ledger_sequence=sequence, kickoff="2026-11-03T10:00:00Z",
        as_of="2026-11-03T08:00:00Z", captured_at="2026-11-03T08:00:00Z",
        market_observed_at="2026-11-03T07:55:00Z",
        model=dict(selection_policy="tipster-vote-v1", operating_version="tipster-vote-v1",
                   affects_selection=True, affects_probability=False, status="operational",
                   policy_authorized=True, promotion_gate="user_authorized_unvalidated",
                   validated_edge=False),
        predictions=dict(action="market_reference", selection="홈", selection_id="home", odds=odds),
        features=dict(fixture=dict(sport="sc"), tipster_hybrid=dict(version="tipster-vote-v1",
            status="applied", authors=1, selected_side="홈", vote_counts={"홈": 1, "무": 0, "원정": 0})))


def settlement(pred, result="hit", sequence=9):
    return dict(record_type="settlement", snapshot_id=pred["snapshot_id"], ledger_sequence=sequence,
                settled_at="2026-11-03T12:00:00Z", outcome=dict(result=result, selection_id="home"))


def test_real_votes_are_not_inflated_by_fallback_or_other_policies():
    vote, fallback, old = prediction(), prediction("b", 2), prediction("c", 3)
    fallback["features"]["tipster_hybrid"] = dict(selection_source="market_fallback", authors=0)
    old["model"]["selection_policy"] = "market90-tipster10-v2"
    result = audit([vote, fallback, old, settlement(vote), settlement(fallback), settlement(old)], evaluated_at=NOW)
    assert result["cohorts"]["actual_tipster_vote"]["decided"] == 1
    assert result["cohorts"]["market_fallback"]["decided"] == 1
    assert result["classification_counts"]["other_policy"] == 1


def test_roi_uses_saved_odds_and_excludes_void_pending():
    hit, miss, void, pending = [prediction(str(i), i, 1.5) for i in range(1, 5)]
    result = audit([hit, miss, void, pending, settlement(hit), settlement(miss, "miss", 10),
                    settlement(void, "void", 11)], evaluated_at=NOW)["cohorts"]["actual_tipster_vote"]
    assert (result["hit"], result["miss"], result["void"], result["pending"]) == (1, 1, 1, 1)
    assert result["accuracy_percent"] == 50
    assert result["equal_stake_roi_percent"] == -25
    assert result["hypothetical_net_won"] == "-5000.0"


def test_later_abstention_cannot_resurrect_a_winning_vote():
    old = prediction()
    newer = copy.deepcopy(old)
    newer.update(snapshot_id="s2", ledger_sequence=2, as_of="2026-11-03T08:10:00Z")
    newer["predictions"] = dict(action="withhold")
    result = audit([newer, old, settlement(old)], evaluated_at=NOW)
    assert result["cohorts"]["actual_tipster_vote"]["events"] == 0
    assert result["classification_counts"]["withheld"] == 1


def test_tied_or_inconsistent_votes_and_late_snapshots_are_not_counted():
    tied = prediction()
    tied["features"]["tipster_hybrid"].update(authors=2, vote_counts={"홈": 1, "원정": 1})
    assert classify(tied) == "unverified_vote_selection"
    late = prediction()
    late["captured_at"] = "2026-11-03T09:30:00Z"
    result = audit([late, settlement(late)], evaluated_at=NOW)
    assert result["holds"][0]["reason"] == "not_before_t30"
    assert result["cohorts"]["actual_tipster_vote"]["decided"] == 0


def test_settlement_must_belong_to_the_exact_selected_offer():
    pred = prediction()
    wrong = settlement(pred)
    wrong["outcome"]["selection_id"] = "away"
    result = audit([pred, wrong], evaluated_at=NOW)
    assert result["holds"][0]["reason"] == "invalid_or_future_settlement"
