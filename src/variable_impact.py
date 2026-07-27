"""종목별로 **어떤 변수가 실제로 중요한가**를 측정한다.

"야구는 선발투수, 농구는 백투백"은 흔한 통념이다. 통념으로 모델을 만들지 않는다.
Elo를 통제한 뒤 각 변수가 승패를 추가로 설명하는지 **종목별로 잰다.**

방법
----
1) 기준 모델:  logit(홈승) = a + b·elo_diff
2) 변수 추가:  logit(홈승) = a + b·elo_diff + c·X
3) 판정: c 의 유의성과, 검증 구간에서 **Brier가 실제로 개선되는가**

⚠️ 계수가 유의해도 Brier가 나아지지 않으면 채택하지 않는다.
   학습 구간 2023–2024, 검증 2025–2026 으로 시간 분리한다.
   (프로젝트 절대 원칙: 백테스트 개선 없이는 채택 없음)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURES, LABELS, build_features   # noqa: E402
from matches import load_matches                        # noqa: E402

TRAIN_END = 2024
SPORTS = {"bs": "야구", "sc": "축구", "bk": "농구", "vl": "배구"}
MIN_N = 800


def _fit(X: np.ndarray, y: np.ndarray, iters: int = 60) -> np.ndarray | None:
    """뉴턴-랩슨 로지스틱. X는 절편 포함."""
    n, k = X.shape
    beta = np.zeros(k)
    for _ in range(iters):
        z = np.clip(X @ beta, -30, 30)
        p = 1 / (1 + np.exp(-z))
        w = np.clip(p * (1 - p), 1e-8, None)
        g = X.T @ (y - p)
        H = (X * w[:, None]).T @ X + np.eye(k) * 1e-6
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            return None
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def _brier(X: np.ndarray, beta: np.ndarray, y: np.ndarray) -> float:
    p = 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30)))
    return float(np.mean((p - y) ** 2))


def _se(X: np.ndarray, beta: np.ndarray) -> np.ndarray:
    z = np.clip(X @ beta, -30, 30)
    p = 1 / (1 + np.exp(-z))
    w = np.clip(p * (1 - p), 1e-8, None)
    H = (X * w[:, None]).T @ X + np.eye(X.shape[1]) * 1e-6
    return np.sqrt(np.diag(np.linalg.inv(H)))


def analyse(df: pd.DataFrame, sport: str) -> None:
    sub = df[(df["sport"] == sport) & (df["outcome"] != 0.5)].copy()
    if len(sub) < MIN_N:
        print(f"\n[{SPORTS.get(sport, sport)}] 표본 {len(sub)}건 — 부족해 생략")
        return

    tr = sub[sub["year"] <= TRAIN_END]
    te = sub[sub["year"] > TRAIN_END]
    if len(tr) < 400 or len(te) < 200:
        print(f"\n[{SPORTS.get(sport, sport)}] 학습/검증 표본 부족")
        return

    # 기준 모델
    def mat(d, cols):
        return np.column_stack([np.ones(len(d))] + [d[c].to_numpy(float) for c in cols])

    y_tr = (tr["outcome"] == 1.0).to_numpy(float)
    y_te = (te["outcome"] == 1.0).to_numpy(float)
    b0 = _fit(mat(tr, ["elo_diff"]), y_tr)
    if b0 is None:
        return
    base = _brier(mat(te, ["elo_diff"]), b0, y_te)

    print(f"\n{'='*76}")
    print(f"[{SPORTS.get(sport, sport)}]  학습 {len(tr):,} / 검증 {len(te):,}  "
          f"· Elo 단독 Brier {base:.5f}")
    print(f"{'='*76}")
    print(f"{'변수':<26}{'n':>8}{'계수':>10}{'z':>8}{'Brier':>10}{'개선':>10}  판정")

    results = []
    for f in FEATURES:
        t2 = tr.dropna(subset=[f, "elo_diff"])
        v2 = te.dropna(subset=[f, "elo_diff"])
        if len(t2) < 400 or len(v2) < 200 or t2[f].std() < 1e-9:
            continue
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        Xt, Xv = mat(t2, ["elo_diff", f]), mat(v2, ["elo_diff", f])
        b = _fit(Xt, yt)
        if b is None:
            continue
        se = _se(Xt, b)
        z = b[2] / se[2] if se[2] > 0 else 0.0
        # 같은 표본에서 기준선도 다시 계산해야 공정한 비교가 된다
        b_ref = _fit(mat(t2, ["elo_diff"]), yt)
        ref = _brier(mat(v2, ["elo_diff"]), b_ref, yv)
        br = _brier(Xv, b, yv)
        gain = ref - br
        ok = (abs(z) >= 2.58) and (gain > 0)
        results.append((f, len(t2), b[2], z, br, gain, ok))

    for f, n, c, z, br, gain, ok in sorted(results, key=lambda x: -x[5]):
        v = "✅ 채택 후보" if ok else ("△ 유의하나 개선 없음" if abs(z) >= 2.58 else "❌")
        print(f"{LABELS.get(f, f):<26}{n:>8,}{c:>10.4f}{z:>8.2f}"
              f"{br:>10.5f}{gain:>+10.5f}  {v}")

    win = [r for r in results if r[6]]
    if win:
        best = max(win, key=lambda x: x[5])
        print(f"\n  → 이 종목의 단일 최대 변수: **{LABELS.get(best[0], best[0])}** "
              f"(Brier {best[5]:+.5f} 개선)")
    else:
        print("\n  → Elo를 넘어서는 변수 없음. 팀 단위 정보는 여기까지다.")


def main() -> int:
    m = load_matches()
    print(f"경기 {len(m):,}건 · 피처 생성 중...")
    df = build_features(m)
    print(f"피처 {len(df):,}행 · 종목 {sorted(df['sport'].dropna().unique())}")
    for sp in ("bs", "sc", "bk", "vl"):
        analyse(df, sp)
    df.to_csv(Path(__file__).resolve().parent.parent
              / "data" / "processed" / "features.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
