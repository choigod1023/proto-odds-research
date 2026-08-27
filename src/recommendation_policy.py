"""자동 추천에 들어갈 수 있는 마켓의 검증 정책.

가격과 모델 확률을 계산할 수 있다는 사실만으로 추천 자격이 생기지는 않는다.
시간순 외부표본에서 시장 대비 우위를 입증하지 못한 마켓은 화면에는 남기되,
경기별 추천과 오늘의 조합에서는 제외한다.
"""
from __future__ import annotations

import math


PREFERRED_RECOMMENDATION_ODDS = 1.5
# 이전 산출물·브라우저와의 호환용 별칭이다. 이제 1.50은 제외 하한이 아니라
# 1순위와 보조 추천을 나누는 경계다.
MIN_AUTO_RECOMMENDATION_ODDS = PREFERRED_RECOMMENDATION_ODDS
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


def recommendation_priority(odds: object) -> int:
    """1.50 이상은 1순위, 그 미만의 유효 배당은 보조 추천으로 분류한다."""
    try:
        price = float(odds)
    except (TypeError, ValueError):
        return -1
    if not math.isfinite(price) or price <= 1.0 or price >= MAX_AUTO_RECOMMENDATION_ODDS:
        return -1
    return 1 if price >= PREFERRED_RECOMMENDATION_ODDS else 0


def is_recommendable_market(market: object) -> bool:
    return recommendation_exclusion_reason(market) is None
