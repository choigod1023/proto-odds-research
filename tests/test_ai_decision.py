import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ai_decision  # noqa: E402
from ai_decision import (  # noqa: E402
    USAGE_CONSUMERS,
    build_decision_snapshot,
    annotate_options,
    choose_market_reference,
    event_id,
    offer_id,
    selection_id,
    usage_counts,
    validate_decision_snapshot,
)
from probability_pipeline import artifact_hash, build_artifact  # noqa: E402


def _game(round_no: int = 91) -> dict:
    return {
        "year": 2026,
        "round": round_no,
        "date": "08.27(목) 19:00",
        "league": "KBO",
        "sport": "bs",
        "home": "서울",
        "away": "부산",
        "form_home": {"last10": "7승 3패"},
        "form_away": {"last10": "4승 6패"},
        "선발": {
            "home": "김선발",
            "away": "박선발",
            "updated_at": "2026-08-27T08:00:00+09:00",
            "source": "공식 경기 정보",
            "source_url": "https://example.test/game",
            "unavailable": {"home": [{"name": "이부상"}], "away": []},
        },
        "options": [
            {
                "market": "승패", "label": "", "line": None, "게임번호": "10",
                "선택": "승", "배당": 1.48, "시장확률": 0.66, "모델확률": 0.31,
            },
            {
                "market": "승패", "label": "", "line": None, "게임번호": "10",
                "선택": "패", "배당": 2.35, "시장확률": 0.34, "모델확률": 0.69,
            },
            {
                "market": "언더오버", "label": "8.5", "line": 8.5, "게임번호": "11",
                "선택": "언더", "배당": 1.75, "시장확률": 0.55, "모델확률": 0.75,
            },
            {
                "market": "언더오버", "label": "8.5", "line": 8.5, "게임번호": "11",
                "선택": "오버", "배당": 1.82, "시장확률": 0.45, "모델확률": 0.25,
            },
        ],
    }


def _promoted_artifact() -> dict:
    return build_artifact(
        feature_order=["market_probability"],
        models=[{
            "sport": "*",
            "market": "*",
            "intercept": 0.0,
            "coefficients": [0.3],
            "uncertainty_clip": {
                "residual_min": -0.5,
                "residual_max": 0.5,
                "logit_radius": 0.1,
                "probability_min": 0.01,
                "probability_max": 0.99,
            },
            "calibration": {
                "schema": "probability-calibration",
                "version": 1,
                "method": "identity",
            },
        }],
        evidence={
            "pristine_future": True,
            "n_predictions": 300,
            "brier_improvement_ci95": [0.001, 0.01],
            "log_loss_improvement_ci95": [0.001, 0.01],
            "baseline_average_odds": 1.65,
            "candidate_average_odds": 1.65,
            "baseline_coverage": 0.70,
            "candidate_coverage": 0.70,
        },
    )


def test_hit_probability_ranker_prefers_target_odds_before_low_price_fallback():
    game = _game()
    selected = choose_market_reference(game["options"])

    assert selected["선택"] == "언더"
    assert selected["시장확률"] == 0.55
    assert selected["예상적중확률"] == 0.55
    assert "제외" not in game["options"][0]
    assert game["options"][0]["추천우선순위"] == "fallback"
    assert game["options"][1]["제외"].startswith("배당 2.20 이상")
    assert game["options"][2]["추천우선순위"] == "primary"
    assert game["options"][2]["선택근거"] == "shin_market_hit_probability"


def test_target_odds_boundary_beats_low_price_market_probability():
    options = [
        {"market": "승패", "label": "", "line": None, "게임번호": "1",
         "선택": "홈", "배당": 1.49, "시장확률": 0.62, "모델확률": 0.60},
        {"market": "언더오버", "label": "8.5", "line": 8.5, "게임번호": "2",
         "선택": "언더", "배당": 1.50, "시장확률": 0.60, "모델확률": 0.61},
    ]

    assert choose_market_reference(options) is options[1]


