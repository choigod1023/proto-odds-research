"""자연선택 추천기의 운영 시점 점수 계산.

학습기는 :mod:`evolutionary_selector`에 있고 이 모듈은 저장된 유전자와 현재 후보만
받아 같은 점수를 재현한다. 결과를 아는 열이나 과거 적중률은 입력으로 받지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np

PROFILE_CONFIGS = {
    "safe": {
        "label": "안정형",
        "odds_min": 1.18,
        "odds_max": 1.45,
        "target_odds": 1.30,
        "minimum_average_odds": 1.18,
        "minimum_coverage": 0.70,
    },
    "balanced": {
        "label": "균형형",
        "odds_min": 1.40,
        "odds_max": 1.85,
        "target_odds": 1.58,
        "minimum_average_odds": 1.40,
        "minimum_coverage": 0.65,
    },
    "challenge": {
        "label": "도전형",
        "odds_min": 1.65,
        "odds_max": 2.20,
        "target_odds": 1.88,
        "minimum_average_odds": 1.65,
        "minimum_coverage": 0.55,
    },
}

GENE_NAMES = (
    "confidence",
    "odds",
    "overround",
    "market_gap",
    "price_distance",
    "three_way",
    "handicap",
    "totals",
    "first_half",
    "baseball",
    "basketball",
    "volleyball",
    "soccer",
)


def probability_of(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 < number < 1.0 else None


def _market_kind(candidate: dict) -> tuple[float, float, float]:
    market = str(candidate.get("market") or "")
    return (
        float("핸디" in market),
        float("언더오버" in market),
        float(market.startswith("전반")),
    )


def _sport_kind(candidate: dict) -> tuple[float, float, float, float]:
    sport = str(candidate.get("sport") or "").lower()
    league = str(candidate.get("league") or "").lower()
    text = f"{sport} {league}"
    baseball = float(any(token in text for token in ("baseball", "야구", "kbo", "mlb", "npb")))
    basketball = float(any(token in text for token in ("basketball", "농구", "nba", "kbl", "wnba")))
    volleyball = float(any(token in text for token in ("volleyball", "배구", "v리그")))
    soccer = float(not (baseball or basketball or volleyball))
    return baseball, basketball, volleyball, soccer


def feature_vector(candidate: dict, profile: dict) -> tuple[float, ...] | None:
    probability = probability_of(candidate.get("market_prob"))
    try:
        odds = float(candidate.get("odds"))
        overround = float(candidate.get("overround"))
        gap = float(candidate.get("market_gap") or 0.0)
        n_way = int(candidate.get("n_way") or 2)
    except (TypeError, ValueError):
        return None
    if (probability is None or not math.isfinite(odds) or not math.isfinite(overround)
            or not profile["odds_min"] <= odds < profile["odds_max"]):
        return None
    logit = math.log(probability / (1.0 - probability))
    price_distance = abs(math.log(odds / float(profile["target_odds"])))
    handicap, totals, first_half = _market_kind(candidate)
    baseball, basketball, volleyball, soccer = _sport_kind(candidate)
    return (
        logit,
        math.log(odds),
        max(0.0, overround - 1.0),
        max(0.0, gap),
        price_distance,
        float(n_way == 3),
        handicap,
        totals,
        first_half,
        baseball,
        basketball,
        volleyball,
        soccer,
    )


def score_candidate(candidate: dict, genome: dict, profile: dict) -> float | None:
    features = feature_vector(candidate, profile)
    if features is None:
        return None
    return float(sum(float(genome.get(name, 0.0)) * value
                     for name, value in zip(GENE_NAMES, features)))


def rank_candidates(candidates: Iterable[dict], rule: dict, limit: int = 3) -> list[dict]:
    profile_name = str(rule.get("profile") or "balanced")
    profile = PROFILE_CONFIGS.get(profile_name)
    genome = rule.get("genome")
    genomes = [row for row in (rule.get("genomes") or []) if isinstance(row, dict)]
    if not profile or (not isinstance(genome, dict) and not genomes):
        return []
    eligible = []
    for candidate in candidates:
        features = feature_vector(candidate, profile)
        if features is None:
            continue
        eligible.append((candidate, features))
    if not eligible:
        return []
    if genomes:
        raw = np.asarray([
            [sum(float(member.get(name, 0.0)) * value
                 for name, value in zip(GENE_NAMES, features)) for member in genomes]
            for _, features in eligible
        ], dtype=float)
        mean = raw.mean(axis=0)
        scale = raw.std(axis=0)
        scale[scale < 1e-9] = 1.0
        scores = ((raw - mean) / scale).mean(axis=1)
    else:
        scores = np.asarray([
            sum(float(genome.get(name, 0.0)) * value
                for name, value in zip(GENE_NAMES, features))
            for _, features in eligible
        ])
    ranked = [{**candidate, "evolution_score": round(float(score), 6)}
              for (candidate, _), score in zip(eligible, scores)]
    ranked.sort(key=lambda row: (
        -float(row["evolution_score"]),
        -float(row.get("market_prob") or 0.0),
        float(row.get("overround") or 99.0),
        str(row.get("kickoff_at") or row.get("date") or ""),
    ))
    return ranked[:max(0, int(limit))]


def canonical_hash(payload: dict) -> str:
    clean = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_artifact(path: Path) -> dict | None:
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expected = artifact.get("artifact_sha256")
    if not isinstance(expected, str) or expected != canonical_hash(artifact):
        return None
    return artifact


def live_snapshot(candidates: list[dict], artifact: dict | None) -> dict:
    if not artifact:
        return {"schema": "evolutionary-selection-v1", "status": "unavailable",
                "default_profile": "balanced", "profiles": {}}
    profiles = {}
    for name, rule in (artifact.get("profiles") or {}).items():
        historical_status = rule.get("historical_status", "unavailable")
        ranked = (rank_candidates(candidates, rule, limit=3)
                  if historical_status != "rejected_in_historical_audit" else [])
        profiles[name] = {
            "label": PROFILE_CONFIGS.get(name, {}).get("label", name),
            "rule": {
                "profile": name,
                "genome": rule.get("genome"),
                "genomes": rule.get("genomes"),
                "constraints": rule.get("constraints"),
            },
            "selected": ranked[0] if ranked else None,
            "alternatives": ranked[1:],
            "historical_validation": rule.get("historical_validation"),
            "historical_test": rule.get("historical_test"),
            "historical_status": historical_status,
            "historical_point_gate_passed": rule.get("historical_point_gate_passed", False),
            "historical_ci_gate_passed": rule.get("historical_ci_gate_passed", False),
        }
    promotion = artifact.get("promotion") or {}
    return {
        "schema": artifact.get("schema", "evolutionary-selection-v1"),
        "status": promotion.get("status", "shadow_only"),
        "default_profile": artifact.get("default_profile", "balanced"),
        "artifact_sha256": artifact.get("artifact_sha256"),
        "trained_through": artifact.get("trained_through"),
        "profiles": profiles,
        "method": artifact.get("method"),
        "promotion": promotion,
    }
