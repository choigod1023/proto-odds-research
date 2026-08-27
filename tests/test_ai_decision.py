import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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


def test_market_reference_ignores_shadow_model_ev():
    game = _game()
    selected = choose_market_reference(game["options"])

    assert selected["선택"] == "언더"
    assert selected["시장확률"] == 0.55
    assert "제외" not in game["options"][0]
    assert game["options"][0]["추천우선순위"] == "fallback"
    assert game["options"][1]["제외"].startswith("배당 2.20 이상")
    assert game["options"][2]["추천우선순위"] == "primary"
    assert game["options"][2]["선택근거"] == "shin_market_accuracy_preferred_odds"


def test_low_odds_market_reference_remains_as_fallback_without_primary():
    low_only = [_game()["options"][0]]

    selected = choose_market_reference(low_only)

    assert selected["선택"] == "승"
    assert selected["추천우선순위"] == "fallback"
    assert selected["선택근거"] == "shin_market_accuracy_low_odds_fallback"


def test_moderate_underdog_replaces_previous_main_pick():
    game = _game()
    game["options"][1]["배당"] = 2.05
    game["options"][1]["모델확률"] = 0.55

    annotate_options(game)
    selected = choose_market_reference(game["options"])

    assert game["options"][1]["이변후보"] is True
    assert game["options"][1]["이변점수"] == 0.21
    assert "검증 전 모델" in game["options"][1]["이변근거"]
    assert selected["선택"] == "패"
    assert selected["추천우선순위"] == "reversal"
    assert selected["최종전환"] is True


def test_reversal_snapshot_keeps_market_probability_and_one_selection():
    game = _game()
    game["options"][1]["배당"] = 2.05
    game["options"][1]["모델확률"] = 0.55

    snapshot = build_decision_snapshot(
        game, as_of="2026-08-27T09:00:00+09:00")

    assert game["추천"] is game["options"][1]
    assert snapshot["selection_id"] == game["options"][1]["selection_id"]
    assert snapshot["gate_codes"] == ["qualified_market_reversal"]
    assert snapshot["probability"]["market"] == 0.34
    assert snapshot["probability"]["final"] == 0.34
    assert snapshot["probability"]["ai_delta_applied"] == 0.0
    assert snapshot["stages"]["structured_ai"]["status"] == "selection_gate"


def test_snapshot_replaces_caller_model_pick_and_applies_zero_ai_delta():
    game = _game()
    game["추천"] = game["options"][1]  # 구조 모델이 좋아하는 역배를 일부러 주입

    snapshot = build_decision_snapshot(
        game, as_of="2026-08-27T09:00:00+09:00", explanation_kind="llm_assisted")

    assert game["추천"]["선택"] == "언더"
    assert snapshot["selection_id"] == game["추천"]["selection_id"]
    assert snapshot["probability"]["market"] == 0.55
    assert snapshot["probability"]["ai_candidate"] == 0.75
    assert snapshot["probability"]["ai_delta_candidate"] == 0.20
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

    assert selected is option
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
