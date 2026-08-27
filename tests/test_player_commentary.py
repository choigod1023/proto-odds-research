from src.player_commentary import player_context_text, with_player_context


NPB_INFO = {
    "home_detail": {"name": "야나기 유야", "stats": {
        "period": "2026시즌", "record": "4승 5패", "era": 2.45, "whip": 1.10,
    }},
    "away_detail": {"name": "마츠모토 켄고", "stats": {
        "period": "2026시즌", "record": "6승 4패", "era": 3.61, "whip": 1.24,
    }},
    "lineups": {
        "home": [{"order": 3, "name": "보슬러", "position": "1루수",
                  "stats": {"avg": .272, "home_runs": 18, "ops": .842}}],
        "away": [{"order": 4, "name": "무라카미 무네타카", "position": "3루수",
                  "stats": {"avg": .285, "home_runs": 26, "ops": .910}}],
    },
    "lineup_status": {"state": "projected_from_recent_official"},
}


def test_npb_player_context_contains_starters_hitters_and_lineup_state():
    text = player_context_text("주니치", "야쿠르트", "bs", NPB_INFO)
    assert "예고 선발 맞대결은" in text
    assert "야나기 유야(2026시즌, 4승 5패, ERA 2.45, WHIP 1.10)" in text
    assert "마츠모토 켄고(2026시즌, 6승 4패, ERA 3.61, WHIP 1.24)" in text
    assert "주니치 3번 보슬러" in text
    assert "야쿠르트 4번 무라카미 무네타카" in text
    assert "해당 경기의 확정 명단은 아니다" in text


def test_official_lineup_is_described_as_official():
    info = {**NPB_INFO, "lineup_status": {"state": "official_today"}}
    text = player_context_text("주니치", "야쿠르트", "bs", info)
    assert "오늘 공식 타순의 팀별 OPS 상위 타자" in text
    assert "확정 명단은 아니다" not in text


def test_예고_선발과_확정_선발을_구분한다():
    probable = {**NPB_INFO, "source_url": "https://www.mlb.com/probable-pitchers"}
    assert player_context_text("주니치", "야쿠르트", "bs", probable).startswith(
        "예고 선발 맞대결은")

    confirmed = {**NPB_INFO, "starter_status": {"state": "confirmed"}}
    assert player_context_text("주니치", "야쿠르트", "bs", confirmed).startswith(
        "확정 선발 맞대결은")


def test_player_context_is_inserted_after_market_conclusion():
    base = "시장 기본값은 주니치 승 59% · 배당 1.52. 주니치는 최근 흐름이 좋아지고 있다."
    text = with_player_context(base, "주니치", "야쿠르트", "bs", NPB_INFO)
    assert text.startswith("시장 기본값은 주니치 승 59% · 배당 1.52. 예고 선발 맞대결은")
    assert text.endswith("주니치는 최근 흐름이 좋아지고 있다.")


def test_player_context_does_not_split_decision_from_probabilities():
    base = ("산출 시점의 최종 선택은 주니치 승이다. 모델확률은 59.0%, 실패확률은 41.0%, "
            "배당은 1.52다. 주니치는 최근 흐름이 좋아지고 있다.")
    text = with_player_context(base, "주니치", "야쿠르트", "bs", NPB_INFO)
    assert text.startswith(
        "산출 시점의 최종 선택은 주니치 승이다. 모델확률은 59.0%, 실패확률은 41.0%, "
        "배당은 1.52다. 예고 선발 맞대결은"
    )
