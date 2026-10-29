"""Exercise real operating decisions plus corrupt/late historical revisions."""
from copy import deepcopy
from datetime import datetime
import json
import gzip
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ai_decision import build_decision_snapshot
from operating_ledger_inputs import latest_pre_cutoff, normalize_operating_prediction
from prediction_runtime import ledger_features, prediction_payload

AT = "2026-10-25T22:00:00Z"
KICKOFF = "2026-10-25T23:10:00Z"


def operating_record(*, votes=True, at=AT, away_odds=2.1):
    game = dict(league="MLB", sport="bs", home="뉴욕양키", away="보스레드",
                year=2026, date="10.26(월) 08:10", round=100,
                options=[dict(market="승패", label="", 선택=s, 배당=o, 시장확률=p, 게임번호="10")
                         for s, o, p in [("홈", 1.49, .52), ("원정", away_odds, .48)]] +
                        [dict(market="언더오버", label="8.5", 선택=s, 배당=o, 시장확률=p, 게임번호="11")
                         for s, o, p in [("언더", 1.8, .8), ("오버", 2.0, .2)]])
    vote = dict(source="tailslips_public_html", event_type="first_observed", identity_version=2,
                eligible_pre_event=True, game_started=False, result=None, is_parlay_leg=False,
                is_baseline=False, market_type="moneyline", team_a="NYY", team_b="BOS",
                slug="author", pick_id="pick1", selection="BOS ML +110", american_odds=110,
                slate_date="2026-10-25", game_header="NYY BOS 7:10 PM ET", observed_at=AT)
    snapshot = build_decision_snapshot(game, as_of=at, tipster_votes=[vote] if votes else None)
    game["decision_snapshot"] = snapshot
    return dict(record_type="prediction", event_id=snapshot["event_id"],
                snapshot_id=snapshot["decision_id"], decision_id=snapshot["decision_id"],
                as_of=at, captured_at=at, kickoff=KICKOFF, market_observed_at=at,
                model=snapshot["model"], versions={"operating_model": snapshot["model"]["operating_version"]},
                features=ledger_features(game), predictions=prediction_payload(game),
                input_revision_hash=snapshot["input_revision_hash"], ledger_sequence=1), game


def test_real_vote_snapshot_matches_operating_selection_and_same_price(monkeypatch):
    import fixed_tipster_hybrid
    import runtime_db
    def forbidden(*args, **kwargs):
        raise AssertionError("DB / implicit vote loading forbidden")
    monkeypatch.setattr(runtime_db.RuntimeDatabase, "__init__", forbidden)
    monkeypatch.setattr(fixed_tipster_hybrid, "load_votes", forbidden)
    row, game = operating_record()
    original = deepcopy(row)
    obs = normalize_operating_prediction(row)
    assert obs["exclusion_reasons"] == []
    pick = obs["operating"]
    assert pick["selection_id"] == game["추천"]["selection_id"]
    assert pick["offer_id"] == game["추천"]["offer_id"]
    assert pick["selection"] == "원정"
    assert pick["odds"] == game["추천"]["배당"] == 2.1
    assert pick["probability"] == .48 != game["tipster_hybrid"]["vote_shares"]["원정"]
    assert pick["probability_kind"] == "market_reference"
    assert pick["calibration_status"] == "unverified"
    assert len(obs["same_market_offers"]) == 2
    assert len(obs["all_offers"]) == 4
    assert obs["fixture"]["sport"] == "bs"
    assert obs["as_of"] == "2026-10-25T22:00:00+00:00"
    assert obs["model_identity"]["policy"] == "tipster-vote-v1"
    assert obs["model_identity"]["model_artifact_hash"] is None
    assert obs["model_identity"]["model_hash"] is None
    assert not obs["result_used"] and not obs["hash_chain_verified"]
    assert row == original
    obs["same_market_offers"][0]["odds"] = 99
    assert row == original


def test_historical_anchor_is_separate_from_vote_policy_and_missing_fixture_not_guessed():
    current, _ = operating_record()
    old, _ = operating_record(votes=False)
    a, b = map(normalize_operating_prediction, [current, old])
    assert not a["exclusion_reasons"] and not b["exclusion_reasons"]
    assert b["model_identity"]["model_version"] == "shin-market-anchor-v1"
    assert b["model_identity"]["policy"] is None
    assert a["cohort_key"] != b["cohort_key"]
    old["features"].pop("fixture")
    b = normalize_operating_prediction(old)
    assert "missing_fixture:sport" in b["exclusion_reasons"]
    assert b["fixture"] == {}


