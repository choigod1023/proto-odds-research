"""시장 반대 + 컨텍스트 모델 + 선발 xFIP 합의 규칙의 시간분리 검증.

2023 적합, 2024 규칙 선택, 2023~24 재적합, 2025+ 고정 홀드아웃 평가.
홀드아웃 결과를 본 뒤 문턱을 바꾸지 않도록 후보와 선택 기준을 코드에 고정한다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import context_ablation as ca
from pitcher_er import _inn
from pitcher_xfip import build, load_full

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "findings" / "xfip_disagreement_gate.json"
MIN_PROMOTION_N = 300
PRIMARY_RULE = {"market_max": 1.0, "edge_min": 0.0, "xfip_margin": 0.0, "ev_min": -1.0}


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return math.nan, math.nan
    p = successes / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return center - half, center + half


def pitcher_frame() -> pd.DataFrame:
    raw = load_full()
    train = raw[raw["date"] < "2025-01-01"]
    totals = {key: 0.0 for key in ("ip", "er", "hr", "bb", "kk")}
    for row in train.itertuples():
        for pitcher in (row.home_sp, row.away_sp):
            totals["ip"] += _inn(pitcher.get("inn"))
            for key in ("er", "hr", "bb", "kk"):
                totals[key] += float(pitcher.get(key) or 0)
    fip_c = (totals["er"] / totals["ip"] * 9
             - (13 * totals["hr"] + 3 * totals["bb"] - 2 * totals["kk"]) / totals["ip"])
    lg_hr9 = totals["hr"] / totals["ip"] * 9
    return build(raw, fip_c, lg_hr9)


def prepare() -> pd.DataFrame:
    frame = ca.prepare()
    frame = frame[(frame["sport"] == "bs") & (frame["league"] == "KBO")].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    team_map = json.loads((ROOT / "data" / "processed" / "team_map.json").read_text(
        encoding="utf-8")).get("KBO", {})
    for column in ("home_team", "away_team"):
        frame[column] = frame[column].map(lambda team: team_map.get(team, team))
    pitchers = pitcher_frame()
    # 양쪽 원천 모두 공식 경기 날짜와 정규화된 팀명을 사용한다.
    return frame.merge(pitchers, on=["date", "home_team", "away_team"], how="inner")


def fit_context(fit: pd.DataFrame, tune: pd.DataFrame, train: pd.DataFrame):
    columns = [c for c in ca.GROUPS["team_plus_schedule"] if c in train.columns]
    choices = []
    for ridge in ca.RIDGES:
        beta, transform = ca.fit_offset(fit, columns, ridge)
        score = ca.metrics(tune, ca.predict(tune, columns, beta, transform))["logloss"]
        choices.append((score, ridge))
    ridge = min(choices)[1]
    beta, transform = ca.fit_offset(train, columns, ridge)
    return columns, ridge, beta, transform


def candidates() -> list[dict]:
    return [
        {"market_max": market_max, "edge_min": edge_min,
         "xfip_margin": xfip_margin, "ev_min": ev_min}
        for market_max in (.55, .60, .65)
        for edge_min in (.00, .01, .02)
        for xfip_margin in (.00, .15, .30)
        for ev_min in (-.05, .00)
    ]


def select(frame: pd.DataFrame, p_model: np.ndarray, rule: dict) -> np.ndarray:
    p_market = frame["p_market"].to_numpy(float)
    model_home = p_model >= .5
    market_home = p_market >= .5
    selected_prob = np.where(model_home, p_model, 1 - p_model)
    selected_market = np.where(model_home, p_market, 1 - p_market)
    selected_odds = np.where(model_home, frame["o_home"], frame["o_away"])
    xfip = frame["xfip_diff"].to_numpy(float)  # 양수면 홈 선발 우위
    xfip_agrees = np.where(model_home, xfip >= rule["xfip_margin"],
                           xfip <= -rule["xfip_margin"])
    return ((model_home != market_home)
            & (np.maximum(p_market, 1 - p_market) <= rule["market_max"])
            & ((selected_prob - selected_market) >= rule["edge_min"])
            & xfip_agrees
            & ((selected_prob * selected_odds - 1) >= rule["ev_min"]))


def evaluate(frame: pd.DataFrame, p_model: np.ndarray, mask: np.ndarray) -> dict:
    chosen = frame.loc[mask]
    if chosen.empty:
        return {"n": 0}
    home = p_model[mask] >= .5
    y = chosen["y"].to_numpy(float)
    hit = np.where(home, y == 1, y == 0)
    odds = np.where(home, chosen["o_home"], chosen["o_away"])
    lo, hi = wilson(int(hit.sum()), len(hit))
    return {"n": int(len(hit)), "wins": int(hit.sum()), "accuracy": float(hit.mean()),
            "accuracy_wilson95": [lo, hi], "average_odds": float(odds.mean()),
            "roi": float(np.mean(np.where(hit, odds - 1, -1.0))),
            "mean_model_probability": float(np.mean(np.where(home, p_model[mask], 1-p_model[mask])))}


def main() -> int:
    frame = prepare().dropna(subset=["xfip_diff"]).copy()
    fit, tune = frame[frame["year"] == 2023], frame[frame["year"] == 2024]
    train, test = frame[frame["year"] <= 2024], frame[frame["year"] >= 2025]
    columns, ridge, beta, transform = fit_context(fit, tune, train)

    # 규칙 선택용 확률은 2023만 적합해 2024에 적용한다.
    beta_tune, transform_tune = ca.fit_offset(fit, columns, ridge)
    p_tune = ca.predict(tune, columns, beta_tune, transform_tune)
    exploratory = []
    for rule in candidates():
        result = evaluate(tune, p_tune, select(tune, p_tune, rule))
        exploratory.append({"rule": rule, "result": result})
    # 2024에서 가장 넓은 규칙조차 30건 미만이다. 문턱 최적화 대신 원가설을 그대로 고정한다.
    rule = PRIMARY_RULE
    tune_result = evaluate(tune, p_tune, select(tune, p_tune, rule))

    p_test = ca.predict(test, columns, beta, transform)
    test_result = evaluate(test, p_test, select(test, p_test, rule))
    test_result["promotion_status"] = (
        "promote" if test_result["n"] >= MIN_PROMOTION_N
        and test_result["accuracy_wilson95"][0] > .5 and test_result["roi"] > 0
        else "research_only")
    report = {
        "protocol": "2023 fit; 2024 rule selection; <=2024 refit; >=2025 untouched test",
        "selection_objective": "none: sparse 2024 data; use pre-specified broad hypothesis",
        "promotion_gate": "test n>=300, Wilson lower>50%, ROI>0",
        "joined_rows": int(len(frame)), "split_n": {"fit": len(fit), "tune": len(tune), "test": len(test)},
        "context_ridge": ridge, "context_columns": columns,
        "selected_rule": rule, "tune": tune_result, "test": test_result,
        "candidate_count": len(candidates()),
        "max_exploratory_tune_n": max(item["result"]["n"] for item in exploratory),
        "exploratory_candidates": exploratory,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
