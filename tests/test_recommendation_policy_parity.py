import re
from pathlib import Path

from src.recommendation_policy import (
    MAX_AUTO_RECOMMENDATION_ODDS,
    MIN_AUTO_RECOMMENDATION_ODDS,
    PREFERRED_RECOMMENDATION_ODDS,
    UPSET_MAX_MODEL_GAP,
    UPSET_MAX_MODEL_PROBABILITY,
    UPSET_MAX_ODDS,
    UPSET_MIN_MARKET_PROBABILITY,
    UPSET_MIN_MODEL_GAP,
    UPSET_MIN_MODEL_PROBABILITY,
    UPSET_MIN_ODDS,
)
from src.today_combo import (
    DAILY_CHALLENGE_MAX_TARGET,
    DAILY_CHALLENGE_MIN_HIT,
    DAILY_CHALLENGE_MIN_ROI,
    DAILY_CHALLENGE_ROI_TOLERANCE,
)


ROOT = Path(__file__).resolve().parents[1]


def _js_number(source: str, name: str) -> float:
    match = re.search(rf"export const {name} = (-?\d+(?:\.\d+)?);", source)
    assert match, f"브라우저 정책 상수 {name}을 찾을 수 없다"
    return float(match.group(1))


def test_browser_and_generator_recommendation_thresholds_stay_in_sync():
    plan_js = (ROOT / "web/src/lib/today-plan.js").read_text(encoding="utf-8")
    policy_js = (ROOT / "web/src/lib/recommendation-policy.js").read_text(encoding="utf-8")

    assert _js_number(policy_js, "MIN_AUTO_ODDS") == MIN_AUTO_RECOMMENDATION_ODDS
    assert _js_number(policy_js, "PREFERRED_AUTO_ODDS") == PREFERRED_RECOMMENDATION_ODDS
    assert _js_number(policy_js, "MAX_AUTO_ODDS") == MAX_AUTO_RECOMMENDATION_ODDS
    assert _js_number(policy_js, "UPSET_MIN_ODDS") == UPSET_MIN_ODDS
    assert _js_number(policy_js, "UPSET_MAX_ODDS") == UPSET_MAX_ODDS
    assert _js_number(policy_js, "UPSET_MIN_MARKET_PROBABILITY") == UPSET_MIN_MARKET_PROBABILITY
    assert _js_number(policy_js, "UPSET_MIN_MODEL_PROBABILITY") == UPSET_MIN_MODEL_PROBABILITY
    assert _js_number(policy_js, "UPSET_MAX_MODEL_PROBABILITY") == UPSET_MAX_MODEL_PROBABILITY
    assert _js_number(policy_js, "UPSET_MIN_MODEL_GAP") == UPSET_MIN_MODEL_GAP
    assert _js_number(policy_js, "UPSET_MAX_MODEL_GAP") == UPSET_MAX_MODEL_GAP
    assert _js_number(plan_js, "DAILY_CHALLENGE_MIN_ROI") == DAILY_CHALLENGE_MIN_ROI
    assert _js_number(plan_js, "DAILY_CHALLENGE_MAX_TARGET") == DAILY_CHALLENGE_MAX_TARGET
    assert _js_number(plan_js, "DAILY_CHALLENGE_ROI_TOLERANCE") == DAILY_CHALLENGE_ROI_TOLERANCE

    hit_match = re.search(
        r"export const DAILY_CHALLENGE_MIN_HIT = \{\s*3:\s*([\d.]+)\s*\};",
        plan_js,
    )
    assert hit_match, "브라우저 목표별 적중 하한을 찾을 수 없다"
    assert {3: float(hit_match.group(1))} == DAILY_CHALLENGE_MIN_HIT