def test_current_policy_withheld_keeps_old_operating_version_but_separate_cohort():
    old, _ = operating_record(votes=False)
    withheld = deepcopy(old)
    withheld["model"]["selection_policy"] = "tipster-vote-v1"
    withheld["features"]["tipster_hybrid"] = {
        "version": "tipster-vote-v1", "status": "withheld", "reason": "no_eligible_pregame_votes"}
    withheld["predictions"].update(action="withhold", selection_id=None, offer_id=None, probability=None)
    a, b = map(normalize_operating_prediction, [old, withheld])
    assert b["model_identity"]["model_version"] == "shin-market-anchor-v1"
    assert b["model_identity"]["feature_policy_version"] == "tipster-vote-v1"
    assert b["model_identity"]["feature_policy_status"] == "withheld"
    assert a["cohort_key"] != b["cohort_key"]
    assert "policy_version_mismatch" not in b["exclusion_reasons"]
    assert "vote_policy_not_applied" in b["exclusion_reasons"]
    # Even without model.selection_policy, the explicitly saved feature policy
    # context must not collapse into the historical market-only cohort.
    withheld["model"].pop("selection_policy")
    c = normalize_operating_prediction(withheld)
    assert c["model_identity"]["policy"] is None
    assert c["cohort_key"] != a["cohort_key"]


@pytest.mark.parametrize("damage,reason", [
    ("version", "model_version_mismatch"),
    ("hybrid_version", "policy_version_mismatch"),
    ("no_model", "unknown_model_identity"),
    ("selected_side", "policy_selection_mismatch"),
    ("selected_offer", "selection_offer_mismatch"),
    ("duplicate", "duplicate_offer"),
    ("price", "selected_field_mismatch:odds"),
    ("no_price", "missing_or_invalid_roster_odds"),
    ("nan_price", "missing_or_invalid_selected_odds"),
    ("probability", "vote_policy_probability_not_market_reference"),
    ("artifact", "model_artifact_hash_mismatch"),
    ("withhold", "no_operating_selection"),
    ("source_late", "feature_after_as_of:features.tipster_hybrid.latest_observed_at"),
])
def test_damaged_operating_contract_is_explicitly_excluded(damage, reason):
    row, _ = operating_record()
    if damage == "version": row["versions"]["operating_model"] = "market90-tipster10-v2"
    elif damage == "hybrid_version": row["features"]["tipster_hybrid"]["version"] = "market90-tipster10-v2"
    elif damage == "no_model": row["model"] = {}
    elif damage == "selected_side": row["features"]["tipster_hybrid"]["selected_side"] = "홈"
    elif damage == "selected_offer": row["predictions"]["offer_id"] = row["features"]["options"][0]["offer_id"]
    elif damage == "duplicate": row["features"]["options"].append(deepcopy(row["features"]["options"][0]))
    elif damage == "price": row["predictions"]["odds"] = 9
    elif damage == "no_price": row["features"]["options"][0]["odds"] = None
    elif damage == "nan_price": row["features"]["options"][1]["odds"] = float("nan")
    elif damage == "probability": row["predictions"]["probability"] = 1.0
    elif damage == "artifact": row["model"].update(artifact_hash="a", model_artifact_hash="b")
    elif damage == "withhold": row["predictions"]["action"] = "withhold"
    elif damage == "source_late": row["features"]["tipster_hybrid"]["latest_observed_at"] = "2026-10-25T22:01:00Z"
    result = normalize_operating_prediction(row)
    assert reason in result["exclusion_reasons"]
    assert not result["roi_input_eligible"]


def test_missing_probability_keeps_actual_pick_and_odds_never_uses_vote_share():
    row, _ = operating_record()
    row["predictions"]["probability"] = None
    obs = latest_pre_cutoff([row])["selected"][0]
    assert obs["roi_input_eligible"]
    assert obs["operating"]["probability"] is None
    assert obs["operating"]["probability_kind"] == "missing"
    assert obs["operating"]["selection"] == "원정" and obs["operating"]["odds"] == 2.1
    assert "probability_missing_roi_only" in obs["warnings"]


def test_ledger_default_nested_probability_payload_is_supported():
    row, game = operating_record()
    snapshot = game["decision_snapshot"]
    row["predictions"] = {k: snapshot[k] for k in ("action", "selection_id", "offer_id", "probability")}
    obs = normalize_operating_prediction(row)
    assert not obs["exclusion_reasons"]
    assert obs["operating"]["odds"] == 2.1
    assert obs["operating"]["probability"] == .48


