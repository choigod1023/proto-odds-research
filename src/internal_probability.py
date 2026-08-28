"""경기 내적 요소를 시장보다 우선하는 운영 확률."""
from __future__ import annotations

import math

INTERNAL_MODEL_WEIGHT = 0.70
MARKET_ANCHOR_WEIGHT = 0.30
MAX_PLAYER_DELTA = 0.10


def _number(value: object) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _side(option: dict) -> str | None:
    choice = str(option.get("선택") or option.get("name") or "").strip()
    if choice in {"홈", "승", "홈승"}:
        return "home"
    if choice in {"원정", "패", "원정승"}:
        return "away"
    return None


def _pitcher_value(starter: dict | None) -> float | None:
    stats = (starter or {}).get("stats") or {}
    for key in ("xfip", "fip", "era"):
        value = _number(stats.get(key))
        if value is not None and 0.0 < value < 15.0:
            return value
    return None


def _lineup_ops(players: list[dict] | None) -> float | None:
    values = []
    for player in players or []:
        if player.get("position") == "투수":
            continue
        value = _number((player.get("stats") or {}).get("ops"))
        if value is not None and 0.2 < value < 1.5:
            values.append(value)
    return sum(values) / len(values) if len(values) >= 3 else None


def baseball_player_delta(game: dict, option: dict) -> tuple[float, list[dict]]:
    """야구 승패 선택의 선수 보정치와 감사 가능한 기여도를 반환한다."""
    if game.get("sport") != "bs" or option.get("market") != "승패":
        return 0.0, []
    side = _side(option)
    if side is None:
        return 0.0, []
    sign = 1.0 if side == "home" else -1.0
    info = game.get("선발") if isinstance(game.get("선발"), dict) else {}
    factors: list[dict] = []
    home_delta = 0.0

    home_pitching = _pitcher_value(info.get("home_detail"))
    away_pitching = _pitcher_value(info.get("away_detail"))
    if home_pitching is not None and away_pitching is not None:
        raw = _clip((away_pitching - home_pitching) * 0.025, -0.075, 0.075)
        confirmed = (info.get("starter_status") or {}).get("state") == "confirmed"
        confidence = 1.0 if confirmed else 0.80
        contribution = raw * confidence
        home_delta += contribution
        factors.append({
            "id": "starting_pitcher", "home_value": round(home_pitching, 3),
            "away_value": round(away_pitching, 3), "confidence": confidence,
            "home_probability_delta": round(contribution, 4),
        })

    lineups = info.get("lineups") or {}
    home_ops = _lineup_ops(lineups.get("home"))
    away_ops = _lineup_ops(lineups.get("away"))
    if home_ops is not None and away_ops is not None:
        raw = _clip((home_ops - away_ops) * 0.30, -0.045, 0.045)
        state = (info.get("lineup_status") or {}).get("state")
        confidence = 1.0 if state == "official_today" else 0.45
        contribution = raw * confidence
        home_delta += contribution
        factors.append({
            "id": "batting_lineup", "home_value": round(home_ops, 3),
            "away_value": round(away_ops, 3), "confidence": confidence,
            "home_probability_delta": round(contribution, 4),
        })

    unavailable = info.get("unavailable") or {}
    home_out, away_out = len(unavailable.get("home") or []), len(unavailable.get("away") or [])
    if home_out or away_out:
        contribution = _clip((away_out - home_out) * 0.006, -0.024, 0.024)
        home_delta += contribution
        factors.append({
            "id": "availability", "home_value": home_out, "away_value": away_out,
            "confidence": 0.65, "home_probability_delta": round(contribution, 4),
        })

    return _clip(sign * home_delta, -MAX_PLAYER_DELTA, MAX_PLAYER_DELTA), factors


def internal_probability(game: dict, option: dict) -> dict:
    """구조모델 70%·시장 30%에 선수 보정을 적용한 최종 확률."""
    market, model = _number(option.get("시장확률")), _number(option.get("모델확률"))
    if market is None or not 0.0 < market < 1.0:
        return {"final": None, "basis": "unavailable", "factors": []}
    player_delta, factors = baseball_player_delta(game, option)
    internal_base = model if model is not None and 0.0 < model < 1.0 else market
    internal = _clip(internal_base + player_delta, 0.02, 0.98)
    final = _clip(INTERNAL_MODEL_WEIGHT * internal + MARKET_ANCHOR_WEIGHT * market, 0.02, 0.98)
    return {
        "final": round(final, 4), "internal": round(internal, 4),
        "market": round(market, 4), "player_delta": round(player_delta, 4),
        "basis": "internal_context_blend_v1",
        "weights": {"internal": INTERNAL_MODEL_WEIGHT, "market": MARKET_ANCHOR_WEIGHT},
        "factors": factors,
    }
