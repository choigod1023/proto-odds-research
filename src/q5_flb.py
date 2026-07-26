"""Q5 — 한국 프로토에 favorite-longshot 편향이 있는가, 방향은 어느 쪽인가.

배경
-----
Snowberg & Wolfers(미국 경마): 사람들이 한 방을 노려 **역배에 과하게 건다**
→ 역배 배당이 짜게 매겨진다 → 역배에 걸수록 손해가 커진다.

한국은 반대일 수 있다는 가설이 있었다. 롯데·기아 같은 **인기구단 쏠림**이 크면
오히려 강팀(인기팀) 쪽이 과대평가되어 역배가 저평가될 수 있기 때문이다.

**가정하지 말고 측정한다.** 방향이 전략을 정한다:
    미국과 같은 방향 → 저평가된 쪽은 **강팀**
    반대                → **역배** 쪽이 기회 (발견되면 그 자체로 연구 성과)
    방향 없음            → 배당대 필터는 쓸모없음

⚠️ ROI는 분산이 매우 크다. 점추정으로는 아무 주장도 할 수 없어
   **게임행 클러스터 부트스트랩 신뢰구간**으로만 판정한다(시드 42).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

BETS = Path(__file__).resolve().parent.parent / "data" / "processed" / "bets.csv"
GAMES = Path(__file__).resolve().parent.parent / "data" / "processed" / "games.csv"

SEED = 42
N_BOOT = 5000

THEORETICAL = {"2-way": 1 / 1.1364 - 1, "3-way": 1 / 1.1494 - 1,
               "3-way-핸디캡": 1 / 1.1629 - 1}

# KBO 전통 인기구단(관중 동원 상위) vs 나머지
POPULAR = {"LG", "KIA", "롯데", "두산", "삼성", "한화"}
KBO_TEAMS = POPULAR | {"KT", "SSG", "NC", "키움"}


def load() -> pd.DataFrame:
    b = pd.read_csv(BETS)
    b["theo"] = b["booking_class"].map(THEORETICAL).fillna(-0.12)
    b["cluster"] = (b["year"].astype(str) + "-" + b["round"].astype(str)
                    + "-" + b["game_no"].astype(str))
    print(f"베팅 레코드 {len(b):,}건")
    return b


def boot_stat(values: np.ndarray, codes: np.ndarray, k: int,
              counts: np.ndarray, n_boot: int = N_BOOT) -> np.ndarray:
    """게임행 클러스터 부트스트랩으로 ROI 분포를 만든다."""
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, k, size=(n_boot, k))
    return values[idx].sum(axis=1) / counts[idx].sum(axis=1)


def cluster_ci(sub: pd.DataFrame, alpha: float = 0.05) -> tuple[float, float]:
    codes, _ = pd.factorize(sub["cluster"])
    k = int(codes.max()) + 1
    if k < 30:
        return (float("nan"), float("nan"))
    sums = np.bincount(codes, weights=sub["profit"].to_numpy(float), minlength=k)
    cnts = np.bincount(codes, minlength=k).astype(float)
    rois = boot_stat(sums, codes, k, cnts)
    return float(np.quantile(rois, alpha / 2)), float(np.quantile(rois, 1 - alpha / 2))


def hdr(t: str) -> None:
    print("\n" + "=" * 84 + f"\n{t}\n" + "=" * 84)


# ---------------------------------------------------------------- 1) 배당대별

def by_bucket(b: pd.DataFrame) -> None:
    hdr("1) 배당 구간별 ROI 와 95% 신뢰구간")
    bins = [1.0, 1.5, 1.8, 2.2, 3.0, 5.0, 999]
    labels = ["1.0–1.5", "1.5–1.8", "1.8–2.2", "2.2–3.0", "3.0–5.0", "5.0+"]
    b = b.copy()
    b["bucket"] = pd.cut(b["odds"], bins=bins, labels=labels, right=False)

    print(f"{'구간':<12}{'n':>9}{'ROI':>10}{'기준선':>10}{'초과':>10}"
          f"{'95% 신뢰구간':>26}")
    for lab in labels:
        sub = b[b["bucket"] == lab]
        if len(sub) < 300:
            continue
        roi, theo = sub["profit"].mean(), sub["theo"].mean()
        lo, hi = cluster_ci(sub)
        print(f"{lab:<12}{len(sub):>9,}{roi:>10.2%}{theo:>10.2%}"
              f"{roi-theo:>+10.2%}{f'[{lo:+.2%}, {hi:+.2%}]':>26}")


# ---------------------------------------------------------------- 2) 회귀

def slope_test(b: pd.DataFrame) -> None:
    hdr("2) 기울기 검정 — ROI 가 log(배당)에 따라 어떻게 변하는가")
    print("  β < 0 : 배당이 높을수록 손해 ↑  → 미국식 FLB (전략 = 강팀)")
    print("  β > 0 : 배당이 높을수록 이득 ↑  → 역FLB (전략 = 역배)")
    print("  CI가 0을 포함 : 방향성 없음\n")

    sub = b.dropna(subset=["odds", "profit"]).copy()
    sub["excess"] = sub["profit"] - sub["theo"]   # 기준선 대비 초과분으로 본다
    x = np.log(sub["odds"].to_numpy(float))
    y = sub["excess"].to_numpy(float)

    codes, _ = pd.factorize(sub["cluster"])
    k = int(codes.max()) + 1
    # 클러스터 단위로 (Σx, Σy, Σxy, Σx², n) 을 모아 부트스트랩
    n_c = np.bincount(codes, minlength=k).astype(float)
    sx = np.bincount(codes, weights=x, minlength=k)
    sy = np.bincount(codes, weights=y, minlength=k)
    sxy = np.bincount(codes, weights=x * y, minlength=k)
    sxx = np.bincount(codes, weights=x * x, minlength=k)

    def slope(sel: np.ndarray) -> np.ndarray:
        N = n_c[sel].sum(axis=-1)
        X, Y = sx[sel].sum(axis=-1), sy[sel].sum(axis=-1)
        XY, XX = sxy[sel].sum(axis=-1), sxx[sel].sum(axis=-1)
        return (N * XY - X * Y) / (N * XX - X * X)

    point = float(slope(np.arange(k)))
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, k, size=(N_BOOT, k))
    dist = slope(idx)
    lo, hi = np.quantile(dist, [0.025, 0.975])

    print(f"  기울기 β = {point:+.4f}   95% CI = [{lo:+.4f}, {hi:+.4f}]   n={len(sub):,}")
    if hi < 0:
        v = "✅ 유의한 음수 → 미국식 FLB 확인. 저평가된 쪽은 강팀"
    elif lo > 0:
        v = "✅ 유의한 양수 → 역FLB(한국 특이). 역배 쪽이 기회"
    else:
        v = "❌ CI가 0을 포함 → 방향성 없음"
    print(f"  판정: {v}")
    print(f"\n  해석: 배당이 e배(2.72배) 오를 때 기준선 대비 수익률이 "
          f"{point*100:+.2f}%p 변한다.")


# ---------------------------------------------------------------- 3) 인기구단

def popularity(b: pd.DataFrame) -> None:
    hdr("3) 인기구단 편향 — KBO 승패 시장")
    print("  가설: 롯데·기아 등 인기구단에 돈이 몰리면 그쪽 배당이 짜져 손해가 커진다.\n")

    g = pd.read_csv(GAMES)
    g = g[(g["league"] == "KBO") & (g["market_family"] == "승패")
          & (~g["is_void"].astype(bool))]

    def team(s: str) -> str:
        return re.sub(r"[\s\d\-]+$", "", str(s)).strip()

    g = g.assign(home_t=g["home"].map(team), away_t=g["away"].map(team))
    g = g[g["home_t"].isin(KBO_TEAMS) & g["away_t"].isin(KBO_TEAMS)]
    key = g.set_index(["year", "round", "game_no"])[["home_t", "away_t"]]

    kb = b[(b["league"] == "KBO") & (b["market_family"] == "승패")].copy()
    kb = kb.join(key, on=["year", "round", "game_no"], how="inner")
    kb["team"] = np.where(kb["selection"] == "홈", kb["home_t"], kb["away_t"])
    kb["popular"] = kb["team"].isin(POPULAR)

    print(f"{'팀':<8}{'n':>8}{'ROI':>10}{'95% 신뢰구간':>26}  인기구단")
    for t, sub in sorted(kb.groupby("team"), key=lambda x: -x[1]["profit"].mean()):
        lo, hi = cluster_ci(sub)
        print(f"{t:<8}{len(sub):>8,}{sub['profit'].mean():>10.2%}"
              f"{f'[{lo:+.2%}, {hi:+.2%}]':>26}  {'●' if t in POPULAR else ''}")

    print()
    for lab, sub in [("인기구단", kb[kb["popular"]]), ("비인기구단", kb[~kb["popular"]])]:
        lo, hi = cluster_ci(sub)
        print(f"  {lab:<10} n={len(sub):>6,}  ROI={sub['profit'].mean():+.2%}  "
              f"95%CI=[{lo:+.2%}, {hi:+.2%}]")

    diff = kb[kb["popular"]]["profit"].mean() - kb[~kb["popular"]]["profit"].mean()
    print(f"\n  차이 = {diff:+.2%}p")
    print("  ⚠️ 두 집단은 실력 분포도 다르므로, 이 차이를 곧바로 '인기 편향'으로 "
          "읽으면 안 된다.\n     배당대를 통제한 비교가 필요하다(아래).")

    kb["bucket"] = pd.cut(kb["odds"], [1.0, 1.5, 1.8, 2.2, 3.0, 999],
                          labels=["1.0–1.5", "1.5–1.8", "1.8–2.2", "2.2–3.0", "3.0+"],
                          right=False)
    print("\n  배당대 통제 후 (같은 배당 구간 안에서 비교):")
    print(f"  {'구간':<12}{'인기 ROI':>12}{'비인기 ROI':>12}{'차이':>10}"
          f"{'인기 n':>9}{'비인기 n':>10}")
    for lab, sub in kb.groupby("bucket", observed=True):
        p, np_ = sub[sub["popular"]], sub[~sub["popular"]]
        if len(p) < 200 or len(np_) < 200:
            continue
        print(f"  {str(lab):<12}{p['profit'].mean():>12.2%}{np_['profit'].mean():>12.2%}"
              f"{p['profit'].mean()-np_['profit'].mean():>+10.2%}"
              f"{len(p):>9,}{len(np_):>10,}")


def main() -> int:
    if not BETS.exists():
        print("먼저 python src/build_dataset.py 를 실행하세요.")
        return 1
    b = load()
    by_bucket(b)
    slope_test(b)
    popularity(b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
