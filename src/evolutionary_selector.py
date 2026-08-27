"""여러 마켓 중 하루 한 픽을 고르는 자연선택형 추천기.

2023~2024에서 전략 유전자를 진화시키고, 2025에서 유형별 생존 전략을 고른다.
2026은 역사 감사에만 사용하며 프로젝트에서 이미 반복 열람한 기간이므로 어떤 결과도
운영 승격 근거로 쓰지 않는다. 운영은 새 사전등록 원장이 쌓인 뒤에만 가능하다.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from accuracy_formula_lab import DEFAULT_GAMES, load_unique_market_rows
from devig import MARKET_PROBABILITY_METHOD, market_probabilities
from evolutionary_policy import (GENE_NAMES, PROFILE_CONFIGS, canonical_hash,
                                   feature_vector)
from recommendation_policy import (automatic_selection_exclusion_reason,
                                   recommendation_exclusion_reason)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "findings" / "evolutionary_selector.json"
TRAIN_YEARS = (2023, 2024)
VALIDATION_YEAR = 2025
TEST_YEAR = 2026
SCHEMA = "evolutionary-selection-v1"
DEFAULT_SEED = 20260827

GENE_BOUNDS = np.asarray([
    (0.20, 4.00),   # confidence
    (-2.00, 2.00),  # odds
    (-8.00, 0.00),  # overround
    (-2.00, 3.00),  # market gap
    (-6.00, 0.00),  # target-price distance
    (-1.50, 1.50),  # three way
    (-1.50, 1.50),  # handicap
    (-1.50, 1.50),  # totals
    (-2.00, 0.50),  # first half
    (-0.35, 0.35),  # baseball
    (-0.35, 0.35),  # basketball
    (-0.35, 0.35),  # volleyball
    (-0.35, 0.35),  # soccer
], dtype=float)


@dataclass(frozen=True)
class Dataset:
    features: np.ndarray
    hit: np.ndarray
    odds: np.ndarray
    dates: np.ndarray
    sports: np.ndarray
    groups: tuple[tuple[int, int], ...]
    universe_days: int


def wilson(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    denominator = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    spread = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)
    return (centre - spread) / denominator, (centre + spread) / denominator


def historical_candidates(records: list[dict]) -> list[dict]:
    candidates = []
    for record in records:
        probability = market_probabilities(list(record["odds"]))
        selection = int(np.argmax(probability))
        favorite = float(probability[selection])
        odds = float(record["odds"][selection])
        kickoff, sport, league, home, away, market, booking, label, n_way = record["key"]
        if recommendation_exclusion_reason(market):
            continue
        if automatic_selection_exclusion_reason(market, odds, favorite, max(probability)):
            continue
        sorted_probability = sorted((float(value) for value in probability), reverse=True)
        candidates.append({
            "date": kickoff.date().isoformat(),
            "year": int(record["year"]),
            "event_key": "|".join(map(str, (kickoff, sport, league, home, away))),
            "sport": sport,
            "league": league,
            "market": market,
            "market_label": label,
            "booking": booking,
            "n_way": int(n_way),
            "odds": odds,
            "overround": float(sum(1.0 / value for value in record["odds"])),
            "market_prob": favorite,
            "market_gap": (sorted_probability[0] - sorted_probability[1]
                           if len(sorted_probability) > 1 else favorite),
            "won": int(selection == record["winner"]),
        })
    return candidates


def make_dataset(candidates: list[dict], profile_name: str,
                 years: tuple[int, ...]) -> Dataset:
    profile = PROFILE_CONFIGS[profile_name]
    universe = {row["date"] for row in candidates if int(row["year"]) in years}
    rows = []
    for row in candidates:
        if int(row["year"]) not in years:
            continue
        features = feature_vector(row, profile)
        if features is not None:
            rows.append((row["date"], row["sport"], row["won"], row["odds"], features))
    rows.sort(key=lambda row: row[0])
    features = np.asarray([row[4] for row in rows], dtype=float)
    hit = np.asarray([row[2] for row in rows], dtype=float)
    odds = np.asarray([row[3] for row in rows], dtype=float)
    dates = np.asarray([row[0] for row in rows], dtype=object)
    sports = np.asarray([row[1] for row in rows], dtype=object)
    groups = []
    start = 0
    while start < len(rows):
        end = start + 1
        while end < len(rows) and rows[end][0] == rows[start][0]:
            end += 1
        groups.append((start, end))
        start = end
    return Dataset(features, hit, odds, dates, sports, tuple(groups), len(universe))


def selected_indices(dataset: Dataset, genome: np.ndarray) -> np.ndarray:
    if not dataset.groups:
        return np.asarray([], dtype=int)
    genes = np.asarray(genome, dtype=float)
    if genes.ndim == 1:
        scores = dataset.features @ genes
        return np.asarray([start + int(np.argmax(scores[start:end]))
                           for start, end in dataset.groups], dtype=int)
    # 독립 집단마다 점수 척도가 다르므로 날짜 안에서 z-score로 맞춘 뒤 합의한다.
    raw = dataset.features @ genes.T
    selected = []
    for start, end in dataset.groups:
        block = raw[start:end]
        mean = block.mean(axis=0)
        scale = block.std(axis=0)
        scale[scale < 1e-9] = 1.0
        consensus = ((block - mean) / scale).mean(axis=1)
        selected.append(start + int(np.argmax(consensus)))
    return np.asarray(selected, dtype=int)


def evaluate(dataset: Dataset, genome: np.ndarray, *, include_picks: bool = False) -> dict:
    indices = selected_indices(dataset, genome)
    if not len(indices):
        return {"n": 0, "coverage": 0.0, "accuracy": None,
                "accuracy_wilson95": [None, None], "average_odds": None,
                "roi": None, "max_drawdown": None, "sport_max_share": None}
    hit = dataset.hit[indices]
    odds = dataset.odds[indices]
    profit = np.where(hit > 0.5, odds - 1.0, -1.0)
    cumulative = np.cumsum(profit)
    peaks = np.maximum.accumulate(np.r_[0.0, cumulative])[:-1]
    drawdown = peaks - cumulative
    wins = int(hit.sum())
    lo, hi = wilson(wins, len(indices))
    _, counts = np.unique(dataset.sports[indices], return_counts=True)
    result = {
        "n": int(len(indices)),
        "wins": wins,
        "coverage": float(len(indices) / max(1, dataset.universe_days)),
        "accuracy": float(hit.mean()),
        "accuracy_wilson95": [lo, hi],
        "average_odds": float(odds.mean()),
        "roi": float(profit.mean()),
        "max_drawdown": float(drawdown.max(initial=0.0)),
        "sport_max_share": float(counts.max() / len(indices)) if len(counts) else None,
    }
    if include_picks:
        result["picks"] = [
            {"date": str(dataset.dates[index]), "hit": int(dataset.hit[index]),
             "odds": float(dataset.odds[index])}
            for index in indices
        ]
    return result


def feasible(metrics: dict, profile: dict) -> bool:
    return bool(metrics.get("n") and metrics.get("average_odds") is not None
                and metrics["average_odds"] >= profile["minimum_average_odds"]
                and metrics["coverage"] >= profile["minimum_coverage"])


def objective(metrics: dict, profile_name: str) -> tuple[float, ...]:
    profile = PROFILE_CONFIGS[profile_name]
    if not metrics.get("n"):
        return (-1.0, -1.0, -1.0, -1e9, -1.0)
    odds_progress = min(1.0, max(0.0, (
        metrics["average_odds"] - profile["minimum_average_odds"]
    ) / max(0.01, profile["odds_max"] - profile["minimum_average_odds"])))
    return (
        1.0 if feasible(metrics, profile) else 0.0,
        float(metrics["accuracy_wilson95"][0]),
        odds_progress,
        float(metrics["roi"]),
        -float(metrics["max_drawdown"]),
        -float(metrics.get("sport_max_share") or 1.0),
    )


def dominates(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return all(a >= b for a, b in zip(left, right)) and any(
        a > b for a, b in zip(left, right))


def pareto_indices(objectives: list[tuple[float, ...]]) -> list[int]:
    return [index for index, score in enumerate(objectives)
            if not any(other != index and dominates(objectives[other], score)
                       for other in range(len(objectives)))]


def scalar_key(metrics: dict, profile_name: str) -> tuple[float, ...]:
    profile = PROFILE_CONFIGS[profile_name]
    valid = feasible(metrics, profile)
    if profile_name == "safe":
        return (float(valid), metrics.get("accuracy_wilson95", [0])[0] or 0.0,
                metrics.get("accuracy") or 0.0, metrics.get("average_odds") or 0.0,
                metrics.get("roi") or -9.0,
                -(metrics.get("sport_max_share") or 1.0))
    if profile_name == "challenge":
        return (float(valid), metrics.get("accuracy_wilson95", [0])[0] or 0.0,
                metrics.get("average_odds") or 0.0, metrics.get("roi") or -9.0,
                metrics.get("accuracy") or 0.0,
                -(metrics.get("sport_max_share") or 1.0))
    return (float(valid), metrics.get("accuracy_wilson95", [0])[0] or 0.0,
            metrics.get("roi") or -9.0, metrics.get("average_odds") or 0.0,
            metrics.get("accuracy") or 0.0,
            -(metrics.get("sport_max_share") or 1.0))


def random_genome(rng: np.random.Generator) -> np.ndarray:
    return rng.uniform(GENE_BOUNDS[:, 0], GENE_BOUNDS[:, 1])


def mutate(genome: np.ndarray, rng: np.random.Generator, rate: float = 0.25) -> np.ndarray:
    child = np.asarray(genome, dtype=float).copy()
    span = GENE_BOUNDS[:, 1] - GENE_BOUNDS[:, 0]
    mask = rng.random(len(child)) < rate
    child[mask] += rng.normal(0.0, span[mask] * 0.12)
    return np.clip(child, GENE_BOUNDS[:, 0], GENE_BOUNDS[:, 1])


def crossover(left: np.ndarray, right: np.ndarray,
              rng: np.random.Generator) -> np.ndarray:
    mix = rng.uniform(0.15, 0.85, len(left))
    return mix * left + (1.0 - mix) * right


def evolve(dataset: Dataset, profile_name: str, *, seed: int = DEFAULT_SEED,
           population_size: int = 56, generations: int = 24) -> tuple[list[np.ndarray], dict]:
    rng = np.random.default_rng(seed)
    baseline = np.zeros(len(GENE_NAMES), dtype=float)
    baseline[GENE_NAMES.index("confidence")] = 1.0
    population = [baseline] + [random_genome(rng) for _ in range(population_size - 1)]
    lineage = []
    for generation in range(generations):
        metrics = [evaluate(dataset, genome) for genome in population]
        objectives = [objective(row, profile_name) for row in metrics]
        front = pareto_indices(objectives)
        ordered = sorted(range(len(population)),
                         key=lambda index: scalar_key(metrics[index], profile_name), reverse=True)
        survivor_indices = list(dict.fromkeys(front + ordered))[:max(6, population_size // 3)]
        survivors = [population[index] for index in survivor_indices]
        best_index = ordered[0]
        lineage.append({"generation": generation + 1,
                        "pareto_survivors": len(front),
                        "best": {key: value for key, value in metrics[best_index].items()
                                 if key != "picks"}})
        next_population = [genome.copy() for genome in survivors]
        while len(next_population) < population_size:
            left, right = rng.choice(len(survivors), size=2, replace=True)
            child = crossover(survivors[int(left)], survivors[int(right)], rng)
            next_population.append(mutate(child, rng))
        population = next_population
    metrics = [evaluate(dataset, genome) for genome in population]
    objectives = [objective(row, profile_name) for row in metrics]
    front = pareto_indices(objectives)
    survivors = [population[index] for index in front]
    return survivors, {"population": population_size, "generations": generations,
                       "seed": seed, "lineage": lineage}


def paired_bootstrap(left: dict, right: dict, *, samples: int = 5000,
                     seed: int = DEFAULT_SEED) -> dict:
    a = {row["date"]: row for row in left.get("picks", [])}
    b = {row["date"]: row for row in right.get("picks", [])}
    dates = sorted(set(a) & set(b))
    if not dates:
        return {"n_dates": 0, "accuracy_delta_pp": None, "ci95_pp": [None, None]}
    delta = np.asarray([a[date]["hit"] - b[date]["hit"] for date in dates], dtype=float)
    rng = np.random.default_rng(seed)
    draws = delta[rng.integers(0, len(delta), size=(samples, len(delta)))].mean(axis=1)
    return {"n_dates": len(dates), "accuracy_delta_pp": float(delta.mean() * 100.0),
            "ci95_pp": [float(value * 100.0) for value in np.quantile(draws, [.025, .975])],
            "probability_evolved_better": float(np.mean(draws > 0.0))}


def _summary(metrics: dict) -> dict:
    return {key: value for key, value in metrics.items() if key != "picks"}


def build_artifact(candidates: list[dict], *, population_size: int = 56,
                   generations: int = 24, seed: int = DEFAULT_SEED,
                   replicates: int = 3) -> dict:
    profiles = {}
    for offset, profile_name in enumerate(PROFILE_CONFIGS):
        train = make_dataset(candidates, profile_name, TRAIN_YEARS)
        validation = make_dataset(candidates, profile_name, (VALIDATION_YEAR,))
        test = make_dataset(candidates, profile_name, (TEST_YEAR,))
        champions = []
        replicate_runs = []
        for replicate in range(max(1, int(replicates))):
            replicate_seed = seed + offset * 100 + replicate
            survivors, evolution = evolve(
                train, profile_name, seed=replicate_seed,
                population_size=population_size, generations=generations)
            validation_metrics = [evaluate(validation, genome) for genome in survivors]
            chosen_index = max(
                range(len(survivors)),
                key=lambda index: scalar_key(validation_metrics[index], profile_name))
            replicate_champion = survivors[chosen_index]
            champions.append(replicate_champion)
            replicate_runs.append({
                "seed": replicate_seed,
                "validation": _summary(evaluate(validation, replicate_champion)),
                "historical_test": _summary(evaluate(test, replicate_champion)),
                "lineage": evolution["lineage"],
            })
        champion = np.asarray(champions, dtype=float)
        validation_result = evaluate(validation, champion, include_picks=True)
        test_result = evaluate(test, champion, include_picks=True)
        baseline = np.zeros(len(GENE_NAMES), dtype=float)
        baseline[GENE_NAMES.index("confidence")] = 1.0
        baseline_validation = evaluate(validation, baseline, include_picks=True)
        baseline_test = evaluate(test, baseline, include_picks=True)
        comparison = paired_bootstrap(test_result, baseline_test, seed=seed + 100 + offset)
        replicate_delta = [
            paired_bootstrap(
                evaluate(test, genome, include_picks=True), baseline_test,
                seed=seed + 1000 + offset * 10 + index)["accuracy_delta_pp"]
            for index, genome in enumerate(champions)
        ]
        validation_improvement = bool(
            validation_result["accuracy"] > baseline_validation["accuracy"])
        point_improvement = bool(validation_improvement
                                 and (comparison.get("accuracy_delta_pp") or 0.0) > 0.0)
        ci_improvement = bool(comparison.get("ci95_pp", [None])[0] is not None
                              and comparison["ci95_pp"][0] > 0.0)
        historical_status = ("promising_but_unproven" if point_improvement
                             else "rejected_in_historical_audit")
        profiles[profile_name] = {
            "profile": profile_name,
            "constraints": PROFILE_CONFIGS[profile_name],
            "genome": {name: round(float(value), 8)
                       for name, value in zip(GENE_NAMES, champion.mean(axis=0))},
            "genomes": [
                {name: round(float(value), 8) for name, value in zip(GENE_NAMES, genome)}
                for genome in champion
            ],
            "evolution": {
                "population": population_size,
                "generations": generations,
                "ensemble": "mean score of independently evolved genomes",
                "replicates": replicate_runs,
                "historical_seed_delta_pp": replicate_delta,
            },
            "historical_validation": {
                "evolved": _summary(validation_result),
                "market_confidence_baseline": _summary(baseline_validation),
            },
            "historical_test": {
                "evolved": _summary(test_result),
                "market_confidence_baseline": _summary(baseline_test),
                "comparison": comparison,
            },
            "historical_status": historical_status,
            "validation_point_gate_passed": validation_improvement,
            "historical_point_gate_passed": point_improvement,
            "historical_ci_gate_passed": ci_improvement,
        }
    validation_dates = [row["date"] for row in candidates if row["year"] == VALIDATION_YEAR]
    payload = {
        "schema": SCHEMA,
        "method": ("multi-objective genetic selection; one pick per KST date and profile; "
                   "mean-score ensemble across independent populations"),
        "market_probability_method": MARKET_PROBABILITY_METHOD,
        "train_years": list(TRAIN_YEARS),
        "validation_year": VALIDATION_YEAR,
        "historical_test_year": TEST_YEAR,
        "trained_through": max(validation_dates) if validation_dates else None,
        "default_profile": "balanced",
        "profiles": profiles,
        "promotion": {
            "status": "shadow_only",
            "passed": False,
            "pristine_holdout_available": False,
            "minimum_future_predictions": 300,
            "rule": ("사전등록 미래 300픽 이상에서 동일 날짜 시장확률 1순위 대비 "
                     "적중률 차이 CI 하한>0, 평균 배당·커버리지 제약 유지"),
            "reason": "2026은 이미 여러 실험에서 열람한 역사 감사 구간",
            "historical_iteration_disclosure": (
                "초기 유전자 설계의 종목 쏠림과 불공정 배당 기준을 2026 감사 후 수정했으므로 "
                "이 결과는 탐색 기록이며 독립 홀드아웃이 아니다"),
        },
    }
    return {**payload, "artifact_sha256": canonical_hash(payload)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=Path, default=DEFAULT_GAMES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--population", type=int, default=56)
    parser.add_argument("--generations", type=int, default=24)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--replicates", type=int, default=3)
    args = parser.parse_args()
    records, quality = load_unique_market_rows(args.games)
    candidates = historical_candidates(records)
    artifact = build_artifact(candidates, population_size=args.population,
                              generations=args.generations, seed=args.seed,
                              replicates=args.replicates)
    artifact["data_quality"] = quality
    artifact["artifact_sha256"] = canonical_hash(artifact)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    compact = {
        name: {
            "validation": rule["historical_validation"]["evolved"],
            "test": rule["historical_test"]["evolved"],
            "delta": rule["historical_test"]["comparison"],
        }
        for name, rule in artifact["profiles"].items()
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
