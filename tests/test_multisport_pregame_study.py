from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.multisport_pregame_study import compare, choose


START = "2025-01-01T00:00:00+09:00"
END = "2026-11-04T00:00:00+09:00"
NOW = "2026-11-04T01:00:00+09:00"


def record(kind, rid, payload, received="2025-05-01T07:00:00Z"):
    return dict(record_id=rid, kind=kind, sport="bs", league="KBO", home="H", away="A",
                kickoff="2025-05-01T09:30:00Z", received_at=received,
                source_url="https://www.betman.co.kr/official", timing_basis="actual_receipt",
                timestamp_basis="http_response_body_completed",
                receipt_verified=True, body_sha256="a"*64, payload=payload)


def fixture():
    product = dict(product_id="G101:250050:3", period="full", market="winner_2way", line=None,
                   settlement_rule="draw_void_refund")
    rows = [record("price", "p", dict(product, odds={"home": 1.8, "away": 2.0}), "2025-05-01T08:00:00Z"),
            record("tipster", "v1", dict(product, author="author1", raw_text="H ML", selection="home")),
            record("tipster", "v2", dict(product, author="author2", raw_text="H ML", selection="home")),
            record("lineup", "l", dict(confirmed=True,
                   home=dict(starter="hp", batting_order=[f"h{i}" for i in range(9)]),
                   away=dict(starter="ap", batting_order=[f"a{i}" for i in range(9)]))),
            record("player_history", "h", dict(latest_source_kickoff="2025-04-30T09:30:00Z", latest_source_completed_at="2025-04-30T12:30:00Z")),
            record("result", "r", dict(product, confirmed=True, home_score=3, away_score=1), "2025-05-01T12:00:00Z")]
    for name in ("kbo_m0", "kbo_m1", "kbo_m2"):
        model_row = record("model_prediction", name, dict(model_id=name, artifact_sha256="b"*64,
                    train_end="2024-12-31T23:00:00Z", as_of="2025-05-01T07:30:00Z",
                    input_record_ids=["l", "h"], product_id=product["product_id"],
                    probabilities={"home": .6, "away": .4}), "2025-05-01T07:31:00Z")
        model_row["model_artifact_verified"] = True
        rows.append(model_row)
    return rows


def run(rows):
    return compare(rows, start=START, end=END, evaluated_at=NOW)


def test_equal_product_and_cohort_payout_exact():
    result = run(fixture())["summary"]["bs/KBO"]
    assert result["all_models_common_n"] == 1
    for stats in result["all_models"].values():
        assert stats["hit"] == 1 and stats["net"] == "8000.0" and stats["roi_percent"] == 80


@pytest.mark.parametrize("kind", ["price", "tipster", "lineup", "model_prediction"])
def test_postgame_reconstruction_cannot_enter_common_cohort(kind):
    rows = fixture()
    for row in rows:
        if row["kind"] == kind:
            row.update(timing_basis="retrospective", published_at="2025-04-30T01:00:00Z")
    result = run(rows)["summary"]["bs/KBO"]
    assert result["all_models_common_n"] == 0


def test_legacy_batch_start_never_becomes_completed_receipt():
    rows = fixture()
    rows[0]["timestamp_basis"] = "legacy_batch_start_not_HTTP_receipt"
    assert run(rows)["events"] == []


def test_result_change_cannot_change_selection():
    rows = fixture()
    before = choose(rows)[0]
    rows[5]["payload"].update(home_score=0, away_score=100)
    assert choose(rows)[0] == before
    assert run(rows)["summary"]["bs/KBO"]["all_models"]["vote"]["net"] == "-10000"


def test_same_author_first_vote_and_conflicting_first_votes():
    rows = fixture()
    duplicate = deepcopy(rows[1]); duplicate["record_id"] = "duplicate"
    duplicate["payload"]["selection"] = "away"
    rows.append(duplicate)
    selected, reasons = choose(rows)
    assert reasons["vote"]["conflicting_first_author_votes"] == 1
    assert selected["vote_record_ids"] == ["v2"]


def test_vote_tie_abstains_not_alphabetical_fallback():
    rows = fixture(); rows[2]["payload"]["selection"] = "away"
    assert choose(rows)[0]["picks"]["vote"] is None
    assert run(rows)["summary"]["bs/KBO"]["all_models_common_n"] == 0


@pytest.mark.parametrize("field,value", [("product_id", "wrong"), ("train_end", "2025-05-02T00:00:00Z"),
                                        ("input_record_ids", ["r"]), ("artifact_sha256", "invalid")])
