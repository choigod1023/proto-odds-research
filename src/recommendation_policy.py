"""자동 추천에 들어갈 수 있는 마켓의 검증 정책.

가격과 모델 확률을 계산할 수 있다는 사실만으로 추천 자격이 생기지는 않는다.
시간순 외부표본에서 시장 대비 우위를 입증하지 못한 마켓은 화면에는 남기되,
경기별 추천과 오늘의 조합에서는 제외한다.
"""
from __future__ import annotations

import math


MAX_AUTO_RECOMMENDATION_ODDS = 2.2

AUTO_RECOMMENDATION_EXCLUSIONS = {
    "홀짝": "시장 대비 우위가 검증되지 않은 마켓 — 자동 추천 제외",
}


def recommendation_exclusion_reason(market: object) -> str | None:
    """자동 추천에서 제외해야 하는 마켓이면 사용자용 사유를 돌려준다."""
    return AUTO_RECOMMENDATION_EXCLUSIONS.get(str(market or "").strip())


def automatic_selection_exclusion_reason(
    market: object,
    odds: object,
    market_probability: object = None,
    favorite_probability: object = None,
) -> str | None:
    """검증 전 자동 추천에서 제외할 선택지면 사용자용 사유를 돌려준다."""
    reason = recommendation_exclusion_reason(market)
    if reason:
        return reason

    try:
        price = float(odds)
    except (TypeError, ValueError):
        price = None
    if price is None or not math.isfinite(price) or price <= 1.0:
        return "유효한 배당이 없어 자동 추천 제외"
    if price >= MAX_AUTO_RECOMMENDATION_ODDS:
        return "배당 2.20 이상 — 과거 손실이 급증한 구간이라 자동 추천 제외"

    try:
        probability = float(market_probability)
        favorite = float(favorite_probability)
    except (TypeError, ValueError):
        probability = favorite = None
    if probability is None or not math.isfinite(probability) or not 0.0 < probability < 1.0:
        return "유효한 시장확률이 없어 자동 추천 제외"
    if (
        probability is not None
        and favorite is not None
        and math.isfinite(favorite)
        and probability < favorite - 1e-9
    ):
        return "시장 최유력 선택이 아닌 역배 — 검증 우위가 없어 자동 추천 제외"
    return None


def is_recommendable_market(market: object) -> bool:
    return recommendation_exclusion_reason(market) is None