def test_latest_revision_not_older_settled_revision_selected_before_quality_checks():
    old, _ = operating_record()
    latest, _ = operating_record(at="2026-10-25T22:10:00Z", away_odds=2.2)
    latest["ledger_sequence"] = 2
    latest["predictions"]["offer_id"] = "broken"
    settlement = dict(record_type="settlement", snapshot_id=old["snapshot_id"], outcome={"result": "hit"})
    report = latest_pre_cutoff(iter([latest, settlement, old]))
    assert len(report["selected"]) == 1
    assert report["selected"][0]["snapshot_id"] == latest["snapshot_id"]
    assert "selection_offer_mismatch" in report["selected"][0]["exclusion_reasons"]
    assert report["ignored_record_counts"] == {"settlement": 1}
    settlement["outcome"]["result"] = "miss"
    assert latest_pre_cutoff([old, latest, settlement]) == report


def test_latest_changed_selection_and_price_is_not_replaced_by_earlier_favorite():
    old, _ = operating_record(votes=False)
    new, _ = operating_record(at="2026-10-25T22:15:00Z", away_odds=2.3)
    new["ledger_sequence"] = 2
    selected = latest_pre_cutoff([new, old])["selected"][0]
    assert selected["snapshot_id"] == new["snapshot_id"]
    assert selected["operating"]["selection"] == "원정"
    assert selected["operating"]["odds"] == 2.3
    assert selected["roi_input_eligible"]


def test_latest_actual_withheld_snapshot_does_not_fall_back_to_earlier_vote_pick():
    old, game = operating_record()
    at = "2026-10-25T22:10:00Z"
    snapshot = build_decision_snapshot(game, as_of=at, tipster_votes=[])
    game["decision_snapshot"] = snapshot
    new = deepcopy(old)
    new.update(snapshot_id=snapshot["decision_id"], decision_id=snapshot["decision_id"],
               as_of=at, captured_at=at, market_observed_at=at, ledger_sequence=2,
               model=snapshot["model"], versions={"operating_model": snapshot["model"]["operating_version"]},
               features=ledger_features(game), predictions=prediction_payload(game))
    selected = latest_pre_cutoff([old, new])["selected"][0]
    assert selected["snapshot_id"] == new["snapshot_id"]
    assert selected["model_identity"]["model_version"] == "shin-market-anchor-v1"
    assert selected["model_identity"]["feature_policy_version"] == "tipster-vote-v1"
    assert selected["model_identity"]["feature_policy_status"] == "withheld"
    assert "vote_policy_not_applied" in selected["exclusion_reasons"]
    assert selected["operating"]["selection_id"] is None
    assert not selected["roi_input_eligible"]


@pytest.mark.parametrize("field,value,reason", [
    ("captured_at", "2026-10-26T00:00:00Z", "not_before_kickoff"),
    ("captured_at", "2026-10-25T22:40:00Z", "not_before_cutoff"),
    ("as_of", "2026-10-25T22:40:00Z", "not_before_cutoff"),
    ("market_observed_at", "2026-10-25T22:40:00Z", "market_not_before_cutoff"),
    ("market_observed_at", "2026-10-25T22:01:00Z", "inconsistent_observation_order"),
    ("captured_at", "2026-10-25T21:59:00Z", "inconsistent_observation_order"),
    ("as_of", "2026-10-25T22:00:00", "missing_or_invalid_time:as_of"),
])
def test_strict_clocks_and_late_capture_cannot_be_backdated(field, value, reason):
    row, _ = operating_record()
    row[field] = value
    report = latest_pre_cutoff([row])
    obs = (report["selected"] + report["rejected"])[0]
    assert reason in obs["exclusion_reasons"]
    assert not obs["roi_input_eligible"]


def test_bad_observed_clock_on_latest_revision_never_falls_back():
    old, _ = operating_record()
    new, _ = operating_record(at="2026-10-25T22:10:00Z")
    new["market_observed_at"] = None
    obs = latest_pre_cutoff([old, new])["selected"][0]
    assert obs["snapshot_id"] == new["snapshot_id"]
    assert "missing_or_invalid_time:market_observed_at" in obs["exclusion_reasons"]


