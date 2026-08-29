"""K리그 무료 피처의 독립모델·시장 offset·쏠림 분해 검증.

목적은 정배를 정당화하는 것이 아니다. 같은 경기에서

1. 프로토 마진 제거 확률
2. 배당을 보지 않은 독립 경기모델
3. 프로토를 offset으로 두고 무료 피처를 추가한 모델

을 나란히 두고, 무료 정보가 시장 밖의 정보를 더하는지 시간 분리로 잰다.

사용:
    python3 src/free_context_eval.py
    python3 src/free_context_eval.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_features import build_schedule_context  # noqa: E402
from devig import shin  # noqa: E402
from features import build_features  # noqa: E402
from free_context import assess_crowding, centered_log_ratio, empirical_percentile  # noqa: E402
from matches import GAMES, _DATE_RE, _away, _home, load_matches  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed" / "free_context_eval.json"
TRAIN_END = 2024
FEATURES = [
    "elo_diff", "margin_diff", "form_diff", "rest_diff",
    "travel_diff_km", "games_7d_diff", "road_streak_diff",
]
L2_GRID = [2.0, 8.0, 32.0, 128.0, 512.0, 2048.0]


def attach_market(matches: pd.DataFrame) -> pd.DataFrame:
    raw = pd.read_csv(GAMES)
    raw = raw[(~raw["is_void"].astype(bool))
              & (raw["league"] == "K리그1")
              & (raw["market_family"] == "승무패")
              & (raw["n_way"] == 3)
              & raw["result"].isin(["홈승", "무승부", "홈패"])]
    home = raw["home"].map(_home)
    away = raw["away"].map(_away)
    raw = raw.assign(home_team=[x for x, _ in home], away_team=[x for _, x in away])
    md = raw["date_text"].astype(str).str.extract(_DATE_RE)
    raw = raw.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                     _dd=pd.to_numeric(md[1], errors="coerce"))
    raw["date"] = pd.to_datetime(dict(
        year=raw["year"], month=raw["_mm"], day=raw["_dd"]), errors="coerce")
    raw = raw.dropna(subset=["date", "home_team", "away_team", "odds"])

    probs = []
    keep = []
    for idx, text in raw["odds"].items():
        try:
            odds = [float(x) for x in str(text).split(",")]
            if len(odds) != 3 or any(x <= 1 for x in odds):
                continue
            probs.append(shin(odds))
            keep.append(idx)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    raw = raw.loc[keep].copy()
    raw[["p_market_home", "p_market_draw", "p_market_away"]] = np.asarray(probs)
    key = ["date", "league", "home_team", "away_team"]
    market = raw.drop_duplicates(key)[key + [
        "p_market_home", "p_market_draw", "p_market_away"]]
    return matches.merge(market, on=key, how="inner")


def _softmax(scores: np.ndarray) -> np.ndarray:
    z = scores - scores.max(axis=1, keepdims=True)
    e = np.exp(np.clip(z, -40, 40))
    return e / e.sum(axis=1, keepdims=True)


def fit_softmax(
    X: np.ndarray,
    y: np.ndarray,
    *,
    offset: np.ndarray | None = None,
    l2: float = 1.0,
) -> np.ndarray:
    """L2 다항 로지스틱. offset은 log 시장확률이다."""
    n, d = X.shape
    k = int(y.max()) + 1
    off = np.zeros((n, k)) if offset is None else np.asarray(offset, dtype=float)
    eye = np.eye(k)[y]

    def objective(flat):
        beta = flat.reshape(d, k)
        p = _softmax(off + X @ beta)
        loss = -np.sum(eye * np.log(np.clip(p, 1e-12, 1)))
        loss += 0.5 * l2 * np.sum(beta[1:] ** 2)
        grad = X.T @ (p - eye)
        grad[1:] += l2 * beta[1:]
        return float(loss), grad.ravel()

    result = minimize(objective, np.zeros(d * k), jac=True, method="L-BFGS-B")
    if not result.success:
        raise RuntimeError(f"softmax 적합 실패: {result.message}")
    return result.x.reshape(d, k)


def predict_softmax(X: np.ndarray, beta: np.ndarray, offset=None) -> np.ndarray:
    scores = X @ beta
    if offset is not None:
        scores = scores + np.asarray(offset, dtype=float)
    return _softmax(scores)


def multiclass_brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.sum((p - np.eye(p.shape[1])[y]) ** 2, axis=1)))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1))))


def fit_preprocessor(frame: pd.DataFrame) -> dict:
    """학습 분포 1~99% 밖을 고정해 새 시즌 극단값의 확률 폭주를 막는다."""
    med = frame[FEATURES].median(numeric_only=True).fillna(0)
    lower = frame[FEATURES].quantile(0.01).fillna(med)
    upper = frame[FEATURES].quantile(0.99).fillna(med)
    clipped = frame[FEATURES].fillna(med).clip(lower, upper, axis=1)
    scale = clipped.std().replace(0, 1).fillna(1)
    return {"median": med, "lower": lower, "upper": upper, "scale": scale}


def transform(frame: pd.DataFrame, stats: dict) -> np.ndarray:
    z = frame[FEATURES].fillna(stats["median"])
    z = z.clip(stats["lower"], stats["upper"], axis=1)
    z = (z - stats["median"]) / stats["scale"]
    return np.column_stack([np.ones(len(frame)), z.to_numpy(float)])


def select_l2(train: pd.DataFrame, *, use_market_offset: bool) -> tuple[float, np.ndarray, np.ndarray]:
    """2023→2024 내부 시간검증으로 정규화 강도를 고른다.

    반환하는 예측은 선택된 강도의 2024 out-of-sample 값이며 쏠림 불확실성 추정에
    사용한다. 테스트 연도(2025+)는 선택 과정에 전혀 들어오지 않는다.
    """
    inner_train = train[train["year"] < TRAIN_END]
    inner_valid = train[train["year"] == TRAIN_END]
    if len(inner_train) < 150 or len(inner_valid) < 100:
        raise ValueError("내부 시간검증 표본 부족")
    stats = fit_preprocessor(inner_train)
    Xtr, Xva = transform(inner_train, stats), transform(inner_valid, stats)
    ytr, yva = inner_train["target"].to_numpy(int), inner_valid["target"].to_numpy(int)
    cols = ["p_market_home", "p_market_draw", "p_market_away"]
    qtr, qva = inner_train[cols].to_numpy(float), inner_valid[cols].to_numpy(float)
    otr = np.log(np.clip(qtr, 1e-9, 1)) if use_market_offset else None
    ova = np.log(np.clip(qva, 1e-9, 1)) if use_market_offset else None
    best = None
    for l2 in L2_GRID:
        beta = fit_softmax(Xtr, ytr, offset=otr, l2=l2)
        pred = predict_softmax(Xva, beta, offset=ova)
        score = log_loss(pred, yva)
        if best is None or score < best[0]:
            best = (score, l2, pred)
    assert best is not None
    return float(best[1]), best[2], qva


def date_block_ci(df: pd.DataFrame, diff: np.ndarray, n_boot: int = 2000) -> list[float]:
    """같은 날짜 경기를 묶어 재표집한다."""
    tmp = pd.DataFrame({"date": pd.to_datetime(df["date"]).dt.date, "diff": diff})
    blocks = [g["diff"].to_numpy() for _, g in tmp.groupby("date")]
    rng = np.random.default_rng(42)
    means = []
    for _ in range(n_boot):
        picked = rng.integers(0, len(blocks), len(blocks))
        means.append(float(np.concatenate([blocks[i] for i in picked]).mean()))
    return [float(x) for x in np.quantile(means, [0.025, 0.975])]


def prepare() -> pd.DataFrame:
    matches = load_matches(("sc",))
    matches = matches[matches["league"] == "K리그1"].copy()
    base = build_features(matches)
    schedule = build_schedule_context(matches)
    key = ["date", "league", "home_team", "away_team"]
    schedule_cols = key + ["rest_diff", "travel_diff_km", "games_7d_diff", "road_streak_diff"]
    # 기존 features.rest_diff는 시즌 경계를 이어 300일을 넘는 차이를 만들 수 있다.
    # 일정 컨텍스트의 30일 리셋판을 정본으로 사용한다.
    frame = base.drop(columns=["rest_diff"]).merge(schedule[schedule_cols], on=key, how="left")
    frame = attach_market(frame)
    frame["target"] = np.where(frame["outcome"] == 1, 0,
                               np.where(frame["outcome"] == 0.5, 1, 2))
    return frame.sort_values("date").reset_index(drop=True)


def evaluate(frame: pd.DataFrame) -> dict:
    train = frame[frame["year"] <= TRAIN_END].copy()
    test = frame[frame["year"] > TRAIN_END].copy()
    if len(train) < 200 or len(test) < 100:
        raise ValueError(f"학습/검증 표본 부족: {len(train)}/{len(test)}")

    market_cols = ["p_market_home", "p_market_draw", "p_market_away"]
    l2_fundamental, inner_pf, inner_q = select_l2(train, use_market_offset=False)
    l2_offset, _, _ = select_l2(train, use_market_offset=True)
    stats = fit_preprocessor(train)
    Xtr, Xte = transform(train, stats), transform(test, stats)
    ytr, yte = train["target"].to_numpy(int), test["target"].to_numpy(int)
    qtr = train[market_cols].to_numpy(float)
    qte = test[market_cols].to_numpy(float)
    log_qtr, log_qte = np.log(np.clip(qtr, 1e-9, 1)), np.log(np.clip(qte, 1e-9, 1))

    fundamental_beta = fit_softmax(Xtr, ytr, l2=l2_fundamental)
    offset_beta = fit_softmax(Xtr, ytr, offset=log_qtr, l2=l2_offset)
    pf = predict_softmax(Xte, fundamental_beta)
    pc = predict_softmax(Xte, offset_beta, offset=log_qte)

    market_loss = multiclass_brier(qte, yte)
    independent_loss = multiclass_brier(pf, yte)
    combined_loss = multiclass_brier(pc, yte)
    per_market = np.sum((qte - np.eye(3)[yte]) ** 2, axis=1)
    per_combined = np.sum((pc - np.eye(3)[yte]) ** 2, axis=1)
    gain = market_loss - combined_loss

    # 임의의 8%p 문턱 대신 2024년에서 관측한 시장-독립모델 괴리의 robust scale을 쓴다.
    inner_gap = []
    for q, p in zip(inner_q, inner_pf):
        fav = int(np.argmax(q))
        inner_gap.append(centered_log_ratio(q, fav) - centered_log_ratio(p, fav))
    inner_gap = np.asarray(inner_gap)
    med_gap = float(np.median(inner_gap))
    uncertainty_clr = max(0.05, 1.4826 * float(np.median(np.abs(inner_gap - med_gap))))

    train_clr = []
    for q in qtr:
        fav = int(np.argmax(q))
        train_clr.append(centered_log_ratio(q, fav))

    assessments = []
    for row, q, p in zip(test.itertuples(), qte, pf):
        fav = int(np.argmax(q))
        percentile = empirical_percentile(train_clr, centered_log_ratio(q, fav))
        a = assess_crowding(
            q, p, market_percentile=percentile,
            incremental_gain=gain, uncertainty_clr=uncertainty_clr,
        ).to_dict()
        a.update({
            "date": str(pd.Timestamp(row.date).date()),
            "home": row.home_team, "away": row.away_team,
        })
        assessments.append(a)

    counts = pd.Series([x["label"] for x in assessments]).value_counts().to_dict()
    top = sorted(
        [x for x in assessments if x["label"] == "설명되지 않는 쏠림"],
        key=lambda x: x["unexplained_z"], reverse=True,
    )[:20]
    return {
        "definition": {
            "league": "K리그1", "train": "2023-2024", "test": "2025-2026",
            "devig": "Shin", "features": FEATURES,
            "l2_fundamental": l2_fundamental, "l2_market_offset": l2_offset,
            "unexplained_scale_clr": uncertainty_clr,
            "lineup_note": "workload는 별도 산출; 현재 모델에는 시점 보존 표본 부족으로 미투입",
            "weather_note": "예보 스냅샷 축적 후 같은 관문으로 추가",
        },
        "sample": {"train": len(train), "test": len(test)},
        "metrics": {
            "brier_market": market_loss,
            "brier_independent": independent_loss,
            "brier_market_plus_free": combined_loss,
            "market_plus_free_gain": gain,
            "gain_ci95_date_block": date_block_ci(test, per_market - per_combined),
            "logloss_market": log_loss(qte, yte),
            "logloss_independent": log_loss(pf, yte),
            "logloss_market_plus_free": log_loss(pc, yte),
        },
        "classification_counts": counts,
        "top_unexplained": top,
    }


def _selftest() -> int:
    rng = np.random.default_rng(42)
    X = np.column_stack([np.ones(600), rng.normal(size=(600, 2))])
    true = np.array([[0.1, 0.0, -0.1], [1.0, 0.0, -1.0], [-0.5, 0.2, 0.3]])
    p = _softmax(X @ true)
    y = np.array([rng.choice(3, p=row) for row in p])
    beta = fit_softmax(X[:400], y[:400])
    pred = predict_softmax(X[400:], beta)
    assert pred.shape == (200, 3)
    assert np.allclose(pred.sum(axis=1), 1)
    assert multiclass_brier(pred, y[400:]) < 0.75
    print("✅ 무료 쏠림 평가 자기검사 통과 (softmax·확률합·Brier)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    frame = prepare()
    result = evaluate(frame)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    m = result["metrics"]
    print(f"K리그1 학습 {result['sample']['train']:,} / 검증 {result['sample']['test']:,}")
    print(f"시장 Brier              {m['brier_market']:.5f}")
    print(f"독립 무료 경기모델       {m['brier_independent']:.5f}")
    print(f"시장 + 무료 피처 offset  {m['brier_market_plus_free']:.5f}")
    print(f"시장 대비 개선           {m['market_plus_free_gain']:+.5f} "
          f"CI {m['gain_ci95_date_block']}")
    print(f"분류: {result['classification_counts']}")
    print(f"저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
