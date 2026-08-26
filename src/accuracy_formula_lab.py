"""시장보다 더 정확한 공식을 찾기 위한 누수 방지 워크포워드 실험.

높은 과거 적중률을 만드는 대신 운영 시점에 알 수 있었던 정보만으로 시장 기준선을
반복해서 개선하는지 판정한다.

1. 재발매 가격이 충돌하는 시장행을 전부 제외한다.
2. 마진 제거 방식 네 가지를 실제 경기 연도별로 비교한다.
3. 2-way 시장확률을 offset으로 고정하고 인과 팀 피처의 잔차만 ridge로 학습한다.
4. 2024년에서 ridge를 한 번 선택하고 2025·2026년은 순서대로 재학습·평가한다.
5. 날짜 블록 신뢰구간과 동일 경기 방향 비교가 문턱을 통과해야 운영 후보가 된다.

과거 아카이브 배당에는 관측시각과 판매상태가 없다. 이 보고서는 후보를 탈락시키는
데는 쓸 수 있지만 실제 T-30 수익 우위를 확정하는 자료는 아니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest

from bets import winner_index
from devig import METHODS, market_probabilities
from features import build_features
from matches import actual_game_year, clean_team, load_matches
from model_v2 import PI_GAMMA, PI_PARAMS, SPORT_FEATURES, attach_odds
from recommendation_policy import (
    automatic_selection_exclusion_reason,
    recommendation_exclusion_reason,
)
from combo_optimizer import pick_target_legs
from today_combo import bin_of, pick_legs
import pi_ratings

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GAMES = ROOT / "data" / "processed" / "games.csv"
DEFAULT_REPORT = ROOT / "findings" / "accuracy_formula_lab.json"
DATETIME_RE = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")
RIDGE_GRID = (100.0, 1_000.0, 10_000.0, 100_000.0)
TUNING_YEAR = 2024
RETROSPECTIVE_EVALUATION_YEARS = (2025, 2026)
MIN_MATERIAL_ACCURACY_GAIN = 0.005
MAX_SPORT_ACCURACY_DEGRADATION = 0.005
MAX_SPORT_BRIER_DEGRADATION = 0.0005


def _logit(values) -> np.ndarray:
    p = np.clip(np.asarray(values, dtype=float), 1e-6, 1.0 - 1e-6)
    return np.log(p / (1.0 - p))


def _sigmoid(values) -> np.ndarray:
    z = np.clip(np.asarray(values, dtype=float), -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-z))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


@dataclass(frozen=True)
class DesignState:
    columns: tuple[str, ...]
    medians: tuple[float, ...]
    scales: tuple[float, ...]


def fit_design(frame: pd.DataFrame, columns: list[str]) -> tuple[np.ndarray, DesignState]:
    cols = _unique(columns)
    medians: list[float] = []
    scales: list[float] = []
    blocks: list[np.ndarray] = [np.ones(len(frame), dtype=float)]
    for column in cols:
        values = pd.to_numeric(frame[column], errors="coerce")
        median = float(values.median()) if values.notna().any() else 0.0
        scale = float(values.std()) if values.notna().any() else 1.0
        if not math.isfinite(scale) or scale <= 1e-12:
            scale = 1.0
        blocks.append(((values.fillna(median) - median) / scale).to_numpy(float))
        blocks.append(values.isna().to_numpy(float))
        medians.append(median)
        scales.append(scale)
    return np.column_stack(blocks), DesignState(
        tuple(cols), tuple(medians), tuple(scales))


def apply_design(frame: pd.DataFrame, state: DesignState) -> np.ndarray:
    blocks: list[np.ndarray] = [np.ones(len(frame), dtype=float)]
    for column, median, scale in zip(state.columns, state.medians, state.scales):
        values = pd.to_numeric(frame[column], errors="coerce")
        blocks.append(((values.fillna(median) - median) / scale).to_numpy(float))
        blocks.append(values.isna().to_numpy(float))
    return np.column_stack(blocks)


def fit_market_offset(
    design: np.ndarray,
    outcomes: np.ndarray,
    market_probability: np.ndarray,
    ridge: float,
    max_iter: int = 80,
) -> np.ndarray:
    """logit(p)=logit(q_market)+X beta를 ridge IRLS로 적합한다."""
    x = np.asarray(design, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    offset = _logit(market_probability)
    beta = np.zeros(x.shape[1], dtype=float)
    penalty = np.full(len(beta), float(ridge), dtype=float)
    penalty[0] = float(ridge) * 0.1
    for _ in range(max_iter):
        probability = _sigmoid(offset + x @ beta)
        weight = np.clip(probability * (1.0 - probability), 1e-6, None)
        gradient = x.T @ (y - probability) - penalty * beta
        hessian = (x.T * weight) @ x + np.diag(penalty)
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        beta += step
        if float(np.max(np.abs(step))) < 1e-9:
            break
    return beta


def predict_market_offset(design: np.ndarray, market_probability, beta) -> np.ndarray:
    return _sigmoid(_logit(market_probability) + np.asarray(design) @ np.asarray(beta))


def date_block_interval(
    frame: pd.DataFrame,
    columns: list[str],
    repeats: int = 5_000,
    seed: int = 20260826,
    block_dates: int = 1,
) -> dict[str, list[float]]:
    """연속 관측일 블록을 재표집해 단기 상관을 보존한다."""
    if frame.empty:
        return {column: [float("nan"), float("nan")] for column in columns}
    dates = pd.to_datetime(frame["date"]).dt.normalize()
    grouped = frame.assign(_date=dates).groupby("_date", sort=True)
    sums = grouped[columns].sum().to_numpy(float)
    counts = grouped.size().to_numpy(float)
    rng = np.random.default_rng(seed)
    width = min(max(1, int(block_dates)), len(counts))
    n_blocks = math.ceil(len(counts) / width)
    starts = rng.integers(0, len(counts), size=(repeats, n_blocks))
    samples = (starts[:, :, None] + np.arange(width)[None, None, :]) % len(counts)
    samples = samples.reshape(repeats, -1)[:, :len(counts)]
    denominator = counts[samples].sum(axis=1)[:, None]
    values = sums[samples].sum(axis=1) / denominator
    quantiles = np.quantile(values, [0.025, 0.975], axis=0)
    return {column: [float(quantiles[0, i]), float(quantiles[1, i])]
            for i, column in enumerate(columns)}


def _market_key(row) -> tuple:
    return (
        row.kickoff, row.sport, row.league, row.home_team, row.away_team,
        row.market_family, row.booking_class, row.market_label, int(row.n_way),
    )


def load_unique_market_rows(games_path: Path) -> tuple[list[dict], dict]:
    """정산 시장행을 읽고 시점 불명 재발매 가격 충돌을 fail-close한다."""
    raw = pd.read_csv(games_path, low_memory=False)
    input_rows = len(raw)
    raw = raw[(~raw["is_void"].astype(bool)) & raw["result"].notna()].copy()
    raw["market_label"] = raw["market_label"].fillna("").astype(str)
    parsed = raw["date_text"].astype(str).str.extract(DATETIME_RE)
    parsed = parsed.apply(pd.to_numeric, errors="coerce")
    valid_date = parsed.notna().all(axis=1)
    raw = raw.loc[valid_date].copy()
    parsed = parsed.loc[valid_date]
    game_year = actual_game_year(raw["year"], raw["round"], parsed[0])
    raw["kickoff"] = pd.to_datetime(dict(
        year=game_year, month=parsed[0].astype(int), day=parsed[1].astype(int),
        hour=parsed[2].astype(int), minute=parsed[3].astype(int)), errors="coerce")
    raw["home_team"] = raw["home"].map(clean_team)
    raw["away_team"] = raw["away"].map(clean_team)

    records: dict[tuple, dict] = {}
    conflicts: set[tuple] = set()
    invalid = 0
    for row in raw.itertuples(index=False):
        try:
            odds = tuple(float(value) for value in str(row.odds).split(","))
            n_way = int(row.n_way)
            winner = winner_index(n_way, str(row.result))
            overround = float(row.overround)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if (winner is None or len(odds) != n_way or winner >= n_way
                or not all(value > 1.0 for value in odds)
                or not 1.0 <= overround <= 1.40 or pd.isna(row.kickoff)):
            invalid += 1
            continue
        key = _market_key(row)
        record = {"key": key, "date": row.kickoff, "year": int(row.kickoff.year),
                  "sport": row.sport, "league": row.league, "odds": odds,
                  "winner": int(winner), "n_way": n_way}
        previous = records.get(key)
        if previous is None:
            records[key] = record
        elif previous["odds"] != odds or previous["winner"] != int(winner):
            conflicts.add(key)
    clean = [record for key, record in records.items() if key not in conflicts]
    return clean, {
        "input_rows": input_rows,
        "valid_unique_market_rows": len(clean),
        "invalid_rows": invalid,
        "conflicting_reissues_excluded": len(conflicts),
    }


def evaluate_devig(
    games_path: Path,
    records: list[dict] | None = None,
    quality: dict | None = None,
) -> dict:
    if records is None or quality is None:
        records, quality = load_unique_market_rows(games_path)
    by_year: dict[int, dict] = {}
    paired_rows: list[dict] = []
    for record in records:
        winner = record["winner"]
        metric: dict[str, dict[str, float]] = {}
        for name, method in METHODS.items():
            probability = method(list(record["odds"]))
            metric[name] = {
                "log_loss": -math.log(max(probability[winner], 1e-12)),
                "brier": float(np.mean([
                    (value - float(index == winner)) ** 2
                    for index, value in enumerate(probability)])),
            }
        year = record["year"]
        bucket = by_year.setdefault(year, {
            name: {"n": 0, "log_loss": 0.0, "brier": 0.0} for name in METHODS})
        for name in METHODS:
            bucket[name]["n"] += 1
            bucket[name]["log_loss"] += metric[name]["log_loss"]
            bucket[name]["brier"] += metric[name]["brier"]
        paired_rows.append({
            "date": record["date"], "year": year,
            "brier_delta": metric["shin"]["brier"] - metric["multiplicative"]["brier"],
            "log_loss_delta": (metric["shin"]["log_loss"]
                               - metric["multiplicative"]["log_loss"]),
        })
    report: dict[str, dict] = {}
    paired = pd.DataFrame(paired_rows)
    for year, methods in sorted(by_year.items()):
        averaged = {
            name: {"n": int(values["n"]),
                   "log_loss": values["log_loss"] / values["n"],
                   "brier": values["brier"] / values["n"]}
            for name, values in methods.items()
        }
        delta = paired[paired["year"] == year]
        block_intervals = {
            str(block): date_block_interval(
                delta, ["brier_delta", "log_loss_delta"], repeats=3_000,
                seed=20260826 + year + block, block_dates=block)
            for block in (1, 7, 14)
        }
        report[str(year)] = {
            "methods": averaged,
            "best_log_loss": min(averaged, key=lambda name: averaged[name]["log_loss"]),
            "best_brier": min(averaged, key=lambda name: averaged[name]["brier"]),
            "shin_minus_multiplicative": {
                "brier": float(delta["brier_delta"].mean()),
                "log_loss": float(delta["log_loss_delta"].mean()),
                "ci95": block_intervals["7"],
                "ci95_by_block_dates": block_intervals,
            },
        }
    return {"quality": quality, "by_year": report}


def evaluate_combo_policy(records: list[dict]) -> dict:
    """2026년 날짜별 2배 조합에서 고정칸과 동적 탐색을 같은 후보로 비교한다."""
    by_day: dict = defaultdict(list)
    for record in records:
        if record["year"] != 2026:
            continue
        kickoff, sport, league, home, away, family, _, label, _ = record["key"]
        if recommendation_exclusion_reason(family):
            continue
        probability = market_probabilities(list(record["odds"]))
        selection = int(np.argmax(probability))
        odds = float(record["odds"][selection])
        reason = automatic_selection_exclusion_reason(
            family, odds, probability[selection], max(probability))
        odds_bin = bin_of(odds)
        if reason or not odds_bin or odds >= 2.2:
            continue
        by_day[kickoff.date()].append({
            "event_key": "|".join(map(str, (kickoff, sport, league, home, away))),
            "kickoff_at": kickoff.isoformat(),
            "market": family,
            "market_label": label,
            "sel": str(selection),
            "odds": odds,
            "bin": odds_bin,
            "overround": sum(1.0 / value for value in record["odds"]),
            "market_prob": probability[selection],
            "won": selection == record["winner"],
        })

    paired = []
    fixed_days = 0
    dynamic_days = 0
    union_days = 0
    for day, candidates in sorted(by_day.items()):
        fixed = pick_legs(
            candidates, ["1.0-1.3", "1.5-1.8"], target=2.0)
        dynamic = pick_target_legs(candidates, 2.0, 2, 4)
        fixed_days += int(bool(fixed))
        dynamic_days += int(bool(dynamic))
        union_days += int(bool(fixed or dynamic))
        if not fixed or not dynamic:
            continue

        def ticket(picks: list[dict]) -> dict:
            won = all(candidate["won"] for candidate in picks)
            odds = math.prod(float(candidate["odds"]) for candidate in picks)
            return {
                "won": int(won),
                "profit": odds - 1.0 if won else -1.0,
                "odds": odds,
                "legs": len(picks),
                "estimated_hit": math.prod(
                    float(candidate["market_prob"]) for candidate in picks),
            }

        paired.append({"date": str(day), "fixed": ticket(fixed),
                       "dynamic": ticket(dynamic)})

    def summary(name: str) -> dict:
        return {
            "hit_rate": float(np.mean([row[name]["won"] for row in paired])),
            "roi": float(np.mean([row[name]["profit"] for row in paired])),
            "mean_odds": float(np.mean([row[name]["odds"] for row in paired])),
            "mean_legs": float(np.mean([row[name]["legs"] for row in paired])),
            "mean_estimated_hit": float(np.mean(
                [row[name]["estimated_hit"] for row in paired])),
        }

    if not paired:
        return {"candidate_days": len(by_day), "fixed_available_days": fixed_days,
                "dynamic_available_days": dynamic_days, "paired_days": 0,
                "decision": "insufficient_data"}
    hit_delta = np.asarray([
        row["dynamic"]["won"] - row["fixed"]["won"] for row in paired], dtype=float)
    roi_delta = np.asarray([
        row["dynamic"]["profit"] - row["fixed"]["profit"] for row in paired], dtype=float)
    delta_frame = pd.DataFrame({
        "date": pd.to_datetime([row["date"] for row in paired]),
        "hit_delta": hit_delta,
        "roi_delta": roi_delta,
    })
    block_intervals = {
        str(block): date_block_interval(
            delta_frame, ["hit_delta", "roi_delta"], repeats=10_000,
            seed=20260826 + block, block_dates=block)
        for block in (1, 7, 14)
    }
    primary_interval = block_intervals["7"]
    dynamic_only_wins = int((hit_delta == 1).sum())
    fixed_only_wins = int((hit_delta == -1).sum())
    discordant = dynamic_only_wins + fixed_only_wins
    return {
        "target": 2.0,
        "candidate_days": len(by_day),
        "fixed_available_days": fixed_days,
        "dynamic_available_days": dynamic_days,
        "union_available_days": union_days,
        "paired_days": len(paired),
        "paired_coverage_of_union": len(paired) / union_days if union_days else 0.0,
        "fixed_bins": summary("fixed"),
        "dynamic_2_to_4_legs": summary("dynamic"),
        "dynamic_minus_fixed": {
            "hit_rate": float(hit_delta.mean()),
            "hit_rate_ci95": primary_interval["hit_delta"],
            "roi": float(roi_delta.mean()),
            "roi_ci95": primary_interval["roi_delta"],
            "ci95_by_block_dates": block_intervals,
            "dynamic_only_wins": dynamic_only_wins,
            "fixed_only_wins": fixed_only_wins,
            "mcnemar_exact_p_descriptive": float(
                binomtest(dynamic_only_wins, discordant, 0.5).pvalue)
                if discordant else 1.0,
        },
        "decision": "shadow_only_keep_fixed_default",
        "limitations": [
            "archive price timestamp is unknown",
            "both policy specifications were inspected on 2026 and are not pristine",
            "existing fixed bins were informed by statistics that include 2026",
        ],
    }


def prepare_two_way(games_path: Path) -> pd.DataFrame:
    matches = load_matches(path=games_path)
    features = build_features(matches)
    old_lambda = dict(pi_ratings.LAMBDA)
    old_damp = dict(pi_ratings.DAMP)
    old_gamma = pi_ratings.GAMMA
    try:
        pi_ratings.LAMBDA = {sport: values[0] for sport, values in PI_PARAMS.items()}
        pi_ratings.DAMP = {sport: values[1] for sport, values in PI_PARAMS.items()}
        pi_ratings.GAMMA = PI_GAMMA
        pi = pi_ratings.run_pi(matches)
    finally:
        pi_ratings.LAMBDA = old_lambda
        pi_ratings.DAMP = old_damp
        pi_ratings.GAMMA = old_gamma
    joined = features.merge(
        pi[["kickoff", "league", "home_team", "away_team", "pi_diff"]],
        on=["kickoff", "league", "home_team", "away_team"], how="inner",
        validate="one_to_one")
    joined = attach_odds(joined, games_path=games_path)
    joined = joined[joined["outcome"] != 0.5].copy()
    joined["y"] = (joined["outcome"] == 1.0).astype(float)
    joined["q"] = [market_probabilities([home, away])[0]
                   for home, away in zip(joined["o_home"], joined["o_away"])]
    return joined.sort_values(
        ["date", "league", "home_team", "away_team"]).reset_index(drop=True)


def predict_year(data: pd.DataFrame, year: int, ridge: float) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    for sport, feature_names in SPORT_FEATURES.items():
        subset = data[data["sport"] == sport]
        train = subset[subset["year"] < year]
        test = subset[subset["year"] == year]
        if len(train) < 300 or len(test) < 50:
            continue
        columns = _unique(["elo_diff", *feature_names])
        x_train, state = fit_design(train, columns)
        x_test = apply_design(test, state)
        beta = fit_market_offset(
            x_train, train["y"].to_numpy(float), train["q"].to_numpy(float), ridge)
        result = test[["date", "year", "sport", "league", "q", "y",
                       "o_home", "o_away"]].copy()
        result["p"] = predict_market_offset(
            x_test, result["q"].to_numpy(float), beta)
        result["coefficient_l2"] = float(np.linalg.norm(beta))
        outputs.append(result)
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()


def score_predictions(
    predictions: pd.DataFrame,
    bootstrap_seed: int,
    interval_repeats: int = 5_000,
    include_sports: bool = True,
) -> dict:
    frame = predictions.copy()
    y = frame["y"].to_numpy(float)
    q = frame["q"].to_numpy(float)
    p = frame["p"].to_numpy(float)
    frame["brier_delta"] = (p - y) ** 2 - (q - y) ** 2
    frame["log_loss_delta"] = (
        -y * np.log(np.clip(p, 1e-9, 1.0))
        - (1.0 - y) * np.log(np.clip(1.0 - p, 1e-9, 1.0))
        + y * np.log(np.clip(q, 1e-9, 1.0))
        + (1.0 - y) * np.log(np.clip(1.0 - q, 1e-9, 1.0)))
    market_correct = (q >= 0.5) == y.astype(bool)
    model_correct = (p >= 0.5) == y.astype(bool)
    frame["accuracy_delta"] = model_correct.astype(int) - market_correct.astype(int)
    model_only = int((model_correct & ~market_correct).sum())
    market_only = int((market_correct & ~model_correct).sum())
    discordant = model_only + market_only
    thresholds = {}
    for threshold in (0.55, 0.60, 0.65, 0.70):
        selected = np.maximum(p, 1.0 - p) >= threshold
        if selected.any():
            thresholds[f"{threshold:.2f}"] = {
                "n": int(selected.sum()),
                "coverage": float(selected.mean()),
                "model_accuracy": float(model_correct[selected].mean()),
                "market_accuracy_same_rows": float(market_correct[selected].mean()),
                "accuracy_delta": float((
                    model_correct[selected].astype(int)
                    - market_correct[selected].astype(int)).mean()),
            }
    interval_columns = ["brier_delta", "log_loss_delta", "accuracy_delta"]
    block_intervals = ({
        str(block): date_block_interval(
            frame, interval_columns, repeats=interval_repeats,
            seed=bootstrap_seed + block, block_dates=block)
        for block in (1, 7, 14)
    } if interval_repeats else {})
    by_sport = {}
    if include_sports:
        for index, (sport, subset) in enumerate(frame.groupby("sport", sort=True), 1):
            by_sport[str(sport)] = score_predictions(
                subset, bootstrap_seed + index * 100,
                interval_repeats=min(interval_repeats, 2_000),
                include_sports=False)
    return {
        "n": len(frame),
        "dates": int(pd.to_datetime(frame["date"]).dt.normalize().nunique()),
        "market_brier": float(np.mean((q - y) ** 2)),
        "model_brier": float(np.mean((p - y) ** 2)),
        "brier_delta": float(frame["brier_delta"].mean()),
        "market_log_loss": float(np.mean(
            -y * np.log(q) - (1.0 - y) * np.log(1.0 - q))),
        "model_log_loss": float(np.mean(
            -y * np.log(p) - (1.0 - y) * np.log(1.0 - p))),
        "log_loss_delta": float(frame["log_loss_delta"].mean()),
        "market_accuracy": float(market_correct.mean()),
        "model_accuracy": float(model_correct.mean()),
        "accuracy_delta": float(frame["accuracy_delta"].mean()),
        "direction_disagreement_rate": float(((p >= 0.5) != (q >= 0.5)).mean()),
        "model_only_correct": model_only,
        "market_only_correct": market_only,
        "mcnemar_exact_p": float(
            binomtest(model_only, discordant, 0.5).pvalue) if discordant else 1.0,
        "ci95": block_intervals.get("7"),
        "ci95_by_block_dates": block_intervals,
        "by_sport": by_sport,
        "absolute_confidence_thresholds": thresholds,
    }


def evaluate_residual(data: pd.DataFrame) -> dict:
    tuning = []
    for ridge in RIDGE_GRID:
        predictions = predict_year(data, TUNING_YEAR, ridge)
        score = score_predictions(
            predictions, 20260826 + int(math.log10(ridge)), interval_repeats=0,
            include_sports=False)
        tuning.append({"ridge": ridge, "n": score["n"],
                       "brier": score["model_brier"],
                       "brier_delta": score["brier_delta"],
                       "log_loss_delta": score["log_loss_delta"]})
    chosen = min(
        tuning, key=lambda row: (row["brier"], -row["ridge"]))["ridge"]
    years = {}
    for year in (TUNING_YEAR, *RETROSPECTIVE_EVALUATION_YEARS):
        predictions = predict_year(data, year, chosen)
        years[str(year)] = score_predictions(predictions, 20260826 + year)
    retrospective = [years[str(year)] for year in RETROSPECTIVE_EVALUATION_YEARS]
    statistical_gate = all(
        result["brier_delta"] < 0.0
        and result["log_loss_delta"] < 0.0
        and result["accuracy_delta"] >= MIN_MATERIAL_ACCURACY_GAIN
        and all(
            interval["brier_delta"][1] < 0.0
            and interval["log_loss_delta"][1] < 0.0
            and interval["accuracy_delta"][0] >= MIN_MATERIAL_ACCURACY_GAIN
            for interval in result["ci95_by_block_dates"].values())
        and all(
            all(
                interval["accuracy_delta"][0] >= -MAX_SPORT_ACCURACY_DEGRADATION
                and interval["brier_delta"][1] <= MAX_SPORT_BRIER_DEGRADATION
                for interval in sport_result["ci95_by_block_dates"].values())
            for sport_result in result["by_sport"].values())
        for result in retrospective)
    # 현재 저장소에는 모델 코드·피처·cutoff·예측 원장 해시를 경기 전에 고정한
    # 사전등록 manifest가 없다. 불리언 한 줄로 미래 홀드아웃을 참이라고 바꿀 수
    # 없도록, 검증기 구현 전에는 승격을 구조적으로 닫아 둔다.
    holdout_evidence = {
        "available": False,
        "reason": "no verified preregistration manifest and append-only prediction ledger",
        "required_evidence": [
            "model_code_sha256", "feature_spec_sha256", "data_cutoff_at",
            "frozen_at", "evaluation_start", "evaluation_end",
            "prediction_ledger_sha256", "minimum_300_predictions",
        ],
    }
    promoted = holdout_evidence["available"] and statistical_gate
    return {
        "formula": "logit(p_final)=logit(p_shin_market)+X_beta",
        "feature_specification": "existing SPORT_FEATURES from prior retrospective experiments",
        "feature_specification_preregistered_for_2025_2026": False,
        "ridge_grid": list(RIDGE_GRID),
        "ridge_selected_on_2024_only": chosen,
        "tuning_year": TUNING_YEAR,
        "retrospective_evaluation_years": list(RETROSPECTIVE_EVALUATION_YEARS),
        "tuning": tuning,
        "years": years,
        "promotion": {
            "status": "promote" if promoted else "shadow_only",
            "passed": promoted,
            "statistical_gate_passed": statistical_gate,
            "pristine_holdout_available": holdout_evidence["available"],
            "holdout_evidence": holdout_evidence,
            "minimum_material_accuracy_gain": MIN_MATERIAL_ACCURACY_GAIN,
            "maximum_sport_accuracy_degradation": MAX_SPORT_ACCURACY_DEGRADATION,
            "maximum_sport_brier_degradation": MAX_SPORT_BRIER_DEGRADATION,
            "rule": ("사전등록된 완전한 미래 구간에서 1·7·14일 블록 Brier·log loss "
                     "CI 상한<0, 적중 CI 하한과 점추정 모두 +0.5%p 이상, "
                     "종목별 비열등성 통과"),
            "mcnemar_is_descriptive_only": True,
        },
    }


def build_report(games_path: Path) -> dict:
    records, quality = load_unique_market_rows(games_path)
    devig = evaluate_devig(games_path, records=records, quality=quality)
    combo = evaluate_combo_policy(records)
    two_way = prepare_two_way(games_path)
    residual = evaluate_residual(two_way)
    shin_best_rank = all(
        devig["by_year"][str(year)]["best_brier"] == "shin"
        and devig["by_year"][str(year)]["best_log_loss"] == "shin"
        for year in RETROSPECTIVE_EVALUATION_YEARS)
    shin_significant = all(
        devig["by_year"][str(year)]["shin_minus_multiplicative"]["ci95"]
        ["brier_delta"][1] < 0.0
        and devig["by_year"][str(year)]["shin_minus_multiplicative"]["ci95"]
        ["log_loss_delta"][1] < 0.0
        for year in RETROSPECTIVE_EVALUATION_YEARS)
    try:
        display_path = str(games_path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        display_path = str(games_path)
    return {
        "model_version": "accuracy-formula-lab-v1",
        "causal_row_construction": {
            "training_rows_precede_each_evaluation_year": True,
            "same_day_results_are_deferred": True,
            "feature_specification_preregistered": False,
            "pristine_holdout_available": False,
        },
        "data": {"path": display_path, "sha256": _sha256(games_path),
                 "rows": int(sum(1 for _ in games_path.open("rb")) - 1)},
        "limitations": [
            "archive odds timestamp and sale_open are unavailable",
            "2025 and 2026 informed earlier feature experiments and are not pristine holdouts",
            "2026 is a partial year",
            "accuracy gain is selection-dependent and is not the same as positive ROI",
        ],
        "devig": {
            **devig,
            "best_rank_both_retrospective_years": shin_best_rank,
            "significant_both_retrospective_years": shin_significant,
            "decision": ("retain_existing_shin_no_new_accuracy_claim"
                         if shin_best_rank else "research_only"),
        },
        "market_offset_residual": residual,
        "combo_selector": combo,
        "operating_decision": (
            "market_only_with_shadow_residual"
            if not residual["promotion"]["passed"] else "promote_residual"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=Path, default=DEFAULT_GAMES)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = build_report(args.games)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    candidate = report["market_offset_residual"]
    print(f"report: {args.out}")
    print(f"ridge: {candidate['ridge_selected_on_2024_only']:.0f}")
    for year, result in candidate["years"].items():
        print(f"{year}: n={result['n']:,} Brier delta={result['brier_delta']:+.6f} "
              f"accuracy delta={result['accuracy_delta']:+.2%}")
    print(f"decision: {report['operating_decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
