"""운영에 승격되지 않은 목표배당 동적 조합 연구 코드.

기본 추천 경로와 분리한다. 워크포워드 실험이 고정 정책을 재현해서 이길 때만
``today_combo``와 브라우저 코드로 옮긴다.
"""
from __future__ import annotations

import math

from recommendation_policy import MAX_AUTO_RECOMMENDATION_ODDS

ODDS_LOG_BUCKET = 0.002


def _probability(candidate: dict) -> float | None:
    try:
        value = float(candidate.get("market_prob"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0.0 < value < 1.0 else None


def pick_target_legs(
    candidates: list[dict],
    target: float,
    min_legs: int = 2,
    max_legs: int = 4,
) -> list[dict] | None:
    """목표 총배당 범위에서 결합 시장확률이 큰 2~4폴을 근사 탐색한다."""
    lower, upper = float(target) * 0.95, float(target) * 1.15
    groups: dict[str, list[dict]] = {}
    for candidate in candidates:
        probability = _probability(candidate)
        try:
            odds = float(candidate.get("odds"))
            overround = float(candidate.get("overround"))
        except (TypeError, ValueError):
            continue
        if probability is None or not 1.0 < odds < MAX_AUTO_RECOMMENDATION_ODDS:
            continue
        if not 1.0 < overround <= 1.40:
            continue
        groups.setdefault(str(candidate["event_key"]), []).append(candidate)

    # (다리수, log배당칸) -> (log확률, log배당, 환급률곱, 선택)
    states: dict[tuple[int, int], tuple[float, float, float, tuple[dict, ...]]] = {
        (0, 0): (0.0, 0.0, 1.0, tuple())
    }
    for event in sorted(groups):
        previous = list(states.values())
        updates = dict(states)
        for log_probability, log_odds, payout, picks in previous:
            if len(picks) >= max_legs:
                continue
            for candidate in groups[event]:
                probability = _probability(candidate)
                odds = float(candidate["odds"])
                next_log_odds = log_odds + math.log(odds)
                if math.exp(next_log_odds) > upper:
                    continue
                next_picks = (*picks, candidate)
                next_log_probability = log_probability + math.log(probability)
                next_payout = payout / float(candidate["overround"])
                key = (len(next_picks), int(round(next_log_odds / ODDS_LOG_BUCKET)))
                current = updates.get(key)
                score = (next_log_probability, next_payout,
                         -abs(next_log_odds - math.log(target)))
                current_score = ((current[0], current[2],
                                  -abs(current[1] - math.log(target)))
                                 if current else None)
                if current_score is None or score > current_score:
                    updates[key] = (
                        next_log_probability, next_log_odds, next_payout, next_picks)
        states = updates

    feasible = []
    for log_probability, log_odds, payout, picks in states.values():
        actual_odds = math.exp(log_odds)
        if min_legs <= len(picks) <= max_legs and lower <= actual_odds <= upper:
            feasible.append((
                log_probability,
                log_probability + log_odds,
                payout,
                -abs(log_odds - math.log(target)),
                picks,
            ))
    return list(max(feasible, key=lambda row: row[:-1])[-1]) if feasible else None
