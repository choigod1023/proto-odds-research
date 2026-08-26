"""KBO 과거 경기를 당시 정보만으로 다시 예측하는 월별 워크포워드 실험."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import context_ablation as ca
from xfip_disagreement_gate import prepare
from xfip_residual_models import CONTEXT, blend, enrich, score

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "findings" / "historical_replay.json"
MIN_TRAIN = 300
BLENDS = (0.0, .25, .5, .75, 1.0)

FEATURES = {
    "xfip": ["xfip_diff"],
    "context_xfip": CONTEXT + ["xfip_diff"],
    "nonlinear": CONTEXT + ["xfip_diff", "xfip_signed_square", "xfip_market_interaction"],
}


@dataclass(frozen=True)
class Config:
    features: str
    window_days: int | None
    ridge: float = 100.0

    @property
    def name(self):
        return f"{self.features}_w{self.window_days or 'all'}_r{self.ridge:g}"


CONFIGS = [Config(features, window) for features in FEATURES for window in (365, 730, None)]


def replay(frame: pd.DataFrame, config: Config) -> pd.DataFrame:
    columns = FEATURES[config.features]
    rows = []
    start = frame["date"].min().to_period("M") + 1
    end = frame["date"].max().to_period("M")
    for period in pd.period_range(start, end, freq="M"):
        cutoff = period.start_time
        train = frame[frame["date"] < cutoff]
        if config.window_days:
            train = train[train["date"] >= cutoff-pd.Timedelta(days=config.window_days)]
        test = frame[frame["date"].dt.to_period("M") == period]
        if len(train) < MIN_TRAIN or test.empty:
            continue
        beta, transform = ca.fit_offset(train, columns, config.ridge)
        pred = ca.predict(test, columns, beta, transform)
        part = test[["date", "year", "home_team", "away_team", "y", "p_market",
                     "o_home", "o_away", "xfip_diff"]].copy()
        part["p_model"] = pred
        part["train_n"] = len(train)
        rows.append(part)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def calibration(frame: pd.DataFrame, p: np.ndarray) -> dict:
    bins = pd.qcut(pd.Series(p), q=5, duplicates="drop")
    temp = pd.DataFrame({"bin": bins.astype(str), "p": p, "y": frame["y"].to_numpy(float)})
    rows, ece = [], 0.0
    for label, group in temp.groupby("bin", observed=True):
        gap = group["y"].mean()-group["p"].mean()
        ece += len(group)/len(temp)*abs(gap)
        rows.append({"bin": label, "n": len(group), "predicted": group["p"].mean(),
                     "observed": group["y"].mean(), "gap": gap})
    return {"ece": float(ece), "bins": rows}


def compare_slice(frame: pd.DataFrame, mask: np.ndarray, p: np.ndarray) -> dict:
    sub = frame.loc[mask]
    if sub.empty:
        return {"n": 0}
    pm = p[mask]
    market = sub["p_market"].to_numpy(float)
    sm, sb = score(sub, pm), score(sub, market)
    return {"n": len(sub), "model": sm, "market": sb,
            "accuracy_uplift_pp": (sm["accuracy"]-sb["accuracy"])*100,
            "brier_delta": sm["brier"]-sb["brier"], "roi_uplift_pp": (sm["roi"]-sb["roi"])*100}


def fixed_slices(frame: pd.DataFrame, p: np.ndarray) -> dict:
    market_strength = np.maximum(frame["p_market"], 1-frame["p_market"]).to_numpy(float)
    xfip = frame["xfip_diff"].abs().to_numpy(float)
    agreement = (p >= .5) == (frame["p_market"].to_numpy(float) >= .5)
    month = frame["date"].dt.month.to_numpy()
    return {
        "market_close": compare_slice(frame, market_strength <= .55, p),
        "market_mid": compare_slice(frame, (market_strength > .55) & (market_strength <= .65), p),
        "market_strong": compare_slice(frame, market_strength > .65, p),
        "xfip_small": compare_slice(frame, xfip <= .25, p),
        "xfip_medium": compare_slice(frame, (xfip > .25) & (xfip <= .75), p),
        "xfip_large": compare_slice(frame, xfip > .75, p),
        "model_market_agree": compare_slice(frame, agreement, p),
        "model_market_disagree": compare_slice(frame, ~agreement, p),
        "early_season": compare_slice(frame, month <= 5, p),
        "mid_season": compare_slice(frame, (month >= 6) & (month <= 7), p),
        "late_season": compare_slice(frame, month >= 8, p),
    }


def slice_mask(frame: pd.DataFrame, name: str, p: np.ndarray) -> np.ndarray:
    strength = np.maximum(frame["p_market"], 1-frame["p_market"]).to_numpy(float)
    xfip = frame["xfip_diff"].abs().to_numpy(float)
    agreement = (p >= .5) == (frame["p_market"].to_numpy(float) >= .5)
    month = frame["date"].dt.month.to_numpy()
    masks = {"market_close": strength <= .55, "market_mid": (strength > .55) & (strength <= .65),
             "market_strong": strength > .65, "xfip_small": xfip <= .25,
             "xfip_medium": (xfip > .25) & (xfip <= .75), "xfip_large": xfip > .75,
             "model_market_agree": agreement, "model_market_disagree": ~agreement,
             "early_season": month <= 5, "mid_season": (month >= 6) & (month <= 7),
             "late_season": month >= 8}
    return masks[name]


def main() -> int:
    frame = enrich(prepare().dropna(subset=["xfip_diff"])).sort_values("date").reset_index(drop=True)
    runs = {config.name: replay(frame, config) for config in CONFIGS}
    selection = []
    for config in CONFIGS:
        run = runs[config.name]
        tune = run[run["year"] == 2024]
        if tune.empty:
            continue
        for weight in BLENDS:
            p = blend(tune["p_market"].to_numpy(float), tune["p_model"].to_numpy(float), weight)
            selection.append((score(tune, p)["brier"], config, weight))
    _, chosen, weight = min(selection, key=lambda item: item[0])
    run = runs[chosen.name]
    result = {"config": chosen.name, "blend_weight": weight, "years": {}}
    for year in (2024, 2025, 2026):
        sub = run[run["year"] == year].reset_index(drop=True)
        p = blend(sub["p_market"].to_numpy(float), sub["p_model"].to_numpy(float), weight)
        result["years"][str(year)] = {"model": score(sub, p),
                                      "market": score(sub, sub["p_market"].to_numpy(float)),
                                      "calibration": calibration(sub, p), "slices": fixed_slices(sub, p)}

    validation = run[run["year"] == 2025].reset_index(drop=True)
    pv = blend(validation["p_market"].to_numpy(float), validation["p_model"].to_numpy(float), weight)
    candidates = [(name, value) for name, value in fixed_slices(validation, pv).items()
                  if value["n"] >= 80]
    chosen_slice, validation_slice = min(candidates, key=lambda item: item[1]["brier_delta"])
    test = run[run["year"] == 2026].reset_index(drop=True)
    pt = blend(test["p_market"].to_numpy(float), test["p_model"].to_numpy(float), weight)
    test_slice = compare_slice(test, slice_mask(test, chosen_slice, pt), pt)
    result["error_driven_gate"] = {"selected_on": 2025, "slice": chosen_slice,
                                   "validation": validation_slice, "test_2026": test_slice,
                                   "promotion": "promote" if test_slice["brier_delta"] < 0
                                   and test_slice["roi_uplift_pp"] > 0 else "reject"}
    report = {"protocol": "monthly prequential replay; each prediction uses only prior months",
              "selection": "2024 chooses config/blend; 2025 chooses one fixed error slice; 2026 final",
              "rows": len(frame), "candidate_configs": len(CONFIGS)*len(BLENDS), "selected": result}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
