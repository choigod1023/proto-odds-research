"""교차 마켓 모순 탐지 — 목표 마켓을 보지 않고 그 가격을 예측한다.

같은 경기의 승패·핸디캡·언더오버·마진밴드·홀짝은 하나의 잠재 스코어
분포와 양립해야 한다. 목표 마켓 *가족 전체*를 제외한 나머지 프로토 가격으로
홈/원정 기대득점(λ_home, λ_away)을 적합하고, 제외한 마켓의 확률을 복원한다.

이후 실제 결과로 다음 두 가지를 검정한다.

1. 교차 마켓 확률의 Brier가 목표 마켓 자체의 devig 확률보다 정확한가.
2. 교차 마켓 확률로 계산한 EV가 양수인 선택지의 실제 ROI가 양수인가.

가격은 지정된 경기 전 시점에 실제 관측된 값만 사용하고, 같은 실제 경기가 여러
회차에 중복된 경우 한 경기당 한 번만 베팅한다.

사용:
    python src/cross_market_edge.py
    python src/cross_market_edge.py --selftest
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bets import _WINNER  # noqa: E402
from devig import market_probabilities  # noqa: E402
from score_dist import (  # noqa: E402
    joint,
    p_handicap,
    p_odd,
    p_one_run,
    p_over,
    p_win,
)


SNAP_DIR = ROOT / "data" / "raw" / "snapshots"
COLS = [
    "ts", "year", "round", "game_no", "sport", "league", "market_family",
    "n_way", "market_label", "home", "away", "date_text", "odds", "result",
]
SPORTS = {"bs", "sc"}
FAMILIES = {"승패", "승무패", "승①패", "언더오버", "핸디캡", "홀짝"}
TEAM_NUM_PRE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s+")
TEAM_NUM_SUF = re.compile(r"\s+-?\d+(?:\.\d+)?\s*$")
DATE_TIME = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")
LINE_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)")

# 적합 범위와 시작값. 24일 스냅샷에서 실제 라인이 놓이는 범위를 넉넉히 덮는다.
BOUNDS = {
    "bs": ((0.35, 12.0), (0.35, 12.0)),
    "sc": ((0.05, 6.0), (0.05, 6.0)),
}
STARTS = {
    "bs": ((4.6, 4.2), (6.0, 3.0), (3.0, 6.0)),
    "sc": ((1.5, 1.2), (2.4, 0.8), (0.8, 2.4)),
}


def clean_team(value: object) -> str:
    text = str(value).strip()
    return TEAM_NUM_SUF.sub("", TEAM_NUM_PRE.sub("", text)).strip()


def parse_odds(value: object, n_way: int) -> np.ndarray | None:
    try:
        out = np.asarray([float(x) for x in str(value).split(",")], dtype=float)
    except (TypeError, ValueError):
        return None
    if len(out) != n_way or not np.isfinite(out).all() or (out <= 1.001).any():
        return None
    return out


def devig(odds: np.ndarray) -> np.ndarray:
    return np.asarray(market_probabilities(odds.tolist()), dtype=float)


def parse_line(label: object) -> float | None:
    match = LINE_RE.search(str(label or ""))
    return float(match.group(1)) if match else None


def model_probs(
    sport: str,
    family: str,
    n_way: int,
    label: object,
    lam_home: float,
    lam_away: float,
) -> np.ndarray | None:
    """잠재 스코어 분포에서 프로토 선택지 순서의 확률을 만든다."""
    matrix = joint(lam_home, lam_away, sport)
    if family == "승패" and n_way == 2:
        home, _, away = p_win(matrix)
        total = home + away
        probs = [home / total, away / total] if total > 0 else None
    elif family == "승무패" and n_way == 3:
        probs = p_win(matrix)
    elif family == "승①패" and n_way == 3:
        probs = p_one_run(matrix)
    elif family == "언더오버" and n_way == 2:
        line = parse_line(label)
        if line is None:
            return None
        over = p_over(matrix, line)
        probs = [1.0 - over, over]
    elif family == "핸디캡":
        handicap = parse_line(label)
        if handicap is None:
            return None
        win, draw, lose = p_handicap(matrix, handicap)
        if n_way == 2:
            total = win + lose
            probs = [win / total, lose / total] if total > 0 else None
        elif n_way == 3:
            probs = [win, draw, lose]
        else:
            return None
    elif family == "홀짝" and n_way == 2:
        odd = p_odd(matrix)
        probs = [odd, 1.0 - odd]
    else:
        return None

    if probs is None:
        return None
    out = np.asarray(probs, dtype=float)
    if len(out) != n_way or not np.isfinite(out).all() or (out < 0).any():
        return None
    total = out.sum()
    return out / total if total > 0 else None


def load_snapshots() -> tuple[pd.DataFrame, dict]:
    files = sorted(SNAP_DIR.glob("odds_timeseries_*.csv"))
    if not files:
        raise FileNotFoundError("프로토 스냅샷이 없습니다")
    frames = []
    raw_rows = 0
    for file in files:
        part = pd.read_csv(
            file,
            usecols=COLS,
            dtype=str,
            on_bad_lines="skip",
            low_memory=False,
        )
        raw_rows += len(part)
        frames.append(part)
    data = pd.concat(frames, ignore_index=True)

    for column in ("year", "round", "game_no", "n_way"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["ts"] = pd.to_datetime(data["ts"], errors="coerce", utc=True)
    dt = data["date_text"].astype(str).str.extract(DATE_TIME)
    dt = dt.apply(pd.to_numeric, errors="coerce")
    naive = pd.to_datetime(
        {
            "year": data["year"],
            "month": dt[0],
            "day": dt[1],
            "hour": dt[2],
            "minute": dt[3],
        },
        errors="coerce",
    )
    data["kickoff"] = naive.dt.tz_localize(
        "Asia/Seoul", ambiguous="NaT", nonexistent="NaT"
    ).dt.tz_convert("UTC")
    data = data.dropna(
        subset=["ts", "kickoff", "year", "round", "game_no", "n_way"]
    ).copy()
    data[["year", "round", "game_no", "n_way"]] = data[
        ["year", "round", "game_no", "n_way"]
    ].astype(int)
    data = data[
        data["sport"].isin(SPORTS)
        & data["market_family"].isin(FAMILIES)
        & data["n_way"].isin([2, 3])
    ].copy()

    data["home_team"] = data["home"].map(clean_team)
    data["away_team"] = data["away"].map(clean_team)
    data["market_id"] = (
        data["year"].astype(str)
        + "-"
        + data["round"].astype(str)
        + "-"
        + data["game_no"].astype(str)
    )
    data["event_id"] = (
        data["league"].astype(str)
        + "|"
        + data["kickoff"].astype(str)
        + "|"
        + data["home_team"]
        + "|"
        + data["away_team"]
    )
    data["market_signature"] = (
        data["market_family"].astype(str)
        + "|"
        + data["n_way"].astype(str)
        + "|"
        + data["market_label"].fillna("").astype(str)
    )
    data = data.sort_values(["market_id", "ts"]).drop_duplicates(
        ["market_id", "ts"], keep="last"
    )
    return data, {
        "files": len(files),
        "raw_rows": raw_rows,
        "usable_rows": len(data),
        "first_observed": data["ts"].min().isoformat(),
        "last_observed": data["ts"].max().isoformat(),
    }


def settled_markets(data: pd.DataFrame) -> pd.DataFrame:
    mask = [
        (int(n_way), str(result)) in _WINNER
        for n_way, result in zip(data["n_way"], data["result"])
    ]
    settled = data[mask].copy()
    settled["winner_idx"] = [
        _WINNER[(int(n_way), str(result))]
        for n_way, result in zip(settled["n_way"], settled["result"])
    ]
    return settled.groupby("market_id", sort=False).tail(1)[
        ["market_id", "winner_idx", "result"]
    ]


def prices_at_cutoff(
    data: pd.DataFrame,
    settled: pd.DataFrame,
    cutoff_min: int,
    stale_min: int = 35,
) -> pd.DataFrame:
    pre = data[data["ts"] < data["kickoff"]].merge(
        settled, on="market_id", how="inner", validate="many_to_one"
    )
    target = pre["kickoff"] - pd.to_timedelta(cutoff_min, unit="m")
    age = target - pre["ts"]
    current = pre[(age >= pd.Timedelta(0)) & (age <= pd.Timedelta(minutes=stale_min))]
    current = current.groupby("market_id", sort=False).tail(1).copy()

    valid = []
    odds_col = []
    probs_col = []
    payout_col = []
    for row in current.itertuples(index=False):
        odds = parse_odds(row.odds, int(row.n_way))
        valid.append(odds is not None)
        odds_col.append(odds)
        probs_col.append(devig(odds) if odds is not None else None)
        payout_col.append(1.0 / np.sum(1.0 / odds) if odds is not None else np.nan)
    current["valid_odds"] = valid
    current["odds_vec"] = odds_col
    current["market_probs"] = probs_col
    current["payout"] = payout_col
    return current[current["valid_odds"]].drop(columns="valid_odds")


@dataclass(frozen=True)
class MarketInput:
    family: str
    n_way: int
    label: str
    probs: np.ndarray


@dataclass(frozen=True)
class FitResult:
    lam_home: float
    lam_away: float
    rmse: float
    source_markets: int
    source_families: int


def collapse_inputs(rows: pd.DataFrame) -> list[MarketInput]:
    """겹친 회차의 동일 마켓은 devig 확률을 평균해 적합 가중치 중복을 막는다."""
    out = []
    for _, group in rows.groupby("market_signature", sort=False):
        first = group.iloc[0]
        probs = np.mean(np.stack(group["market_probs"].to_list()), axis=0)
        out.append(
            MarketInput(
                family=str(first["market_family"]),
                n_way=int(first["n_way"]),
                label=str(first["market_label"] if pd.notna(first["market_label"]) else ""),
                probs=probs,
            )
        )
    return out


def _residuals(theta: np.ndarray, sport: str, markets: list[MarketInput]) -> np.ndarray:
    lam_home, lam_away = np.exp(theta)
    residuals = []
    for market in markets:
        predicted = model_probs(
            sport,
            market.family,
            market.n_way,
            market.label,
            float(lam_home),
            float(lam_away),
        )
        if predicted is None:
            return np.full(sum(m.n_way for m in markets), 10.0)
        # 각 마켓이 선택지 수와 무관하게 같은 총 가중치를 갖게 한다.
        residuals.extend((predicted - market.probs) / np.sqrt(market.n_way))
    return np.asarray(residuals, dtype=float)


def fit_score_distribution(sport: str, markets: list[MarketInput]) -> FitResult | None:
    families = {market.family for market in markets}
    if sport not in BOUNDS or len(markets) < 2 or len(families) < 2:
        return None
    bounds = [(np.log(low), np.log(high)) for low, high in BOUNDS[sport]]
    best = None
    for start in STARTS[sport]:
        result = minimize(
            lambda theta: float(np.mean(_residuals(theta, sport, markets) ** 2)),
            x0=np.log(start),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 120, "ftol": 1e-12},
        )
        if not np.isfinite(result.fun):
            continue
        if best is None or result.fun < best.fun:
            best = result
    if best is None:
        return None
    lam_home, lam_away = np.exp(best.x)
    residuals = _residuals(best.x, sport, markets)
    return FitResult(
        lam_home=float(lam_home),
        lam_away=float(lam_away),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        source_markets=len(markets),
        source_families=len(families),
    )


def cross_market_records(current: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """마켓 단위 정확도 레코드와 선택지 단위 EV 레코드를 만든다."""
    market_records = []
    leg_records = []
    fit_attempts = fit_success = 0
    for event_id, event in current.groupby("event_id", sort=False):
        sport = str(event["sport"].iloc[0])
        all_inputs = collapse_inputs(event)
        available_families = {market.family for market in all_inputs}
        if len(available_families) < 3:
            continue
        fit_cache: dict[tuple[str, ...], FitResult | None] = {}

        def get_fit(excluded: set[str]) -> FitResult | None:
            nonlocal fit_attempts, fit_success
            key = tuple(sorted(excluded))
            if key not in fit_cache:
                fit_attempts += 1
                sources = [market for market in all_inputs if market.family not in excluded]
                fit_cache[key] = fit_score_distribution(sport, sources)
                if fit_cache[key] is not None:
                    fit_success += 1
            return fit_cache[key]

        for target_family, targets in event.groupby("market_family", sort=False):
            fit = get_fit({str(target_family)})
            if fit is None:
                continue
            source_families = available_families - {str(target_family)}

            # 동일 마켓이 여러 회차에 있으면 정확도 평가는 환급률이 가장 높은 한 행만 쓴다.
            accuracy_targets = (
                targets.sort_values("payout", ascending=False)
                .drop_duplicates("market_signature", keep="first")
            )
            for target in targets.itertuples(index=False):
                predicted = model_probs(
                    sport,
                    str(target.market_family),
                    int(target.n_way),
                    target.market_label,
                    fit.lam_home,
                    fit.lam_away,
                )
                if predicted is None:
                    continue

                # 소스 가족 하나씩을 추가로 뺀 jackknife로 목표확률의 불안정성을 잰다.
                jackknife = [predicted]
                for source_family in source_families:
                    jk_fit = get_fit({str(target_family), str(source_family)})
                    if jk_fit is None:
                        continue
                    jk_pred = model_probs(
                        sport,
                        str(target.market_family),
                        int(target.n_way),
                        target.market_label,
                        jk_fit.lam_home,
                        jk_fit.lam_away,
                    )
                    if jk_pred is not None:
                        jackknife.append(jk_pred)
                prediction_sd = np.std(np.stack(jackknife), axis=0, ddof=0)
                # 소스 가격 적합 오차도 확률 오차의 하한으로 취급한다.
                sigma = np.maximum(prediction_sd, fit.rmse)
                lower_p = np.clip(predicted - 1.645 * sigma, 0.0, 1.0)
                odds = target.odds_vec
                winner_idx = int(target.winner_idx)
                for selection in range(int(target.n_way)):
                    hit = int(selection == winner_idx)
                    offered = float(odds[selection])
                    leg_records.append(
                        {
                            "event_id": event_id,
                            "market_id": target.market_id,
                            "market_signature": target.market_signature,
                            "kickoff": target.kickoff,
                            "sport": sport,
                            "league": target.league,
                            "target_family": target.market_family,
                            "target_label": target.market_label,
                            "selection": selection,
                            "odds": offered,
                            "p_cross": float(predicted[selection]),
                            "p_proto": float(target.market_probs[selection]),
                            "probability_sigma": float(sigma[selection]),
                            "raw_ev": float(predicted[selection] * offered - 1.0),
                            "robust_ev": float(lower_p[selection] * offered - 1.0),
                            "fit_rmse": fit.rmse,
                            "source_markets": fit.source_markets,
                            "source_families": fit.source_families,
                            "hit": hit,
                            "ret": offered - 1.0 if hit else -1.0,
                        }
                    )

            for target in accuracy_targets.itertuples(index=False):
                predicted = model_probs(
                    sport,
                    str(target.market_family),
                    int(target.n_way),
                    target.market_label,
                    fit.lam_home,
                    fit.lam_away,
                )
                if predicted is None:
                    continue
                outcome = np.zeros(int(target.n_way), dtype=float)
                outcome[int(target.winner_idx)] = 1.0
                market_records.append(
                    {
                        "event_id": event_id,
                        "market_signature": target.market_signature,
                        "kickoff": target.kickoff,
                        "sport": sport,
                        "league": target.league,
                        "target_family": target.market_family,
                        "fit_rmse": fit.rmse,
                        "brier_cross": float(np.mean((predicted - outcome) ** 2)),
                        "brier_proto": float(np.mean((target.market_probs - outcome) ** 2)),
                    }
                )
    return pd.DataFrame(market_records), pd.DataFrame(leg_records), {
        "fit_attempts": fit_attempts,
        "fit_success": fit_success,
    }


def bootstrap_mean_ci(values: np.ndarray, seed: int = 42, n_boot: int = 10000) -> list[float | None]:
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return [None, None]
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    chunk = 500
    for start in range(0, n_boot, chunk):
        size = min(chunk, n_boot - start)
        idx = rng.integers(0, len(values), size=(size, len(values)))
        means[start:start + size] = values[idx].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return [float(lo), float(hi)]


def brier_summary(markets: pd.DataFrame, label: str) -> dict:
    if markets.empty:
        return {"label": label, "n_markets": 0}
    event_diff = (
        markets.assign(diff=markets["brier_cross"] - markets["brier_proto"])
        .groupby("event_id")["diff"]
        .mean()
    )
    return {
        "label": label,
        "n_markets": int(len(markets)),
        "n_events": int(markets["event_id"].nunique()),
        "brier_cross": float(markets["brier_cross"].mean()),
        "brier_proto": float(markets["brier_proto"].mean()),
        "difference_cross_minus_proto": float(event_diff.mean()),
        "difference_ci95": bootstrap_mean_ci(event_diff.to_numpy()),
    }


def select_bets(
    legs: pd.DataFrame,
    score: str,
    threshold: float,
    max_fit_rmse: float,
) -> pd.DataFrame:
    if legs.empty:
        return legs
    eligible = legs[(legs[score] > threshold) & (legs["fit_rmse"] <= max_fit_rmse)].copy()
    if eligible.empty:
        return eligible
    # 회차·마켓이 여러 개여도 실제 경기당 가장 높은 보수적/원시 EV 한 번만 산다.
    return (
        eligible.sort_values(["event_id", score, "odds"], ascending=[True, False, False])
        .drop_duplicates("event_id", keep="first")
        .sort_values("kickoff")
        .reset_index(drop=True)
    )


def bet_summary(bets: pd.DataFrame, label: str) -> dict:
    if bets.empty:
        return {"label": label, "n": 0}
    return {
        "label": label,
        "n": int(len(bets)),
        "hit_rate": float(bets["hit"].mean()),
        "avg_odds": float(bets["odds"].mean()),
        "avg_raw_ev": float(bets["raw_ev"].mean()),
        "avg_robust_ev": float(bets["robust_ev"].mean()),
        "roi": float(bets["ret"].mean()),
        "roi_ci95": bootstrap_mean_ci(bets["ret"].to_numpy()),
        "profit_units": float(bets["ret"].sum()),
        "first_kickoff": bets["kickoff"].min().isoformat(),
        "last_kickoff": bets["kickoff"].max().isoformat(),
    }


def chronological_split(frame: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        return None, frame, frame
    events = frame[["event_id", "kickoff"]].drop_duplicates().sort_values("kickoff")
    index = max(1, int(len(events) * 2 / 3))
    split = events.iloc[min(index, len(events) - 1)]["kickoff"]
    return split, frame[frame["kickoff"] < split], frame[frame["kickoff"] >= split]


def analyze_cutoff(
    data: pd.DataFrame,
    settled: pd.DataFrame,
    cutoff_min: int,
) -> dict:
    current = prices_at_cutoff(data, settled, cutoff_min)
    markets, legs, fit_meta = cross_market_records(current)
    split, early_markets, holdout_markets = chronological_split(markets)

    primary = select_bets(legs, "robust_ev", 0.0, 0.05)
    if split is None:
        early_primary = primary
        holdout_primary = primary
    else:
        early_primary = primary[primary["kickoff"] < split]
        holdout_primary = primary[primary["kickoff"] >= split]

    sensitivity = []
    for score, thresholds in (("raw_ev", (0.0, 0.05, 0.10, 0.20)), ("robust_ev", (0.0, 0.05))):
        for threshold in thresholds:
            for max_rmse in (0.03, 0.05, 0.08):
                bets = select_bets(legs, score, threshold, max_rmse)
                sensitivity.append(
                    bet_summary(bets, f"{score}>{threshold:.0%}|rmse<={max_rmse:.0%}")
                )

    family_brier = []
    for (sport, family), group in markets.groupby(["sport", "target_family"]):
        family_brier.append(brier_summary(group, f"{sport}|{family}"))

    return {
        "cutoff_min": cutoff_min,
        "current_markets": int(len(current)),
        "current_events": int(current["event_id"].nunique()),
        "fit_meta": fit_meta,
        "prediction_markets": int(len(markets)),
        "prediction_events": int(markets["event_id"].nunique()) if not markets.empty else 0,
        "brier": brier_summary(markets, "전체"),
        "brier_early": brier_summary(early_markets, "시간순 앞 2/3"),
        "brier_holdout": brier_summary(holdout_markets, "시간순 뒤 1/3"),
        "family_brier": family_brier,
        "split_time": split.isoformat() if split is not None else None,
        "primary_rule": "robust_ev>0, source fit RMSE<=5%, 실제 경기당 1회",
        "primary": bet_summary(primary, "주 검정 전체"),
        "primary_early": bet_summary(early_primary, "주 검정 앞 2/3"),
        "primary_holdout": bet_summary(holdout_primary, "주 검정 뒤 1/3"),
        "sensitivity": sensitivity,
    }


def selftest() -> int:
    failures = []
    for sport, lam_home, lam_away in (("bs", 5.1, 4.2), ("sc", 1.8, 1.1)):
        specs = [
            ("승패", 2, ""),
            ("승무패", 3, ""),
            ("언더오버", 2, "U 2.5" if sport == "sc" else "U 8.5"),
            ("핸디캡", 3 if sport == "sc" else 2, "H -1.0" if sport == "sc" else "H -1.5"),
            ("홀짝", 2, ""),
        ]
        inputs = []
        for family, n_way, label in specs:
            probs = model_probs(sport, family, n_way, label, lam_home, lam_away)
            if probs is not None:
                inputs.append(MarketInput(family, n_way, label, probs))
        target_family = "승패"
        fit = fit_score_distribution(sport, [x for x in inputs if x.family != target_family])
        if fit is None:
            failures.append(f"{sport}: 적합 실패")
            continue
        actual = model_probs(sport, "승패", 2, "", lam_home, lam_away)
        predicted = model_probs(sport, "승패", 2, "", fit.lam_home, fit.lam_away)
        if actual is None or predicted is None or np.max(np.abs(actual - predicted)) > 0.015:
            failures.append(f"{sport}: leave-family-out 복원 오차 초과")
        if fit.rmse > 0.01:
            failures.append(f"{sport}: 합성가격 적합 RMSE {fit.rmse:.4f}")

    if failures:
        print("교차 마켓 자기검사 실패")
        for failure in failures:
            print(" -", failure)
        return 1
    print("교차 마켓 자기검사 통과")
    return 0


def main() -> int:
    data, source_meta = load_snapshots()
    settled = settled_markets(data)
    report = {
        "method": "leave-one-market-family-out latent score distribution",
        "source": source_meta,
        "settled_markets": int(len(settled)),
        "cutoffs": [],
    }
    for cutoff in (90, 30, 10):
        report["cutoffs"].append(analyze_cutoff(data, settled, cutoff))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    raise SystemExit(main())
