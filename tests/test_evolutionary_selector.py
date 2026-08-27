from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evolutionary_policy import (GENE_NAMES, PROFILE_CONFIGS, canonical_hash,
                                  load_artifact, rank_candidates)
from evolutionary_selector import Dataset, evaluate, evolve


def candidate(*, probability: float, odds: float, market: str = "승패") -> dict:
    return {
        "sport": "축구",
        "league": "테스트",
        "market": market,
        "n_way": 2,
        "odds": odds,
        "overround": 1.12,
        "market_prob": probability,
        "market_gap": 0.12,
        "kickoff_at": "2026-08-27T19:00:00+09:00",
    }


def test_runtime_ranking_uses_frozen_genome_and_profile():
    rule = {
        "profile": "balanced",
        "genome": {name: 0.0 for name in GENE_NAMES},
    }
    rule["genome"]["confidence"] = 1.0
    ranked = rank_candidates([
        candidate(probability=.62, odds=1.55),
        candidate(probability=.70, odds=1.45),
        candidate(probability=.80, odds=1.15),  # 균형형 배당 범위 밖
    ], rule)
    assert len(ranked) == 2
    assert ranked[0]["market_prob"] == .70


def test_artifact_hash_fails_closed_when_rule_is_tampered(tmp_path):
    payload = {
        "schema": "evolutionary-selection-v1",
        "default_profile": "balanced",
        "profiles": {},
        "promotion": {"status": "shadow_only"},
    }
    payload["artifact_sha256"] = canonical_hash(payload)
    path = tmp_path / "rule.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_artifact(path) is not None
    payload["default_profile"] = "challenge"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_artifact(path) is None


def test_natural_selection_learns_repeatable_signal_without_dropping_days():
    # 매일 첫 후보는 시장확률만 높고 틀리며, 두 번째 후보는 totals 표시가 있고 맞는다.
    days = 48
    features = []
    hit = []
    odds = []
    dates = []
    sports = []
    groups = []
    for day in range(days):
        start = len(features)
        wrong = np.zeros(len(GENE_NAMES))
        wrong[GENE_NAMES.index("confidence")] = 1.1
        right = np.zeros(len(GENE_NAMES))
        right[GENE_NAMES.index("confidence")] = .7
        right[GENE_NAMES.index("totals")] = 1.0
        features.extend([wrong, right])
        hit.extend([0, 1])
        odds.extend([1.52, 1.52])
        dates.extend([f"2024-02-{day % 28 + 1:02d}-{day:02d}"] * 2)
        sports.extend(["축구", "축구"])
        groups.append((start, start + 2))
    dataset = Dataset(
        np.asarray(features), np.asarray(hit, dtype=float), np.asarray(odds, dtype=float),
        np.asarray(dates, dtype=object), np.asarray(sports, dtype=object),
        tuple(groups), days,
    )
    survivors, lineage = evolve(
        dataset, "balanced", seed=19, population_size=20, generations=8)
    best = max((evaluate(dataset, genome) for genome in survivors),
               key=lambda row: row["accuracy"])
    assert best["n"] == days
    assert best["coverage"] == 1.0
    assert best["accuracy"] >= .95
    assert lineage["generations"] == 8
    assert PROFILE_CONFIGS["balanced"]["minimum_average_odds"] <= best["average_odds"]
