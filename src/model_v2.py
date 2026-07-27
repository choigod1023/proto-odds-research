"""종목별 다변량 모델 — variable_impact.py 가 채택한 변수만 넣는다.

채택 기준(사전등록): |z| ≥ 2.58 **AND** 검증 구간 Brier 개선.
통념이 아니라 측정 결과로 정한다. 종목마다 다르게 나왔다.

    야구  Elo 단독      — 어떤 팀 단위 변수도 Elo를 못 넘었다
    축구  Elo + 득실마진 + 최근폼 + 홈원정
    농구  Elo + 득실마진 + 홈팀 백투백
    배구  Elo + 득실마진 + 최근폼

최종 판정은 **시장 배당과의 Brier 비교**다. 시장을 못 이기면 +EV 픽은 나올 수 없다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import build_features                    # noqa: E402
from matches import load_matches                       # noqa: E402
from variable_impact import _brier, _fit               # noqa: E402

TRAIN_END = 2024
SPORTS = {"bs": "야구", "sc": "축구", "bk": "농구", "vl": "배구"}

# variable_impact.py 측정 결과로 확정된 종목별 피처
SPORT_FEATURES: dict[str, list[str]] = {
    "bs": [],
    "sc": ["margin_diff", "form_diff", "venue_diff"],
    "bk": ["margin_diff", "b2b_home"],
    "vl": ["margin_diff", "form_diff"],
}


def design(d: pd.DataFrame, cols: list[str]) -> np.ndarray:
    return np.column_stack([np.ones(len(d))]
                           + [d[c].to_numpy(float) for c in ["elo_diff"] + cols])


def attach_odds(df: pd.DataFrame) -> pd.DataFrame:
    """승패 2-way 배당 결합 (리그·홈·원정·날짜 기준)."""
    from matches import GAMES, _DATE_RE, _away, _home
    raw = pd.read_csv(GAMES)
    raw = raw[(~raw["is_void"].astype(bool)) & (raw["market_family"] == "승패")
              & (raw["n_way"] == 2) & (raw["result"].isin(["홈승", "홈패"]))]
    parts = raw["odds"].str.split(",", expand=True)
    raw = raw.assign(o_home=pd.to_numeric(parts[0], errors="coerce"),
                     o_away=pd.to_numeric(parts[1], errors="coerce"))
    hs, aw = raw["home"].map(_home), raw["away"].map(_away)
    raw = raw.assign(home_team=[t for t, _ in hs], away_team=[t for _, t in aw])
    md = raw["date_text"].astype(str).str.extract(_DATE_RE)
    raw = raw.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                     _dd=pd.to_numeric(md[1], errors="coerce"))
    raw = raw.dropna(subset=["home_team", "away_team", "_mm", "_dd",
                             "o_home", "o_away"])
    raw["date"] = pd.to_datetime(dict(year=raw["year"],
                                      month=raw["_mm"].astype(int),
                                      day=raw["_dd"].astype(int)), errors="coerce")
    key = ["league", "home_team", "away_team", "date"]
    raw = raw.dropna(subset=["date"]).drop_duplicates(key)
    return df.merge(raw[key + ["o_home", "o_away"]], on=key, how="inner")


def main() -> int:
    m = load_matches()
    df = build_features(m)
    df = attach_odds(df)
    df = df[df["outcome"] != 0.5]
    print(f"배당 결합 경기 {len(df):,}건\n")

    print(f"{'종목':<6}{'추가변수':<34}{'검증n':>7}"
          f"{'모델Brier':>11}{'시장Brier':>11}{'차이':>10}  판정")
    print("-" * 92)

    all_rows = []
    for sp, cols in SPORT_FEATURES.items():
        sub = df[df["sport"] == sp].dropna(subset=["elo_diff"] + cols)
        tr = sub[sub["year"] <= TRAIN_END]
        te = sub[sub["year"] > TRAIN_END]
        if len(tr) < 400 or len(te) < 200:
            continue
        y_tr = (tr["outcome"] == 1.0).to_numpy(float)
        y_te = (te["outcome"] == 1.0).to_numpy(float)
        beta = _fit(design(tr, cols), y_tr)
        if beta is None:
            continue
        bm = _brier(design(te, cols), beta, y_te)

        ov = 1 / te["o_home"] + 1 / te["o_away"]
        p_mkt = ((1 / te["o_home"]) / ov).to_numpy(float)
        bk = float(np.mean((p_mkt - y_te) ** 2))

        p_model = 1 / (1 + np.exp(-np.clip(design(te, cols) @ beta, -30, 30)))
        all_rows.append(pd.DataFrame({
            "sport": sp, "p_model": p_model, "p_mkt": p_mkt, "y": y_te,
            "o_home": te["o_home"].to_numpy(), "o_away": te["o_away"].to_numpy()}))

        v = "✅ 모델 우위" if bm < bk else "❌ 시장 우위"
        print(f"{SPORTS[sp]:<6}{('· '.join(cols) or 'Elo 단독'):<34}{len(te):>7,}"
              f"{bm:>11.5f}{bk:>11.5f}{bm-bk:>+10.5f}  {v}")

    if not all_rows:
        return 1
    A = pd.concat(all_rows, ignore_index=True)
    bm = float(np.mean((A["p_model"] - A["y"]) ** 2))
    bk = float(np.mean((A["p_mkt"] - A["y"]) ** 2))
    print("-" * 92)
    print(f"{'전체':<6}{'':<34}{len(A):>7,}{bm:>11.5f}{bk:>11.5f}{bm-bk:>+10.5f}  "
          f"{'✅' if bm < bk else '❌'}")

    # ---- 픽 수익률
    print("\n픽 규칙별 실제 수익률 (검증 구간)")
    print(f"{'EV 임계':>8}{'픽 수':>9}{'적중률':>9}{'ROI':>10}{'95% CI':>24}")
    rng = np.random.default_rng(42)
    ev_h = A["p_model"] * A["o_home"] - 1
    ev_a = (1 - A["p_model"]) * A["o_away"] - 1
    for th in (0.0, 0.03, 0.06, 0.10):
        pick_home = (ev_h > th) & (ev_h >= ev_a)
        pick_away = (ev_a > th) & (ev_a > ev_h)
        sel = pick_home | pick_away
        if sel.sum() < 100:
            continue
        odds = np.where(pick_home[sel], A["o_home"][sel], A["o_away"][sel])
        won = np.where(pick_home[sel], A["y"][sel] == 1, A["y"][sel] == 0)
        prof = np.where(won, odds - 1, -1.0)
        idx = rng.integers(0, len(prof), size=(4000, len(prof)))
        d = prof[idx].mean(axis=1)
        lo, hi = np.quantile(d, [0.025, 0.975])
        print(f"{th:>8.0%}{len(prof):>9,}{won.mean():>9.1%}{prof.mean():>10.2%}"
              f"{f'[{lo:+.2%}, {hi:+.2%}]':>24}")
    print("\n기준선: 2-way 시장에 아무거나 걸면 −12.00%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