def test_validated_final_hit_probability_beats_raw_market_probability():
    options = [
        {"market": "승패", "label": "", "line": None, "게임번호": "1",
         "선택": "홈", "배당": 1.60, "시장확률": 0.61, "최종확률": 0.58,
         "확률근거": "validated_residual", "AI반영": True},
        {"market": "언더오버", "label": "8.5", "line": 8.5, "게임번호": "2",
         "선택": "언더", "배당": 1.65, "시장확률": 0.59, "최종확률": 0.66,
         "확률근거": "validated_residual", "AI반영": True},
    ]

    selected = choose_market_reference(options)

    assert selected is options[1]
    assert selected["추천점수"] == 0.66
    assert selected["선택근거"] == "validated_final_hit_probability"


def test_low_odds_market_reference_remains_as_fallback_without_primary():
    low_only = [_game()["options"][0]]

    selected = choose_market_reference(low_only)

    assert selected["선택"] == "승"
    assert selected["추천우선순위"] == "fallback"
    assert selected["선택근거"] == "shin_market_hit_probability"


def test_moderate_underdog_is_observed_but_does_not_replace_market_favorite():
    game = _game()
    game["options"][1]["배당"] = 2.05
    game["options"][1]["모델확률"] = 0.55

    annotate_options(game)
    selected = choose_market_reference(game["options"])

    assert game["options"][1]["이변후보"] is True
    assert game["options"][1]["이변점수"] == 0.21
    assert "검증 전 모델" in game["options"][1]["이변근거"]
    assert selected["선택"] == "언더"
    assert selected["추천우선순위"] == "primary"
    assert not selected.get("최종전환")


def test_shadow_underdog_snapshot_keeps_market_favorite_and_zero_ai_delta():
    game = _game()
    game["options"][1]["배당"] = 2.05
    game["options"][1]["모델확률"] = 0.55

    snapshot = build_decision_snapshot(
        game, as_of="2026-08-27T09:00:00+09:00")

    assert game["추천"] is game["options"][2]
    assert snapshot["selection_id"] == game["options"][2]["selection_id"]
    assert snapshot["gate_codes"] == []
    assert snapshot["probability"]["market"] == 0.55
    assert snapshot["probability"]["final"] == 0.55
    assert snapshot["probability"]["ai_delta_applied"] == 0.0
    assert snapshot["stages"]["structured_ai"]["status"] == "shadow"


def test_snapshot_replaces_caller_model_pick_and_applies_zero_ai_delta():
    game = _game()
    game["추천"] = game["options"][1]  # 구조 모델이 좋아하는 역배를 일부러 주입

    snapshot = build_decision_snapshot(
        game, as_of="2026-08-27T09:00:00+09:00", explanation_kind="llm_assisted")

    assert game["추천"]["선택"] == "언더"
    assert snapshot["selection_id"] == game["추천"]["selection_id"]
    assert snapshot["probability"]["market"] == 0.55
    assert snapshot["probability"]["ai_candidate"] == 0.75
    assert snapshot["probability"]["ai_delta_candidate"] == 0.2
    assert snapshot["probability"]["ai_delta_applied"] == 0.0
    assert snapshot["probability"]["final"] == snapshot["probability"]["market"]
    assert snapshot["model"]["status"] == "shadow"
    assert snapshot["stages"]["language_ai"]["status"] == "wording_only"
    assert snapshot["stages"]["language_ai"]["affects_probability"] is False


def test_snapshot_has_unique_complete_evidence_usage_ledger():
    snapshot = build_decision_snapshot(
        _game(), as_of="2026-08-27T09:00:00+09:00")

    validate_decision_snapshot(snapshot)
    evidence_ids = [row["id"] for row in snapshot["evidence"]]
    assert len(evidence_ids) == len(set(evidence_ids))
    for evidence in snapshot["evidence"]:
        assert set(evidence["usage"]) == set(USAGE_CONSUMERS)
    assert sum(usage_counts(snapshot).values()) == len(snapshot["evidence"])
    assert usage_counts(snapshot)["context_only"] >= 1


def test_event_and_selection_ids_survive_reissued_round_and_game_number():
    first = _game(round_no=91)
    second = _game(round_no=92)
    second["options"][0]["게임번호"] = "310"

    assert event_id(first) == event_id(second)
    assert selection_id(first, first["options"][0]) == selection_id(second, second["options"][0])
    assert offer_id(first, first["options"][0]) != offer_id(second, second["options"][0])


