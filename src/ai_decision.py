"""시장 기준 예측과 AI 역할을 하나의 불변 스냅샷으로 만든다.

수치 모델, 선수·출전 자료, 생성형 AI가 한 화면에 함께 나오더라도 실제 확률에
무엇이 들어갔는지를 숨기지 않는 것이 목적이다. 현재 검증된 운영식은
``p_final = p_shin_market`` 이므로 구조 모델의 차이는 shadow 로만 기록한다.
추천 정렬은 시장확률 필드를 직접 보지 않고 이 최종확률을 사용한다. 이후 검증된
잔차모델이 승격되면 같은 정렬 계약 안에서 보정된 적중확률이 자동으로 우선된다.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Iterable, Mapping

from probability_pipeline import (
    ArtifactValidationError,
    apply_artifact,
    artifact_hash,
)

from recommendation_policy import (
    automatic_selection_exclusion_reason,
    qualified_underdog,
    recommendation_priority,
    underdog_score,
)
from internal_probability import OPERATING_VERSION as INTERNAL_OPERATING_VERSION
from internal_probability import internal_probability


SCHEMA_VERSION = "decision-snapshot-v2"
OPERATING_MODEL_VERSION = "shin-market-anchor-v1"
SHADOW_MODEL_VERSION = "score-distribution-shadow-v1"

USAGE_CONSUMERS = ("market_baseline", "ai_residual", "decision_gate", "explainer")
USAGE_STATUSES = {"used", "shadow", "context_only", "ignored", "missing"}
STAGE_IDS = ("market", "structured_ai", "availability_ai", "language_ai")
PROMOTED_ARTIFACT_HASHES: frozenset[str] = frozenset()
POLICY_AUTHORIZED_MODELS = frozenset({INTERNAL_OPERATING_VERSION})
EVIDENCE_MANIFEST = {
    "market_price": {"label": "동일 시점 프로토 배당", "type": "market"},
    "team_performance": {"label": "팀 경기력 기록", "type": "team"},
    "lineup": {"label": "선발·라인업", "type": "player"},
    "availability": {"label": "결장·출전 상태", "type": "player"},
    "cross_market": {"label": "교차 마켓 진단", "type": "market"},
}


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def hit_probability(option: dict) -> float | None:
    """추천 정렬에 쓰는 최종 예상 적중확률.

    생성 단계가 검증된 AI 보정을 적용했으면 ``최종확률``에 반영한다. 아직 승격된
    모델이 없거나 구형 산출물이면 동일 시점 Shin 시장확률로 안전하게 복귀한다.
    """
    final = _number(option.get("최종확률"))
    if final is not None and 0.0 < final < 1.0:
        return final
    market = _number(option.get("시장확률"))
    return market if market is not None and 0.0 < market < 1.0 else None


def decision_manifest() -> dict:
    """경기마다 반복하지 않는 판정 계약 카탈로그."""
    return {
        "schema_version": SCHEMA_VERSION,
        "stage_ids": list(STAGE_IDS),
        "evidence": EVIDENCE_MANIFEST,
        "usage_consumers": list(USAGE_CONSUMERS),
        "usage_statuses": sorted(USAGE_STATUSES),
    }


def _stable_id(prefix: str, *parts: object) -> str:
    # 0, False, None과 빈 문자열을 서로 다른 입력으로 보존한다.
    raw = "|".join(json.dumps(
        part.strip() if isinstance(part, str) else part,
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def event_id(game: dict) -> str:
    """재발매 상품과 무관한 실제 경기 식별자."""
    source_id = game.get("source_event_id") or game.get("match_id")
    if source_id:
        return _stable_id("evt", "source", game.get("sport"), source_id)
    return _stable_id(
        "evt", "fallback", game.get("sport"), game.get("year"),
        game.get("date"), game.get("league"),
        game.get("home"), game.get("away"),
    )


def selection_id(game: dict, option: dict) -> str:
    """용지의 한 선택지를 가리키는 안정적인 식별자."""
    return _stable_id(
        "sel", event_id(game), option.get("market"),
        option.get("label"), option.get("line"), option.get("선택"),
    )


def offer_id(game: dict, option: dict) -> str:
    """동일 결과 의미가 어느 회차·게임번호에 발매됐는지를 구분한다."""
    return _stable_id(
        "off", selection_id(game, option), game.get("round"),
        option.get("게임번호"),
    )


def _residual_features(
    game: Mapping, option: Mapping, favorite_probability: float | None,
) -> dict[str, float | None]:
    """Stable inference features available for future residual artifacts."""

    market = _number(option.get("시장확률"))
    model = _number(option.get("모델확률"))
    home = _number(game.get("lam_home"))
    away = _number(game.get("lam_away"))
    return {
        "market_probability": market,
        "favorite_market_probability": favorite_probability,
        "score_model_probability": model,
        "score_market_gap": (
            None if market is None or model is None else model - market
        ),
        "home_lambda": home,
        "away_lambda": away,
        "lambda_difference": (
            None if home is None or away is None else home - away
        ),
        "lambda_total": (
            None if home is None or away is None else home + away
        ),
        "odds": _number(option.get("배당")),
    }


def annotate_options(
    game: dict,
    *,
    probability_artifact: Mapping | None = None,
) -> None:
    """Attach stable IDs and apply only a code-reviewed promoted artifact."""

    game["event_id"] = event_id(game)
    favorite_by_market: dict[tuple, float] = {}
    for option in game.get("options", []):
        probability = _number(option.get("시장확률"))
        if probability is None:
            continue
        key = (option.get("market"), option.get("label"), option.get("line"),
               option.get("게임번호"))
        favorite_by_market[key] = max(favorite_by_market.get(key, 0.0), probability)

    digest = None
    if probability_artifact is not None:
        try:
            digest = artifact_hash(probability_artifact)
        except (ArtifactValidationError, TypeError, ValueError):
            digest = None
    pipeline_statuses: list[str] = []
    pipeline_reasons: list[str] = []

    for option in game.get("options", []):
        option["selection_id"] = selection_id(game, option)
        option["offer_id"] = offer_id(game, option)
        market = _number(option.get("시장확률"))
        option["최종확률"] = None if market is None else round(market, 4)
        option["확률근거"] = "shin_market" if market is not None else "unavailable"
        option["AI반영"] = False
        model = _number(option.get("모델확률"))
        option["AI잔차"] = (
            None if market is None or model is None else round(model - market, 4)
        )
        for field in (
            "잔차모델확률", "잔차모델구간", "잔차모델상태", "잔차모델사유",
            "잔차모델해시",
        ):
            option.pop(field, None)
        for field in ("내부확률", "선수보정", "내부요인", "내부모델상태", "내부모델사유"):
            option.pop(field, None)
        key = (option.get("market"), option.get("label"), option.get("line"),
               option.get("게임번호"))
        if (
            market is not None
            and 0.0 < market < 1.0
            and probability_artifact is not None
        ):
            result = apply_artifact(
                market,
                sport=game.get("sport") or "unknown",
                market=option.get("market") or "unknown",
                features=_residual_features(
                    game, option, favorite_by_market.get(key)
                ),
                artifact=probability_artifact,
            )
            reviewed = (
                result.applied
                and digest is not None
                and digest in PROMOTED_ARTIFACT_HASHES
            )
            status = (
                "promoted" if reviewed
                else "shadow_only" if result.shadow_probability is not None
                else "market_fallback"
            )
            reason = (
                result.reason
                if not result.applied or reviewed
                else "artifact promotion claim is not in the code-reviewed allowlist"
            )
            option["잔차모델확률"] = (
                None if result.shadow_probability is None
                else round(result.shadow_probability, 4)
            )
            option["잔차모델구간"] = (
                None if result.shadow_interval is None
                else [round(value, 4) for value in result.shadow_interval]
            )
            option["잔차모델상태"] = status
            option["잔차모델사유"] = reason
            option["잔차모델해시"] = digest
            option["최종확률"] = round(
                result.probability if reviewed else market, 4
            )
            option["확률근거"] = (
                "validated_market_residual" if reviewed else "shin_market"
            )
            option["AI반영"] = reviewed
            pipeline_statuses.append(status)
            pipeline_reasons.append(reason)
        # 승격된 잔차 artifact가 없을 때만 코드 리뷰된 야구 내부식이 운영 후보가 된다.
        # 둘을 동시에 더하지 않아 같은 구조 신호가 중복 반영되는 일을 막는다.
        if not option.get("AI반영"):
            internal = internal_probability(game, option)
            option["내부확률"] = internal.get("internal")
            option["선수보정"] = internal.get("player_delta")
            option["내부요인"] = internal.get("factors")
            option["내부모델상태"] = internal.get("status")
            option["내부모델사유"] = internal.get("reason")
            if internal.get("status") == "operational":
                option["최종확률"] = internal["final"]
                option["확률근거"] = INTERNAL_OPERATING_VERSION
                option["AI반영"] = True
                pipeline_statuses.append("internal_operational")
        is_upset = qualified_underdog(
            option.get("market"), option.get("배당"), market,
            favorite_by_market.get(key), model,
        )
        option["이변후보"] = is_upset
        option["이변점수"] = (
            round(underdog_score(market, model), 4) if is_upset else None
        )
        option["이변근거"] = (
            "시장 역배·1.50~3.00 미만·검증 전 모델 우위 8~25%p"
            if is_upset else None
        )

    game["probability_pipeline"] = {
        "status": (
            "operational" if "promoted" in pipeline_statuses
            else "operational" if "internal_operational" in pipeline_statuses
            else "shadow_only" if "shadow_only" in pipeline_statuses
            else "market_fallback" if probability_artifact is not None
            else "market_baseline"
        ),
        "artifact_hash": digest,
        "allowlisted": bool(digest and digest in PROMOTED_ARTIFACT_HASHES),
        "affects_probability": "promoted" in pipeline_statuses,
        "reason": (
            next(iter(dict.fromkeys(pipeline_reasons)), None)
            if probability_artifact is not None
            else "probability artifact is absent; Shin market retained"
        ),
    }
    if "internal_operational" in pipeline_statuses and "promoted" not in pipeline_statuses:
        game["probability_pipeline"].update({
            "operating_version": INTERNAL_OPERATING_VERSION,
            "policy_authorized": True,
            "affects_probability": True,
            "reason": "baseball internal-factor activation gates passed",
        })


def choose_market_reference(options: list[dict]) -> dict | None:
    """가격대 우선선 안에서 최종 예상 적중확률이 가장 높은 선택을 고른다.

    이변 후보는 설명용 shadow 신호로만 남긴다. 시간순 외부검증을 통과하지 않은
    모델 차이로 운영 방향을 뒤집지 않는다. 1.50~2.20 미만 후보가 하나라도 있으면
    그 후보군을 먼저 쓰고, 없을 때만 1.50 미만 최유력을 보조 선택으로 남긴다.
    """
    favorite_by_market: dict[tuple, float] = {}
    for option in options:
        # 같은 문서를 재계산할 때 예전 배당에서 붙은 제외 사유가 남지 않게 한다.
        option.pop("제외", None)
        option.pop("추천점수", None)
        option.pop("예상적중확률", None)
        option.pop("선택근거", None)
        option.pop("추천우선순위", None)
        option.pop("최종전환", None)
        probability = _number(option.get("시장확률"))
        if probability is None:
            continue
        key = (option.get("market"), option.get("label"), option.get("line"),
               option.get("게임번호"))
        favorite_by_market[key] = max(favorite_by_market.get(key, 0.0), probability)

    eligible: list[dict] = []
    for option in options:
        key = (option.get("market"), option.get("label"), option.get("line"),
               option.get("게임번호"))
        probability = _number(option.get("시장확률"))
        model = _number(option.get("모델확률"))
        if qualified_underdog(
            option.get("market"), option.get("배당"), probability,
            favorite_by_market.get(key), model,
        ):
            option["이변후보"] = True
            option["이변점수"] = round(underdog_score(probability, model), 4)
        reason = automatic_selection_exclusion_reason(
            option.get("market"), option.get("배당"), probability,
            favorite_by_market.get(key),
        )
        if reason:
            option["제외"] = reason
            continue
        if probability is None:
            option["제외"] = "시장확률을 계산할 수 없음"
            continue
        predicted_hit = hit_probability(option)
        if predicted_hit is None:
            option["제외"] = "최종 예상 적중확률을 계산할 수 없음"
            continue
        option["예상적중확률"] = round(predicted_hit, 4)
        option["추천점수"] = round(predicted_hit, 4)
        option["추천우선순위"] = (
            "primary" if recommendation_priority(option.get("배당")) == 1 else "fallback"
        )
        source = str(option.get("확률근거") or "shin_market")
        option["선택근거"] = (
            "validated_final_hit_probability"
            if option.get("AI반영") is True
            else f"{source}_hit_probability"
        )
        eligible.append(option)
    if not eligible:
        return None
    primary = [
        option for option in eligible if option.get("추천우선순위") == "primary"
    ]
    pool = primary or eligible
    return max(pool, key=lambda option: (
        hit_probability(option) or 0.0,
        _number(option.get("시장확률")) or 0.0,
        -(_number(option.get("배당")) or 999.0),
        str(option.get("selection_id") or ""),
    ))


def _usage(consumer: str, status: str) -> str:
    if consumer not in USAGE_CONSUMERS:
        raise ValueError(f"unknown evidence consumer: {consumer}")
    if status not in USAGE_STATUSES:
        raise ValueError(f"unknown evidence usage status: {status}")
    return status


def _all_usage(
    *, market: str, residual: str, gate: str, explainer: str,
    residual_reason: str | None = None, gate_reason: str | None = None,
    explainer_reason: str | None = None,
) -> dict:
    # 소비자 이름을 매 행마다 반복하는 배열 대신 상태 맵을 쓴다. 같은 의미를 더 작게
    # 전달하면서도 네 사용처가 모두 있는지는 validator가 그대로 강제한다.
    usage = {
        "market_baseline": _usage("market_baseline", market),
        "ai_residual": _usage("ai_residual", residual),
        "decision_gate": _usage("decision_gate", gate),
        "explainer": _usage("explainer", explainer),
    }
    reasons = {
        consumer: reason
        for consumer, reason in {
            "ai_residual": residual_reason,
            "decision_gate": gate_reason,
            "explainer": explainer_reason,
        }.items()
        if reason
    }
    result = {"usage": usage}
    if reasons:
        result["reason_codes"] = reasons
    return result


def _starter_info(game: dict) -> dict:
    info = game.get("선발")
    return info if isinstance(info, dict) else {}


def _has_rows(value: object) -> bool:
    if isinstance(value, dict):
        return any(_has_rows(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return bool(value)


def _evidence(
    game: dict, option: dict | None, *, market_observed_at: str,
) -> tuple[list[dict], list[dict]]:
    """자료마다 사용처와 미반영 이유를 빠짐없이 기록한다."""
    info = _starter_info(game)
    internal_applied = bool(option and option.get("확률근거") == INTERNAL_OPERATING_VERSION)
    evidence: list[dict] = []
    sources: list[dict] = []

    if option and _number(option.get("시장확률")) is not None:
        evidence.append({
            "id": "market_price",
            "available": True,
            "value": _number(option.get("시장확률")),
            "source_ids": ["proto_market"],
            **_all_usage(
                market="used", residual="used", gate="used", explainer="used"),
        })
    else:
        evidence.append({
            "id": "market_price",
            "available": False,
            **_all_usage(
                market="missing", residual="missing", gate="missing", explainer="missing"),
        })

    has_form = bool(game.get("form_home") or game.get("form_away") or game.get("h2h"))
    evidence.append({
        "id": "team_performance",
        "available": has_form,
        **_all_usage(
            market="ignored", residual="shadow" if has_form else "missing",
            gate="ignored", explainer="used" if has_form else "missing",
            residual_reason="model_not_promoted" if has_form else None,
            gate_reason="market_anchor_policy",
        ),
    })

    has_lineup = _has_rows(info.get("lineups")) or bool(info.get("home") or info.get("away"))
    evidence.append({
        "id": "lineup",
        "available": has_lineup,
        "observed_at": info.get("updated_at"),
        **_all_usage(
            market="ignored",
            residual="used" if has_lineup and internal_applied else "context_only" if has_lineup else "missing",
            gate="used" if has_lineup and internal_applied else "ignored",
            explainer="used" if has_lineup and internal_applied else "context_only" if has_lineup else "missing",
            residual_reason=None if internal_applied else "future_validation_pending" if has_lineup else None,
            gate_reason=None if internal_applied else "not_in_operating_formula",
            explainer_reason=None if internal_applied else "context_not_probability" if has_lineup else None,
        ),
    })

    has_availability = _has_rows(info.get("unavailable"))
    evidence.append({
        "id": "availability",
        "available": has_availability,
        "observed_at": info.get("updated_at"),
        **_all_usage(
            market="ignored",
            residual="used" if has_availability and internal_applied else "context_only" if has_availability else "missing",
            gate="used" if has_availability and internal_applied else "ignored",
            explainer="used" if has_availability and internal_applied else "context_only" if has_availability else "missing",
            residual_reason=None if internal_applied else "future_validation_pending" if has_availability else None,
            gate_reason=None if internal_applied else "not_in_operating_formula",
            explainer_reason=None if internal_applied else "context_not_probability" if has_availability else None,
        ),
    })

    has_cross_market = _has_rows(game.get("시장문맥"))
    evidence.append({
        "id": "cross_market",
        "available": has_cross_market,
        **_all_usage(
            market="ignored", residual="shadow" if has_cross_market else "missing",
            gate="ignored", explainer="used" if has_cross_market else "missing",
            residual_reason="diagnostic_not_promoted" if has_cross_market else None,
            gate_reason="diagnostic_only",
        ),
    })

    if info.get("source"):
        sources.append({
            "id": "player_source", "name": info.get("source"),
            "url": info.get("source_url"), "collected_at": info.get("updated_at"),
        })
        for item in evidence:
            if item["id"] in {"lineup", "availability"}:
                item["source_ids"] = ["player_source"]
    sources.insert(0, {
        "id": "proto_market", "name": "프로토 공식 배당",
        "url": None, "collected_at": market_observed_at,
    })
    return evidence, sources


def _input_revision_hash(game: dict, evidence: list[dict]) -> str:
    payload = {
        "event_id": event_id(game),
        "options": sorted((
            option.get("selection_id"), option.get("offer_id"),
            _number(option.get("배당")), _number(option.get("시장확률")),
            _number(option.get("모델확률")), _number(option.get("잔차모델확률")),
            _number(option.get("내부확률")), _number(option.get("선수보정")),
            _number(option.get("최종확률")), option.get("잔차모델해시"),
        ) for option in game.get("options", [])),
        "evidence": sorted((
            row.get("id"), row.get("available"), row.get("observed_at"),
        ) for row in evidence),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _stage(status: str, affects_probability: bool = False) -> dict:
    return {"status": status, "affects_probability": affects_probability}


def build_decision_snapshot(
    game: dict,
    *,
    as_of: str,
    explanation_kind: str = "deterministic",
    built_at: str | None = None,
    pre_registered: bool = False,
    reconstructed_at: str | None = None,
    probability_artifact: Mapping | None = None,
) -> dict:
    """한 경기의 수치·근거·AI 역할을 단일 화면 계약으로 고정한다."""
    annotate_options(game, probability_artifact=probability_artifact)
    # 호출자가 예전 모델 추천을 넣어 둔 경우에도 그것을 신뢰하지 않는다. 운영 선택은
    # 이 함수 안에서 다시 계산해야 구조 AI/LLM이 우회 경로로 최종 판정을 바꿀 수 없다.
    selected = choose_market_reference(game.get("options", []))
    game["추천"] = selected
    market = _number(selected.get("시장확률")) if selected else None
    shadow = _number(selected.get("모델확률")) if selected else None
    residual_shadow = _number(selected.get("잔차모델확률")) if selected else None
    final = _number(selected.get("최종확률")) if selected else None
    applied = bool(selected and selected.get("AI반영") is True)
    pipeline = game.get("probability_pipeline") or {}
    action = "market_reference" if selected and market is not None else "withhold"
    reversed_pick = selected and selected.get("추천우선순위") == "reversal"
    built_at = built_at or as_of
    evidence, sources = _evidence(game, selected, market_observed_at=as_of)
    input_hash = _input_revision_hash(game, evidence)
    explanation_status = "wording_only" if explanation_kind == "llm_assisted" else "template"

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "decision_id": _stable_id(
            "dec", event_id(game), selected.get("selection_id") if selected else "withhold",
            selected.get("offer_id") if selected else None,
            as_of, input_hash, OPERATING_MODEL_VERSION,
        ),
        "event_id": event_id(game),
        "as_of": as_of,
        "action": action,
        "selection_id": selected.get("selection_id") if selected else None,
        "offer_id": selected.get("offer_id") if selected else None,
        "input_revision_hash": input_hash,
        "gate_codes": (
            ["qualified_market_reversal"] if reversed_pick
            else [] if action == "market_reference"
            else ["no_eligible_market_reference"]
        ),
        "probability": {
            "market": market,
            "ai_candidate": shadow,
            "ai_delta_candidate": (
                None if market is None or shadow is None else round(shadow - market, 4)
            ),
            "residual_candidate": residual_shadow,
            "residual_interval": (
                selected.get("잔차모델구간") if selected else None
            ),
            "residual_delta_candidate": (
                None if market is None or residual_shadow is None
                else round(residual_shadow - market, 4)
            ),
            "ai_delta_applied": (
                0.0 if market is None or final is None
                else round(final - market, 4)
            ),
            "final": final,
            "basis": (
                selected.get("확률근거") if selected
                else "unavailable"
            ),
        },
        "model": {
            "operating_version": pipeline.get("operating_version") or OPERATING_MODEL_VERSION,
            "residual_version": SHADOW_MODEL_VERSION,
            "probability_pipeline_version": "market-logit-residual-v1",
            "status": (
                "operational" if applied
                else "shadow" if shadow is not None or residual_shadow is not None
                else "unavailable"
            ),
            "validated_edge": bool(applied and pipeline.get("artifact_hash") in PROMOTED_ARTIFACT_HASHES),
            "policy_authorized": bool(applied and pipeline.get("policy_authorized")),
            "promotion_gate": "passed" if applied else "not_passed",
            "artifact_hash": pipeline.get("artifact_hash"),
        },
        # label/summary는 전 경기 공통 카탈로그다. 스냅샷에는 경기마다 달라지는 상태만
        # 둬서 웹 데이터가 같은 설명을 수백 번 중복하지 않게 한다.
        "stages": {
            "market": _stage("used" if market is not None else "missing", market is not None),
            "structured_ai": _stage(
                (
                    "used" if applied
                    else "selection_gate" if reversed_pick
                    else "shadow" if shadow is not None
                    else "missing"
                ),
                applied,
            ),
            "availability_ai": _stage(
                "used" if applied and selected and selected.get("확률근거") == INTERNAL_OPERATING_VERSION
                else "context_only" if any(row["id"] in {"lineup", "availability"} and row["available"] for row in evidence)
                else "missing",
                bool(applied and selected and selected.get("확률근거") == INTERNAL_OPERATING_VERSION)),
            "language_ai": _stage(explanation_status),
        },
        "evidence": evidence,
        "sources": sources,
        "explanation": {
            "kind": explanation_kind,
            "affects_probability": False,
            "evidence_ids": [row["id"] for row in evidence if row.get("available")],
        },
        "audit": {
            "feature_cutoff_at": as_of,
            "built_at": built_at,
            "pre_registered": bool(pre_registered),
            **({"reconstructed_at": reconstructed_at} if reconstructed_at else {}),
        },
    }
    validate_decision_snapshot(snapshot)
    return snapshot


def validate_decision_snapshot(snapshot: dict) -> None:
    """중복·침묵 미반영·설명 참조 오류를 생성 단계에서 차단한다."""
    raw_stages = snapshot.get("stages") or {}
    if isinstance(raw_stages, dict):
        stage_ids = list(raw_stages)
        stage_rows = list(raw_stages.values())
    else:  # v1 문서도 검증·이관할 수 있게 유지한다.
        stage_ids = [row.get("id") for row in raw_stages]
        stage_rows = list(raw_stages)
    evidence_ids = [row.get("id") for row in snapshot.get("evidence", [])]
    if len(stage_ids) != len(set(stage_ids)) or set(stage_ids) != set(STAGE_IDS):
        raise ValueError("duplicate or incomplete AI stage id")
    if any(row.get("status") not in (
        USAGE_STATUSES | {"wording_only", "template", "selection_gate"}
    )
           for row in stage_rows):
        raise ValueError("unknown AI stage status")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("duplicate evidence id")
    probability = snapshot.get("probability") or {}
    model = snapshot.get("model") or {}
    can_apply = (
        model.get("status") == "operational"
        and model.get("promotion_gate") == "passed"
        and bool(model.get("operating_version"))
        and (
            (model.get("validated_edge") is True
             and model.get("artifact_hash") in PROMOTED_ARTIFACT_HASHES)
            or (model.get("policy_authorized") is True
                and model.get("operating_version") in POLICY_AUTHORIZED_MODELS)
        )
    )
    if not can_apply:
        if probability.get("final") != probability.get("market"):
            raise ValueError("unvalidated AI changed final probability")
        if probability.get("ai_delta_applied") != 0.0:
            raise ValueError("unvalidated AI applied a probability delta")
    known = set(evidence_ids)
    refs = set((snapshot.get("explanation") or {}).get("evidence_ids") or [])
    if not refs.issubset(known):
        raise ValueError("explanation references unknown evidence")
    audit = snapshot.get("audit") or {}
    cutoff = _timestamp(audit.get("feature_cutoff_at"))
    built = _timestamp(audit.get("built_at"))
    if cutoff is None or built is None or built < cutoff:
        raise ValueError("invalid decision cutoff or build timestamp")
    for evidence in snapshot.get("evidence", []):
        observed = _timestamp(evidence.get("observed_at"))
        if evidence.get("observed_at") and (observed is None or observed > cutoff):
            raise ValueError(f"evidence observed after cutoff: {evidence.get('id')}")
        usage = evidence.get("usage") or {}
        if isinstance(usage, dict):
            consumers = list(usage)
            statuses = list(usage.values())
        else:  # v1 배열 형식과의 하위 호환
            consumers = [row.get("consumer") for row in usage]
            statuses = [row.get("status") for row in usage]
        if set(consumers) != set(USAGE_CONSUMERS) or len(consumers) != len(set(consumers)):
            raise ValueError(f"incomplete evidence usage ledger: {evidence.get('id')}")
        for status in statuses:
            if status not in USAGE_STATUSES:
                raise ValueError(f"unknown evidence status: {status}")


def usage_counts(snapshot: dict) -> dict[str, int]:
    """UI와 테스트가 같은 분류를 쓰도록 최종 사용 상태를 요약한다."""
    counts = {"used": 0, "shadow": 0, "context_only": 0, "ignored": 0, "missing": 0}
    for evidence in snapshot.get("evidence", []):
        raw_usage = evidence.get("usage") or {}
        rows: Iterable[dict] = (
            [{"consumer": consumer, "status": status}
             for consumer, status in raw_usage.items()]
            if isinstance(raw_usage, dict) else raw_usage
        )
        gate = next((row for row in rows if row.get("consumer") == "decision_gate"), None)
        residual = next((row for row in rows if row.get("consumer") == "ai_residual"), None)
        explainer = next((row for row in rows if row.get("consumer") == "explainer"), None)
        if evidence.get("available") is False:
            key = "missing"
        elif gate and gate.get("status") == "used":
            key = "used"
        elif residual and residual.get("status") == "shadow":
            key = "shadow"
        elif explainer and explainer.get("status") == "context_only":
            key = "context_only"
        elif any(row and row.get("status") == "ignored" for row in (gate, residual)):
            key = "ignored"
        else:
            key = "missing"
        counts[key] += 1
    return counts