def test_invalid_model_identity_training_or_lineage_fails_closed(field, value):
    rows = fixture(); rows[6]["payload"][field] = value
    assert run(rows)["summary"]["bs/KBO"]["all_models_common_n"] == 0


def test_target_day_history_is_not_pregame_player_form():
    rows = fixture(); rows[4]["payload"]["latest_source_kickoff"] = "2025-05-01T00:00:00Z"
    assert run(rows)["summary"]["bs/KBO"]["all_models_common_n"] == 0


def test_lineup_flag_does_not_replace_confirmed_ordered_nine():
    rows = fixture(); rows[3]["payload"]["home"]["batting_order"] = ["one"]
    assert run(rows)["summary"]["bs/KBO"]["all_models_common_n"] == 0


def test_newer_unconfirmed_official_correction_invalidates_older_score():
    rows = fixture(); correction = deepcopy(rows[5]); correction["record_id"] = "r2"
    correction["received_at"] = "2025-05-01T13:00:00Z"
    correction["payload"].update(confirmed=False, home_score=None, away_score=None)
    rows.append(correction)
    assert run(rows)["events"][0]["result"] is None


def test_product_settlement_rule_must_match():
    rows = fixture(); rows[5]["payload"]["settlement_rule"] = "regulation_only"
    assert run(rows)["events"][0]["result"] is None


def test_unknown_result_host_not_official():
    rows = fixture(); rows[5]["source_url"] = "https://lookalike.example/official"
    assert run(rows)["events"][0]["result"] is None


def test_contradicting_duplicate_record_aborts_not_keeps_first():
    rows = fixture(); conflict = deepcopy(rows[0]); conflict["payload"]["odds"]["home"] = 9
    with pytest.raises(ValueError, match="conflicting_record_id"):
        run(rows + [conflict])


def test_baseball_models_cannot_be_renamed_as_soccer_models():
    rows = fixture()
    for row in rows:
        row.update(sport="sc", league="K리그1")
        if "settlement_rule" in row["payload"]:
            row["payload"]["settlement_rule"] = "regulation_90_plus_stoppage"
    result = run(rows)["summary"]["sc/K리그1"]
    assert result["all_models_common_n"] == 0 and "kbo_m0" in result["unavailable_scope"]
    assert "kbo_m0" not in result["models"]


def test_two_way_draw_void_and_accuracy_denominator_are_explicit():
    rows = fixture(); rows[5]["payload"].update(home_score=2, away_score=2)
    result = run(rows)["summary"]["bs/KBO"]["all_models"]["vote"]
    assert result["void"] == 1 and result["net"] == "0" and result["roi_percent"] == 0
    assert result["accuracy_percent"] is None


def test_period_does_not_pull_2024_into_2025_results():
    rows = fixture()
    for row in rows:
        row["kickoff"] = "2024-05-01T09:30:00Z"
    assert run(rows)["physical_fixtures"] == 0


@pytest.mark.parametrize("basis", [None, "legacy_batch_start_unrecognized", "http_request_started"])
def test_unknown_or_disguised_legacy_clock_is_not_completed_receipt(basis):
    rows = fixture(); rows[0]["timestamp_basis"] = basis
    assert not run(rows)["events"]


def test_different_expert_product_or_settlement_rule_is_not_same_market():
    rows = fixture()
    for row in rows[1:3]:
        row["payload"].update(product_id="other", settlement_rule="regulation_only")
    assert run(rows)["summary"]["bs/KBO"]["controls_common_n"] == 0


def test_history_cannot_contain_game_after_claimed_receipt_or_forecast():
    rows = fixture()
    for row in rows:
        if row["kind"] in {"lineup", "player_history", "model_prediction"}:
            row["received_at"] = "2025-04-30T07:00:00Z"
        if row["kind"] == "model_prediction":
            row["payload"]["as_of"] = "2025-04-30T08:00:00Z"
    assert run(rows)["summary"]["bs/KBO"]["all_models_common_n"] == 0


def test_both_supported_baseball_refund_contracts_keep_same_stake_denominator():
    rows = fixture()
    for row in rows:
        if "settlement_rule" in row["payload"]:
            row["payload"]["settlement_rule"] = "full_game_including_extra_innings_draw_refund"
    rows[5]["payload"].update(home_score=2, away_score=2)
    result = run(rows)["summary"]["bs/KBO"]
    assert result["all_models_common_n"] == 1
    assert all(m["bets"] == 1 and m["void"] == 1 for m in result["all_models"].values())