def test_unorderable_revision_prevents_claiming_earlier_event_is_usable():
    old, _ = operating_record()
    new, _ = operating_record(at="2026-10-25T22:10:00Z")
    new["captured_at"] = None
    report = latest_pre_cutoff([old, new])
    assert report["selected"][0]["snapshot_id"] == old["snapshot_id"]
    assert "unorderable_event_revision" in report["selected"][0]["exclusion_reasons"]
    assert not report["selected"][0]["roi_input_eligible"]
    assert len(report["rejected"]) == 1


def test_applied_vote_policy_requires_saved_unique_plurality():
    row, _ = operating_record()
    row["features"]["tipster_hybrid"]["vote_counts"] = {"홈": 1, "원정": 1, "무": 0}
    obs = normalize_operating_prediction(row)
    assert "policy_vote_selection_mismatch" in obs["exclusion_reasons"]


def test_different_timezone_offsets_normalize_to_same_instant():
    row, _ = operating_record()
    row.update(as_of="2026-10-26T07:00:00+09:00", market_observed_at="2026-10-25T18:00:00-04:00")
    obs = latest_pre_cutoff([row])["selected"][0]
    assert obs["roi_input_eligible"]
    assert obs["as_of"] == obs["market_observed_at"] == obs["captured_at"]


def test_missing_third_side_of_three_way_market_is_not_a_complete_roster():
    row, _ = operating_record(votes=False)
    # The recorded side/offer is valid, but a two-offer fragment cannot stand
    # in for a complete three-way book.
    row["predictions"]["market"] = "승무패"
    for option in row["features"]["options"]:
        if option["market"] == row["predictions"]["market"] or option["market"] == "언더오버":
            option["market"] = "승무패"
    obs = normalize_operating_prediction(row)
    assert "incomplete_same_market_roster" in obs["exclusion_reasons"]


def test_saved_model_hash_conflicts_are_not_hidden_by_precedence():
    row, _ = operating_record()
    row["model_hash"] = "first"
    row["model"]["model_hash"] = "second"
    row["model_artifact_hash"] = "root"
    row["model"].update(model_artifact_hash="nested", artifact_hash="nested")
    obs = normalize_operating_prediction(row)
    assert "model_hash_mismatch" in obs["exclusion_reasons"]
    assert "model_artifact_hash_mismatch" in obs["exclusion_reasons"]


@pytest.mark.parametrize("value", ["hit", {"result": "pending", "home_score": 0}, {"status": "unconfirmed", "winner": "home"}])
def test_external_current_results_in_features_are_rejected_even_if_unconfirmed(value):
    row, _ = operating_record()
    row["features"]["external_current"] = {"result": value}
    obs = normalize_operating_prediction(row)
    assert "result_in_features:features.external_current.result" in obs["exclusion_reasons"]
    assert obs["result_used"] is False


def test_pending_marker_and_historical_form_are_not_current_labels():
    row, _ = operating_record()
    row["features"]["fixture"]["result"] = "경기전"
    row["features"]["team_context"]["home_form"] = [{"result": "win", "score": "3-1"}]
    row["outcome"] = {"result": "hit"}  # Not used or copied as a label.
    obs = normalize_operating_prediction(row)
    assert obs["roi_input_eligible"]
    assert "unconfirmed_result_feature:features.fixture.result" in obs["warnings"]
    assert "outcome" not in obs


def test_unavailable_non_input_marker_is_not_an_active_feature_clock():
    row, _ = operating_record()
    row["features"]["research_player_inputs"] = dict(
        version="pregame-player-capture-v1", status="unavailable", affects_probability=False,
        training_eligible=False, observed_at="2026-10-25T22:00:25Z", reason="missing")
    obs = normalize_operating_prediction(row)
    assert obs["roi_input_eligible"]
    assert "non_input_metadata_after_as_of:features.research_player_inputs.observed_at" in obs["warnings"]
    # An arbitrary data payload, even marked unavailable, must not receive this
    # narrow metadata-only exception.
    row["features"]["research_player_inputs"]["model_inputs"] = {"x": 1}
    obs = normalize_operating_prediction(row)
    assert "feature_after_as_of:features.research_player_inputs.observed_at" in obs["exclusion_reasons"]
    row["features"]["research_player_inputs"]["result"] = "unconfirmed-winner"
    obs = normalize_operating_prediction(row)
    assert "result_in_features:features.research_player_inputs.result" in obs["exclusion_reasons"]


