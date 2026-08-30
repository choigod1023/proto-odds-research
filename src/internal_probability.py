"""야구 경기 내부 자료를 감사 가능한 확률 후보로 변환한다.

이 모듈은 자료가 충분한 MLB/KBO/NPB 승패만 다룬다. 출처와 관측 시각,
양 팀 선발 지표가 없으면 시장 확률을 바꾸지 않는 fail-close 계약이다.
"""
from __future__ import annotations

import math
import json
from pathlib import Path

INTERNAL_MODEL_WEIGHT = 0.70
MARKET_ANCHOR_WEIGHT = 0.30
MAX_PLAYER_DELTA = 0.10
SUPPORTED_LEAGUES = frozenset({"MLB", "KBO", "NPB"})
OPERATING_VERSION = "internal-context-blend-v2"
VALIDATION_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "internal_factor_validation.json"


def league_is_promoted(league: str, path: Path = VALIDATION_PATH) -> bool:
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if artifact.get("schema") != "internal-factor-rolling-validation-v1":
        return False
    row = (artifact.get("leagues") or {}).get(str(league).upper()) or {}
    return row.get("status") == "promoted" and int(row.get("future_sample") or 0) >= 300


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _side(option: dict) -> str | None:
    choice = str(option.get("선택") or option.get("name") or "").strip()
    if choice in {"홈", "승", "홈승"}:
        return "home"
    if choice in {"원정", "패", "원정승"}:
        return "away"
    return None


def _pitcher_value(starter: dict | None) -> tuple[float | None, str | None]:
    stats = (starter or {}).get("stats") or {}
    for key in ("xfip", "fip", "era"):
        value = _number(stats.get(key))
        if value is not None and 0.0 < value < 15.0:
            return value, key
    return None, None


def _lineup_ops(players: list[dict] | None) -> float | None:
    values = []
    for player in players or []:
        if player.get("position") == "투수":
            continue
        value = _number((player.get("stats") or {}).get("ops"))
        if value is not None and 0.2 < value < 1.5:
            values.append(value)
    return sum(values) / len(values) if len(values) >= 3 else None


def activation_reason(game: dict, option: dict) -> str | None:
    """운영 계수를 켜지 못하는 첫 번째 이유를 반환한다."""
    if game.get("sport") != "bs" or str(game.get("league") or "").upper() not in SUPPORTED_LEAGUES:
        return "unsupported_sport_or_league"
    if option.get("market") != "승패" or _side(option) is None:
        return "unsupported_market_or_side"
    market, model = _number(option.get("시장확률")), _number(option.get("모델확률"))
    if market is None or not 0.0 < market < 1.0:
        return "market_probability_missing"
    if model is None or not 0.0 < model < 1.0:
        return "score_model_probability_missing"
    info = game.get("선발") if isinstance(game.get("선발"), dict) else {}
    if not info.get("source") or not info.get("updated_at"):
        return "player_provenance_missing"
    if not info.get("first_seen_at") or not info.get("revision_id"):
        return "player_first_seen_revision_missing"
    home, _ = _pitcher_value(info.get("home_detail"))
    away, _ = _pitcher_value(info.get("away_detail"))
    if home is None or away is None:
        return "both_starting_pitcher_metrics_required"
    return None


def baseball_player_delta(game: dict, option: dict) -> tuple[float, list[dict]]:
    """야구 승패 선택의 선수 보정치와 감사 가능한 기여도를 반환한다."""
    if activation_reason(game, option) is not None:
        return 0.0, []
    side = _side(option)
    sign = 1.0 if side == "home" else -1.0
    info = game["선발"]
    factors: list[dict] = []
    home_delta = 0.0

    home_pitching, home_metric = _pitcher_value(info.get("home_detail"))
    away_pitching, away_metric = _pitcher_value(info.get("away_detail"))
    raw = _clip((away_pitching - home_pitching) * 0.025, -0.075, 0.075)
    confirmed = (info.get("starter_status") or {}).get("state") == "confirmed"
    confidence = 1.0 if confirmed else 0.80
    contribution = raw * confidence
    home_delta += contribution
    factors.append({
        "id": "starting_pitcher", "home_value": round(home_pitching, 3),
        "away_value": round(away_pitching, 3),
        "metric": home_metric if home_metric == away_metric else f"{home_metric}/{away_metric}",
        "confidence": confidence, "home_probability_delta": round(contribution, 4),
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
    home_out = len(unavailable.get("home") or [])
    away_out = len(unavailable.get("away") or [])
    if home_out or away_out:
        contribution = _clip((away_out - home_out) * 0.006, -0.024, 0.024)
        home_delta += contribution
        factors.append({
            "id": "availability", "home_value": home_out, "away_value": away_out,
            "confidence": 0.65, "home_probability_delta": round(contribution, 4),
        })

    return _clip(sign * home_delta, -MAX_PLAYER_DELTA, MAX_PLAYER_DELTA), factors


def internal_probability(game: dict, option: dict) -> dict:
    """운영 가능할 때만 구조모델·시장·선수자료를 결합한다."""
    market = _number(option.get("시장확률"))
    reason = activation_reason(game, option)
    if reason is not None:
        return {
            "final": market, "internal": None, "market": market,
            "player_delta": 0.0, "basis": "shin_market",
            "status": "ineligible", "reason": reason, "factors": [],
        }
    model = _number(option.get("모델확률"))
    player_delta, factors = baseball_player_delta(game, option)
    internal = _clip(model + player_delta, 0.02, 0.98)
    final = _clip(INTERNAL_MODEL_WEIGHT * internal + MARKET_ANCHOR_WEIGHT * market, 0.02, 0.98)
    if not league_is_promoted(str(game.get("league") or "")):
        return {
            "final": round(market, 4), "internal": round(internal, 4),
            "market": round(market, 4), "player_delta": round(player_delta, 4),
            "basis": "shin_market", "status": "shadow_only",
            "reason": "league_future_validation_not_promoted", "factors": factors,
            "shadow_final": round(final, 4),
        }
    return {
        "final": round(final, 4), "internal": round(internal, 4),
        "market": round(market, 4), "player_delta": round(player_delta, 4),
        "basis": OPERATING_VERSION, "status": "operational", "reason": None,
        "weights": {"internal": INTERNAL_MODEL_WEIGHT, "market": MARKET_ANCHOR_WEIGHT},
        "factors": factors,
    }
