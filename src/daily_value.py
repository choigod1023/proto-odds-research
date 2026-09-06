"""Daily highlights across stored canonical picks; never change pick direction.

This is a heuristic risk limit, not an empirically optimized prediction model.
Keep the unrounded JSON contract in sync with the frontend daily-value policy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re


POLICY_VERSION = "daily-value-v1"
MIN_HIT = 0.50
MIN_RETURN = -0.15
BASE_PER_LEAGUE = 3
EPSILON = 1e-12
KST = timezone(timedelta(hours=9))


def _finite(value: object) -> float | None:
    # Accept numeric strings from older artifacts, but not booleans/containers.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not re.fullmatch(
        r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value.strip()
    ):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _probability(value: object) -> float | None:
    number = _finite(value)
    return number if number is not None and 0 < number < 1 else None


def _alias(candidate: dict, field: str, alias: str) -> object:
    value = candidate.get(field)
    return candidate.get(alias) if value is None else value


def daily_value_metrics(candidate: dict | None) -> dict:
    """Compute point and conservative returns, with strict validation markers."""
    candidate = candidate or {}
    metrics = {
        "policy_version": POLICY_VERSION,
        "probability": None,
        "comparison_probability": None,
        "break_even_probability": None,
        "expected_return": None,
        "comparison_return": None,
        "validated_probability": False,
        "validated_interval": False,
        "qualifies": False,
    }
    odds = _finite(_alias(candidate, "odds", "배당"))
    market = _probability(_alias(candidate, "market_prob", "시장확률"))
    if odds is None or odds <= 1 or market is None:
        return metrics

    final = _probability(candidate.get("predicted_hit_prob"))
    validated = (
        candidate.get("decision_pipeline_applied") is True
        and candidate.get("has_validated_edge") is True
        and final is not None
    )
    estimate = final if validated else market
    comparison = min(market, estimate)
    interval = candidate.get("probability_interval")
    trusted_interval = False
    if (
        validated
        and candidate.get("validated_uncertainty_available") is True
        and candidate.get("uncertainty_source") == "validated_residual_interval"
        and isinstance(interval, list)
        and len(interval) == 2
    ):
        lower, upper = map(_probability, interval)
        bound = _probability(candidate.get("probability_lower_bound"))
        trusted_interval = (
            lower is not None and upper is not None and bound is not None
            and lower <= estimate <= upper
            # The producer serializes the stated bound to four decimals while
            # retaining full precision for the interval endpoints.
            and abs(bound - lower) <= 5.0001e-5
        )
        if trusted_interval:
            comparison = lower

    expected_return = estimate * odds - 1
    comparison_return = comparison * odds - 1
    metrics.update({
        "probability": estimate,
        "comparison_probability": comparison,
        "break_even_probability": 1 / odds,
        "expected_return": expected_return,
        "comparison_return": comparison_return,
        "validated_probability": validated,
        "validated_interval": trusted_interval,
        "qualifies": (estimate >= MIN_HIT
                      and comparison_return >= MIN_RETURN - EPSILON),
    })
    return metrics


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _date_parts(candidate: dict) -> tuple[str, str | None]:
    """ISO kickoff -> KST; never read the clock to infer a missing year."""
    try:
        kickoff = datetime.fromisoformat(_clean(candidate.get("kickoff_at")))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=KST)
        day = kickoff.astimezone(KST).date().isoformat()
        return day[:4], day[5:]
    except (ValueError, OverflowError):
        match = re.match(r"^(\d{2})\.(\d{2})", _clean(candidate.get("date")))
        return _clean(candidate.get("year")), f"{match[1]}-{match[2]}" if match else None


def _day_league(candidate: dict, known_years: dict[str, set[str]]) -> tuple[str, str]:
    year, month_day = _date_parts(candidate)
    known = known_years.get(month_day, set())
    inferred = next(iter(known)) if len(known) == 1 else "undated"
    day = f"{year or inferred}-{month_day}" if month_day else "undated"
    return day, _clean(candidate.get("league")) or "리그 미분류"


def _rank(candidate: dict) -> tuple:
    metrics = candidate["daily_recommendation"]
    label = candidate.get("market_label")
    if label is None:
        label = candidate.get("label")
    key = "|".join(map(_clean, (
        candidate.get("round"), _alias(candidate, "game_no", "게임번호"),
        candidate.get("market"), label, _alias(candidate, "sel", "선택"),
    )))
    return (
        -metrics["comparison_return"], -metrics["expected_return"],
        -metrics["probability"],
        # JS lexical comparison uses UTF-16 code units, with no locale collation.
        _clean(candidate.get("kickoff_at") or candidate.get("date")).encode(
            "utf-16-be", errors="surrogatepass"),
        key.encode("utf-16-be", errors="surrogatepass"),
    )


def annotate_daily_values(candidates: list[dict]) -> list[dict]:
    """Return copies in input order, including every excluded canonical pick.

    Reason precedence: safety, invalid, hit_floor, return_floor, fallback,
    then rank/base/validated_extra. 'fallback' excludes a low-odds row when
    a qualifying primary exists; selected low-odds rows use 'base' or
    'validated_extra'. Ranks are one-based within the retained league pool.
    This pure full-list helper intentionally does not special-case started locks.
    """
    annotated = []
    groups: dict[tuple[str, str], list[dict]] = {}
    known_years: dict[str, set[str]] = {}
    for original in candidates:
        year, month_day = _date_parts(original or {})
        if year and month_day:
            known_years.setdefault(month_day, set()).add(year)
    for original in candidates:
        candidate = dict(original or {})
        metrics = daily_value_metrics(candidate)
        decision = {
            **metrics, "recommended": False, "league_rank": None,
            "reason_code": "rank",
        }
        candidate["daily_recommendation"] = decision
        annotated.append(candidate)
        odds = _finite(_alias(candidate, "odds", "배당"))
        if (
            _clean(candidate.get("market")) == "홀짝"
            or odds is None or odds <= 1 or odds >= 2.2
            or candidate.get("is_market_favorite") is False
            or candidate.get("final_reversal") is True
            or candidate.get("최종전환") is True
        ):
            decision["reason_code"] = "safety"
        elif metrics["probability"] is None:
            decision["reason_code"] = "invalid"
        elif metrics["probability"] < MIN_HIT:
            decision["reason_code"] = "hit_floor"
        elif not metrics["qualifies"]:
            decision["reason_code"] = "return_floor"
        else:
            groups.setdefault(_day_league(candidate, known_years), []).append(candidate)

    for rows in groups.values():
        primary = [row for row in rows if _finite(_alias(row, "odds", "배당")) >= 1.5]
        if primary:
            for row in rows:
                if _finite(_alias(row, "odds", "배당")) < 1.5:
                    row["daily_recommendation"]["reason_code"] = "fallback"
        for rank, row in enumerate(sorted(primary or rows, key=_rank), start=1):
            decision = row["daily_recommendation"]
            decision["league_rank"] = rank
            if rank <= BASE_PER_LEAGUE:
                decision.update(recommended=True, reason_code="base")
            elif decision["validated_interval"] and decision["comparison_return"] > 0:
                decision.update(recommended=True, reason_code="validated_extra")
    return annotated
