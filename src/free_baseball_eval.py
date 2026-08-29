"""KBO·NPB·MLB 무료 컨텍스트의 시장 추가가치와 쏠림을 검증한다.

리그별로 다음 세 값을 분리한다.

1. 2-way 프로토 배당을 정규화한 시장 확률
2. 배당을 보지 않은 독립 경기모델
3. 시장 log-확률을 offset으로 두고 무료 피처만 추가한 모델

선발 이름은 과거 경기의 실제 선발을 사후 복원한 것이다. 그 선발의 현재 경기
결과는 쓰지 않고 직전 등판만 쓰지만, 당시 발표시각이 없으므로 예측 신호가 아니라
효과의 상한·설명용이다. 신규 경기는 info_watch.py의 observed_at이 있는 행만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_features import build_schedule_context  # noqa: E402
from features import build_features  # noqa: E402
from free_context import assess_crowding, centered_log_ratio, empirical_percentile  # noqa: E402
from free_context_eval import fit_softmax, log_loss, predict_softmax  # noqa: E402
from matches import load_matches  # noqa: E402
from model_v2 import attach_odds  # noqa: E402
from pitcher_impact import build_pitcher_features, load_starters  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed" / "free_baseball_eval.json"
TEAM_MAP = ROOT / "data" / "processed" / "team_map.json"
TRAIN_END = 2024
LEAGUES = ("KBO", "NPB", "MLB")
L2_GRID = [2.0, 8.0, 32.0, 128.0, 512.0, 2048.0]

TEAM_FEATURES = ["elo_diff", "margin_diff", "form_diff"]
SCHEDULE_FEATURES = [
    "rest_diff", "travel_diff_km", "games_3d_diff", "games_7d_diff",
    "road_streak_diff",
]
STARTER_FEATURES = ["starter_ra_diff", "starter_wr_diff", "starter_history_available"]
KBO_DETAIL_FEATURES = [
    "xfip_diff", "ip_diff", "pen_fip_diff", "pen_load_diff",
    "kbo_pitch_detail_available",
]


def _naver_team(league: str, team: str, date, team_map: dict) -> str:
    # 네이버가 2025년부터 구단명을 오클랜드 → 애슬레틱스로 바꿨다.
    if league == "MLB" and team == "애슬레틱":
        return "오클랜드" if pd.Timestamp(date).year <= 2024 else "애슬레틱스"
    return team_map.get(league, {}).get(team, team)


def attach_starter_context(frame: pd.DataFrame, league: str, team_map: dict) -> pd.DataFrame:
    pf = build_pitcher_features(load_starters(league)).rename(columns={
        "home_team": "_naver_home", "away_team": "_naver_away",
    })
    out = frame.copy()
    out["_naver_home"] = [
        _naver_team(league, t, d, team_map) for t, d in zip(out["home_team"], out["date"])
    ]
    out["_naver_away"] = [
        _naver_team(league, t, d, team_map) for t, d in zip(out["away_team"], out["date"])
    ]
    cols = [
        "date", "_naver_home", "_naver_away", "home_starter", "away_starter",
        "starter_ra_diff", "starter_wr_diff", "p_n_min",
    ]
    out = out.merge(pf[cols], on=["date", "_naver_home", "_naver_away"], how="left")
    out["starter_history_available"] = out[["starter_ra_diff", "starter_wr_diff"]].notna().all(axis=1).astype(float)
    return out


def attach_kbo_pitch_detail(frame: pd.DataFrame) -> pd.DataFrame:
    """KBO 무료 박스스코어의 선발 xFIP·이닝과 불펜 상태를 경기 전 값으로 결합."""
    from pitcher_er import _inn
    from pitcher_xfip import build, load_full

    raw = load_full()
    train = raw[raw["date"] < f"{TRAIN_END + 1}-01-01"]
    total = {k: 0.0 for k in ("ip", "er", "hr", "bb", "kk")}
    for r in train.itertuples():
        for p in (r.home_sp, r.away_sp):
            total["ip"] += _inn(p.get("inn"))
            for key in ("er", "hr", "bb", "kk"):
                total[key] += float(p.get(key) or 0)
    fip_c = total["er"] / total["ip"] * 9 - (
        13 * total["hr"] + 3 * total["bb"] - 2 * total["kk"]
    ) / total["ip"]
    league_hr9 = total["hr"] / total["ip"] * 9
    detail = build(raw, fip_c, league_hr9)
    cols = ["date", "home_team", "away_team"] + KBO_DETAIL_FEATURES[:-1]
    out = frame.merge(detail[cols], on=["date", "home_team", "away_team"], how="left")
    out["kbo_pitch_detail_available"] = out[KBO_DETAIL_FEATURES[:-1]].notna().all(axis=1).astype(float)
    return out


def prepare_league(league: str) -> tuple[pd.DataFrame, dict]:
    matches = load_matches(("bs",))
    matches = matches[(matches["league"] == league) & (matches["outcome"] != 0.5)].copy()
    base = build_features(matches)
    schedule = build_schedule_context(matches)
    key = ["date", "league", "home_team", "away_team"]
    schedule_cols = key + SCHEDULE_FEATURES + ["travel_quality", "venue_id", "venue_roof"]
    frame = base.drop(columns=["rest_diff"]).merge(schedule[schedule_cols], on=key, how="left")
    frame = attach_odds(frame)
    overround = 1 / frame["o_home"] + 1 / frame["o_away"]
    frame["p_market_home"] = (1 / frame["o_home"]) / overround
    frame["p_market_away"] = 1 - frame["p_market_home"]
    frame["target"] = np.where(frame["outcome"] == 1.0, 0, 1)
    team_map = json.loads(TEAM_MAP.read_text(encoding="utf-8"))
    frame = attach_starter_context(frame, league, team_map)
    if league == "KBO":
        frame = attach_kbo_pitch_detail(frame)
    frame = frame.sort_values("date").reset_index(drop=True)
    coverage = {
        "market_matches": int(len(frame)),
        "venue": float((frame["travel_quality"] != "missing_venue").mean()),
        "starter_join": float(frame["home_starter"].notna().mean()),
        "starter_history": float(frame["starter_history_available"].mean()),
    }
    if league == "KBO":
        coverage["kbo_pitch_detail"] = float(frame["kbo_pitch_detail_available"].mean())
    return frame, coverage


def fit_preprocessor(frame: pd.DataFrame, features: list[str]) -> dict:
    med = frame[features].median(numeric_only=True).fillna(0)
    lower = frame[features].quantile(0.01).fillna(med)
    upper = frame[features].quantile(0.99).fillna(med)
    clipped = frame[features].fillna(med).clip(lower, upper, axis=1)
    scale = clipped.std().replace(0, 1).fillna(1)
    return {"median": med, "lower": lower, "upper": upper, "scale": scale}


def transform(frame: pd.DataFrame, features: list[str], stats: dict) -> np.ndarray:
    z = frame[features].fillna(stats["median"])
    z = z.clip(stats["lower"], stats["upper"], axis=1)
    z = (z - stats["median"]) / stats["scale"]
    return np.column_stack([np.ones(len(frame)), z.to_numpy(float)])


def select_l2(
    train: pd.DataFrame,
    features: list[str],
    *,
    use_market_offset: bool,
) -> tuple[float, np.ndarray, np.ndarray]:
    inner_train = train[train["year"] < TRAIN_END]
    inner_valid = train[train["year"] == TRAIN_END]
    if len(inner_train) < 300 or len(inner_valid) < 200:
        raise ValueError(f"내부 시간검증 표본 부족: {len(inner_train)}/{len(inner_valid)}")
    stats = fit_preprocessor(inner_train, features)
    xtr = transform(inner_train, features, stats)
    xva = transform(inner_valid, features, stats)
    ytr = inner_train["target"].to_numpy(int)
    yva = inner_valid["target"].to_numpy(int)
    cols = ["p_market_home", "p_market_away"]
    qtr = inner_train[cols].to_numpy(float)
    qva = inner_valid[cols].to_numpy(float)
    otr = np.log(np.clip(qtr, 1e-9, 1)) if use_market_offset else None
    ova = np.log(np.clip(qva, 1e-9, 1)) if use_market_offset else None
    best = None
    for l2 in L2_GRID:
        beta = fit_softmax(xtr, ytr, offset=otr, l2=l2)
        pred = predict_softmax(xva, beta, offset=ova)
        score = log_loss(pred, yva)
        if best is None or score < best[0]:
            best = (score, l2, pred)
    assert best is not None
    return float(best[1]), best[2], qva


def _binary_brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p[:, 0] - (y == 0)) ** 2))


def date_block_ci(frame: pd.DataFrame, diff: np.ndarray, n_boot: int = 2000) -> list[float]:
    tmp = pd.DataFrame({"date": pd.to_datetime(frame["date"]).dt.date, "diff": diff})
    blocks = [g["diff"].to_numpy() for _, g in tmp.groupby("date")]
    rng = np.random.default_rng(42)
    means = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(blocks), len(blocks))
        means.append(float(np.concatenate([blocks[i] for i in pick]).mean()))
    return [float(x) for x in np.quantile(means, [0.025, 0.975])]


def evaluate_bundle(frame: pd.DataFrame, features: list[str]) -> tuple[dict, dict]:
    train = frame[frame["year"] <= TRAIN_END]
    test = frame[frame["year"] > TRAIN_END]
    cols = ["p_market_home", "p_market_away"]
    l2_f, inner_pf, inner_q = select_l2(train, features, use_market_offset=False)
    l2_o, _, _ = select_l2(train, features, use_market_offset=True)
    stats = fit_preprocessor(train, features)
    xtr, xte = transform(train, features, stats), transform(test, features, stats)
    ytr, yte = train["target"].to_numpy(int), test["target"].to_numpy(int)
    qtr, qte = train[cols].to_numpy(float), test[cols].to_numpy(float)
    log_qtr, log_qte = np.log(np.clip(qtr, 1e-9, 1)), np.log(np.clip(qte, 1e-9, 1))
    bf = fit_softmax(xtr, ytr, l2=l2_f)
    bo = fit_softmax(xtr, ytr, offset=log_qtr, l2=l2_o)
    pf = predict_softmax(xte, bf)
    po = predict_softmax(xte, bo, offset=log_qte)
    market = _binary_brier(qte, yte)
    independent = _binary_brier(pf, yte)
    combined = _binary_brier(po, yte)
    yhome = (yte == 0).astype(float)
    per_market = (qte[:, 0] - yhome) ** 2
    per_combined = (po[:, 0] - yhome) ** 2
    result = {
        "features": features,
        "l2_fundamental": l2_f,
        "l2_market_offset": l2_o,
        "brier_market": market,
        "brier_independent": independent,
        "brier_market_plus_free": combined,
        "market_plus_free_gain": market - combined,
        "gain_ci95_date_block": date_block_ci(test, per_market - per_combined),
        "logloss_market": log_loss(qte, yte),
        "logloss_independent": log_loss(pf, yte),
        "logloss_market_plus_free": log_loss(po, yte),
        "coefficients_fundamental_home_logodds": {
            name: float(value) for name, value in zip(
                ["intercept"] + features, bf[:, 0] - bf[:, 1]
            )
        },
        "coefficients_market_offset_home_logodds": {
            name: float(value) for name, value in zip(
                ["intercept"] + features, bo[:, 0] - bo[:, 1]
            )
        },
    }
    aux = {
        "train": train, "test": test, "qtr": qtr, "qte": qte, "pf": pf,
        "inner_pf": inner_pf, "inner_q": inner_q,
        "xte": xte, "fundamental_beta": bf, "offset_beta": bo, "features": features,
    }
    return result, aux


def _drivers(xrow: np.ndarray, beta: np.ndarray, features: list[str]) -> list[dict]:
    contributions = xrow[1:] * (beta[1:, 0] - beta[1:, 1])
    order = np.argsort(np.abs(contributions))[::-1][:3]
    return [
        {
            "feature": features[int(i)],
            "home_logodds_contribution": float(contributions[i]),
            "direction": "home" if contributions[i] >= 0 else "away",
        }
        for i in order
    ]


def classify(bundle: dict, aux: dict) -> dict:
    gaps = []
    for q, p in zip(aux["inner_q"], aux["inner_pf"]):
        fav = int(np.argmax(q))
        gaps.append(centered_log_ratio(q, fav) - centered_log_ratio(p, fav))
    gaps = np.asarray(gaps)
    med = float(np.median(gaps))
    scale = max(0.05, 1.4826 * float(np.median(np.abs(gaps - med))))
    train_clr = [centered_log_ratio(q, int(np.argmax(q))) for q in aux["qtr"]]
    assessments = []
    for idx, (row, q, p) in enumerate(zip(
        aux["test"].itertuples(), aux["qte"], aux["pf"]
    )):
        fav = int(np.argmax(q))
        percentile = empirical_percentile(train_clr, centered_log_ratio(q, fav))
        item = assess_crowding(
            q, p, market_percentile=percentile, uncertainty_clr=scale,
            incremental_gain=bundle["market_plus_free_gain"],
        ).to_dict()
        item.update({
            "date": str(pd.Timestamp(row.date).date()),
            "home": row.home_team, "away": row.away_team,
            "top_fundamental_drivers": _drivers(
                aux["xte"][idx], aux["fundamental_beta"], aux["features"]
            ),
            "top_market_residual_drivers": _drivers(
                aux["xte"][idx], aux["offset_beta"], aux["features"]
            ),
        })
        assessments.append(item)
    counts = pd.Series([x["label"] for x in assessments]).value_counts().to_dict()
    return {
        "unexplained_scale_clr": scale,
        "counts": {str(k): int(v) for k, v in counts.items()},
        "top_unexplained": sorted(
            [x for x in assessments if x["label"] == "설명되지 않는 쏠림"],
            key=lambda x: x["unexplained_z"], reverse=True,
        )[:20],
    }


def evaluate_all() -> dict:
    out = {
        "definition": {
            "train": "2023-2024", "test": "2025-2026", "devig": "multiplicative 2-way",
            "starter_timing": "postgame reconstruction; explanatory only until observed_at sample",
            "weather_timing": "forecast snapshots accumulate prospectively; not in retrospective model",
        },
        "leagues": {},
    }
    for league in LEAGUES:
        frame, coverage = prepare_league(league)
        bundles = {
            "schedule": TEAM_FEATURES + SCHEDULE_FEATURES,
            "schedule_plus_starter": TEAM_FEATURES + SCHEDULE_FEATURES + STARTER_FEATURES,
        }
        if league == "KBO":
            bundles["schedule_plus_starter_plus_pitch_detail"] = (
                TEAM_FEATURES + SCHEDULE_FEATURES + STARTER_FEATURES + KBO_DETAIL_FEATURES
            )
        results, last_aux = {}, None
        for name, features in bundles.items():
            results[name], last_aux = evaluate_bundle(frame, features)
        assert last_aux is not None
        final_name = list(bundles)[-1]
        out["leagues"][league] = {
            "sample": {
                "train": int((frame["year"] <= TRAIN_END).sum()),
                "test": int((frame["year"] > TRAIN_END).sum()),
            },
            "coverage": coverage,
            "bundles": results,
            "classification_bundle": final_name,
            "classification": classify(results[final_name], last_aux),
        }
    return out


def _selftest() -> int:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2023-01-01", periods=900, freq="D")
    x = rng.normal(size=900)
    frame = pd.DataFrame({
        "date": dates, "year": dates.year, "x": x,
        "p_market_home": np.clip(0.5 + 0.05 * x, 0.1, 0.9),
    })
    frame["p_market_away"] = 1 - frame["p_market_home"]
    frame["target"] = (rng.random(900) > frame["p_market_home"]).astype(int)
    # 2023/2024 내부 분리에 필요한 최소 표본을 맞추기 위해 가상 연도를 재배치한다.
    frame.loc[:349, "year"] = 2023
    frame.loc[350:699, "year"] = 2024
    frame.loc[700:, "year"] = 2025
    result, _ = evaluate_bundle(frame, ["x"])
    assert 0 < result["brier_market"] < 1
    assert len(result["gain_ci95_date_block"]) == 2
    print("✅ 무료 야구 컨텍스트 자기검사 통과 (2-way offset·시간분리·Brier)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    result = evaluate_all()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for league, item in result["leagues"].items():
        print(f"\n[{league}] 학습 {item['sample']['train']:,} / 검증 {item['sample']['test']:,}")
        for name, metrics in item["bundles"].items():
            print(f"  {name:<43} 시장 {metrics['brier_market']:.5f} → "
                  f"offset {metrics['brier_market_plus_free']:.5f} "
                  f"개선 {metrics['market_plus_free_gain']:+.5f} "
                  f"CI {metrics['gain_ci95_date_block']}")
        print(f"  분류 {item['classification']['counts']}")
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
