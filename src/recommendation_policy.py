"""자동 추천에 들어갈 수 있는 마켓의 검증 정책.

가격과 모델 확률을 계산할 수 있다는 사실만으로 추천 자격이 생기지는 않는다.
시간순 외부표본에서 시장 대비 우위를 입증하지 못한 마켓은 화면에는 남기되,
경기별 추천과 오늘의 조합에서는 제외한다.
"""
from __future__ import annotations


AUTO_RECOMMENDATION_EXCLUSIONS = {
    "홀짝": "시장 대비 우위가 검증되지 않은 마켓 — 자동 추천 제외",
}


def recommendation_exclusion_reason(market: object) -> str | None:
    """자동 추천에서 제외해야 하는 마켓이면 사용자용 사유를 돌려준다."""
    return AUTO_RECOMMENDATION_EXCLUSIONS.get(str(market or "").strip())


def is_recommendable_market(market: object) -> bool:
    return recommendation_exclusion_reason(market) is None