def test_no_eligible_market_reference_is_an_explicit_withhold():
    game = _game()
    for option in game["options"]:
        option["market"] = "홀짝"

    snapshot = build_decision_snapshot(game, as_of="2026-08-27T09:00:00+09:00")

    assert snapshot["action"] == "withhold"
    assert snapshot["probability"]["final"] is None
    assert snapshot["gate_codes"] == ["no_eligible_market_reference"]
    assert game["추천"] is None


def test_recalculation_clears_stale_exclusion_reason():
    game = _game()
    option = game["options"][2]
    option["제외"] = "이전 배당에서 제외됨"

    selected = choose_market_reference(game["options"])

    assert "제외" not in option


def test_operational_label_without_passed_gate_cannot_change_probability():
    snapshot = build_decision_snapshot(
        _game(), as_of="2026-08-27T09:00:00+09:00")
    snapshot["model"]["status"] = "operational"
    snapshot["model"]["validated_edge"] = False
    snapshot["probability"]["ai_delta_applied"] = 0.2
    snapshot["probability"]["final"] = 0.86

    try:
        validate_decision_snapshot(snapshot)
    except ValueError as error:
        assert "unvalidated AI" in str(error)
    else:
        raise AssertionError("검증 관문 없는 operational 값이 통과했다")


def test_self_claimed_promoted_artifact_stays_shadow_until_code_reviewed():
    artifact = _promoted_artifact()
    snapshot = build_decision_snapshot(
        _game(),
        as_of="2026-08-27T09:00:00+09:00",
        probability_artifact=artifact,
    )

    assert snapshot["probability"]["residual_candidate"] is not None
    assert snapshot["probability"]["final"] == snapshot["probability"]["market"]
    assert snapshot["probability"]["ai_delta_applied"] == 0.0
    assert snapshot["model"]["artifact_hash"] == artifact_hash(artifact)
    assert snapshot["model"]["promotion_gate"] == "not_passed"


def test_allowlisted_promoted_artifact_can_change_final_probability(monkeypatch):
    artifact = _promoted_artifact()
    digest = artifact_hash(artifact)
    monkeypatch.setattr(
        ai_decision, "PROMOTED_ARTIFACT_HASHES", frozenset({digest})
    )

    snapshot = build_decision_snapshot(
        _game(),
        as_of="2026-08-27T09:00:00+09:00",
        probability_artifact=artifact,
    )

    assert snapshot["probability"]["final"] != snapshot["probability"]["market"]
    assert snapshot["probability"]["ai_delta_applied"] != 0.0
    assert snapshot["model"]["status"] == "operational"
    assert snapshot["model"]["promotion_gate"] == "passed"
    validate_decision_snapshot(snapshot)


def test_missing_or_invalid_price_cannot_be_a_market_reference():
    options = [{
        "market": "승패", "label": "", "line": None, "게임번호": "10",
        "선택": "승", "배당": None, "시장확률": 0.60, "모델확률": 0.70,
    }]

    assert choose_market_reference(options) is None
    assert "유효한 배당" in options[0]["제외"]


def test_evidence_observed_after_feature_cutoff_is_rejected():
    game = _game()
    game["선발"]["updated_at"] = "2026-08-27T10:00:00+09:00"

    try:
        build_decision_snapshot(game, as_of="2026-08-27T09:00:00+09:00")
    except ValueError as error:
        assert "observed after cutoff" in str(error)
    else:
        raise AssertionError("입력 컷오프 뒤 자료가 판정에 들어갔다")


def test_policy_authorized_internal_baseball_model_changes_probability_with_audit():
    game = _game()
    game["options"] = game["options"][:2]
    game["options"][0]["배당"] = 1.60
    game["선발"].update({
        "home_detail": {"name": "김선발", "stats": {"fip": 2.8}},
        "away_detail": {"name": "박선발", "stats": {"fip": 4.8}},
        "starter_status": {"state": "confirmed"},
    })

    snapshot = build_decision_snapshot(
        game, as_of="2026-08-27T09:00:00+09:00")

    assert snapshot["model"]["operating_version"] == "internal-context-blend-v2"
    assert snapshot["model"]["policy_authorized"] is True
    assert snapshot["model"]["validated_edge"] is False
    assert snapshot["probability"]["final"] != snapshot["probability"]["market"]
    assert snapshot["stages"]["availability_ai"]["affects_probability"] is True
    validate_decision_snapshot(snapshot)