def test_export_wrapper_and_provenance_are_preserved_without_claiming_authentication():
    row, _ = operating_record()
    row.update(batch_id="batch1", common_input_hash="inputs1", candidate_provenance={"kind": "replayed", "saved_at": AT})
    row["features"].update(feature_cutoff_at=AT, model_inputs={"x": 1})
    row["model"].update(artifact_hash="saved-code", model_hash="saved-model")
    obs = normalize_operating_prediction({"record_json": json.dumps(row), "captured_at": "2099-01-01T00:00:00Z"})
    assert obs["roi_input_eligible"]
    assert obs["batch_id"] == "batch1" and obs["common_input_hash"] == "inputs1"
    assert obs["candidate_provenance"]["kind"] == "replayed"
    assert obs["model_inputs"] == {"x": 1}
    assert obs["model_identity"]["model_artifact_hash"] == "saved-code"
    assert obs["model_identity"]["model_hash"] == "saved-model"
    assert obs["model_identity_hash"] != "saved-model"
    assert obs["source_feature_cutoff_at"] == datetime.fromisoformat(AT.replace("Z", "+00:00")).isoformat()
    assert not obs["hash_chain_verified"]


def test_ambiguous_revision_tie_and_conflicting_kickoff_fail_closed():
    row, _ = operating_record()
    conflict = deepcopy(row)
    conflict["predictions"]["odds"] = 3.0
    for rows in ([row, conflict], [conflict, row]):
        assert "ambiguous_latest_revision" in latest_pre_cutoff(rows)["selected"][0]["exclusion_reasons"]
    assert latest_pre_cutoff([row, deepcopy(row)])["selected"][0]["roi_input_eligible"]
    conflict = deepcopy(row)
    conflict["kickoff"] = "2026-10-25T23:11:00Z"
    assert "conflicting_event_kickoff" in latest_pre_cutoff([row, conflict])["selected"][0]["exclusion_reasons"]


@pytest.mark.parametrize("cutoff", [-1, float("inf"), True])
def test_invalid_cutoff_is_rejected(cutoff):
    with pytest.raises(ValueError):
        latest_pre_cutoff([], cutoff_minutes=cutoff)


def test_invalid_json_and_settlements_do_not_create_predictions():
    report = latest_pre_cutoff([{"record_json": "{"}, {"record_type": "settlement"}])
    assert report["selected"] == []
    assert report["rejected"][0]["exclusion_reasons"] == ["invalid_record_json"]
    assert report["ignored_record_counts"] == {"settlement": 1}


def test_optional_read_only_operating_export_policy_and_price_parity():
    """Opt in via OPERATING_LEDGER_TEST_EXPORT; never opens storage/network."""
    path = os.environ.get("OPERATING_LEDGER_TEST_EXPORT")
    if not path:
        pytest.skip("Set OPERATING_LEDGER_TEST_EXPORT to a read-only local gzip export")
    from fixed_tipster_hybrid import apply
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        text = handle.read()
    decoder, offset, count, withheld_count = json.JSONDecoder(), 0, 0, 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        # Also tolerate two valid objects concatenated at an export boundary.
        row, offset = decoder.raw_decode(text, offset)
        if row.get("record_type") != "prediction":
            continue
        hybrid = row.get("features", {}).get("tipster_hybrid", {})
        if hybrid.get("version") != "tipster-vote-v1":
            continue
        observation = normalize_operating_prediction(row)
        if hybrid.get("status") == "withheld":
            withheld_count += 1
            assert observation["model_identity"]["feature_policy_status"] == "withheld"
            assert "vote_policy_not_applied" in observation["exclusion_reasons"]
            assert "policy_version_mismatch" not in observation["exclusion_reasons"]
            continue
        if row["model"].get("operating_version") != "tipster-vote-v1":
            continue
        game = {"probability_pipeline": {}, "options": [
            {**o, "선택": o["selection"], "배당": o["odds"], "시장확률": o["market_probability"]}
            for o in row["features"]["options"]]}
        pick = apply(game, deepcopy(hybrid))
        assert pick is not None
        pred, canonical = row["predictions"], observation["operating"]
        assert canonical["selection_id"] == pred["selection_id"] == pick["selection_id"]
        assert canonical["offer_id"] == pred["offer_id"] == pick["offer_id"]
        assert canonical["selection"] == pred["selection"] == pick["선택"]
        assert canonical["odds"] == pred["odds"] == pick["배당"]
        assert canonical["probability"] == pred["probability"] == pick["최종확률"]
        assert canonical["probability_kind"] == "market_reference"
        assert not any("mismatch" in reason for reason in observation["exclusion_reasons"])
        count += 1
    assert count > 0 and withheld_count > 0
