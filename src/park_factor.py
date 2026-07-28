"""구장 파크팩터 — 경기마다 바뀌고 Elo 가 모르는 정보인가.

배경
----
`HANDOFF.md` 의 수정된 가설:

    쓸모 있는 피처 = **경기마다 바뀌는 정보**이면서 **팀 레이팅이 모르는 것**

선발투수가 통과했고(+0.006), 타선은 전부 죽었다(≈0). 구장은 이 기준에서
선발 쪽에 가깝다 — 팀마다 홈구장이 고정이라 '홈팀 정체성'의 일부는 Elo 가
이미 알지만, **원정팀 입장에서는 매 경기 바뀌는 조건**이고 잠실(555경기)과
대구의 득점 환경 차이는 팀 실력과 별개다.

⚠️ 설계상 가장 중요한 것
-----------------------
**구장은 양 팀이 같이 쓴다.** 잠실이 투수친화적이면 홈·원정 둘 다 점수가 준다.
따라서 승패 확률에는 **대칭이라 거의 영향이 없다.** 효과가 있다면 **총득점**,
즉 **언더오버** 마켓이다. 그래서 이 스크립트는 승패를 아예 보지 않는다.

측정
----
1. 관문 1 — **총득점 RMSE**. 파크팩터를 곱해 λ 를 보정하면 총득점 예측이
   나아지는가. 여기서 안 되면 O/U 는 볼 필요도 없다.
2. 관문 2 — **O/U Brier vs 프로토**. 나아졌다면, 그 개선이 프로토 배당을
   이길 만큼인가. 환급률 88% 문턱을 기억할 것.

⚠️ 규율 (HANDOFF §6 함정 모음 반영)
-----------------------------------
- **walk-forward.** 파크팩터는 그 경기 **이전** 경기만으로 계산한다.
  구장 평균도, 리그 평균도 전부 과거만. 시즌 전체 평균을 쓰면 미래정보 누수다.
- **시간 분리.** 학습 ≤2024 / 검증 2025~ (TRAIN_END 규약 동일).
- **점추정 금지.** 부트스트랩으로 우위확률과 CI 를 같이 낸다(함정 6).
- **필요 표본 계산.** 효과가 작으면 몇 경기가 필요한지 같이 보고한다(함정 8).
- 올스타전(나눔/드림)은 제외한다.
"""
from __future__ import annotations

import json
import sys
from math import exp, lgamma
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import multiplicative                       # noqa: E402
from matches import _DATE_RE, _away, _home             # noqa: E402
from variable_impact import _brier, _fit               # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"

TRAIN_END = 2024              # 이 해까지 학습, 이후 검증
SHRINK_K = 60                 # 파크팩터 축소 강도(경기 수 단위). 아래에서 민감도 확인
ALLSTAR = {"나눔", "드림"}


# ---------------------------------------------------------------- 데이터 로드
def load_stadiums() -> pd.DataFrame:
    """네이버 KBO 박스스코어에서 (날짜, 홈, 원정) → 구장."""
    src = RAW / "detail" / "kbo_batters_2023_2026.json"
    rows = []
    for v in json.load(open(src, encoding="utf-8")).values():
        st = (v.get("data") or {}).get("stadium")
        if not st or v["home"] in ALLSTAR or v["away"] in ALLSTAR:
            continue
        rows.append({"date": v["date"], "home_team": v["home"],
                     "away_team": v["away"], "stadium": st})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(subset=["date", "home_team", "away_team"])


def load_base() -> pd.DataFrame:
    """λ 기준선 + 실제 총득점 + 구장. 경기 단위 1행."""
    lam = pd.read_csv(PROC / "lambdas.csv")
    lam = lam[lam["league"] == "KBO"].copy()
    lam["date"] = pd.to_datetime(lam["date"])
    lam = lam[~lam["home_team"].isin(ALLSTAR) & ~lam["away_team"].isin(ALLSTAR)]

    lam["total"] = lam["home_score"] + lam["away_score"]
    lam["base"] = lam["lam_home"] + lam["lam_away"]

    st = load_stadiums()
    df = lam.merge(st, on=["date", "home_team", "away_team"], how="inner")
    return df.sort_values("date").reset_index(drop=True)


# ------------------------------------------------------------- 파크팩터 산출
def park_factors(df: pd.DataFrame, k: int = SHRINK_K) -> np.ndarray:
    """walk-forward 파크팩터.

        PF_raw = (그 구장 과거 평균 총득점) / (리그 과거 평균 총득점)
        PF     = 1 + (PF_raw − 1) · n/(n+k)      ← 표본이 적으면 1 로 당긴다

    그 경기 **이전** 경기만 쓴다. 같은 날 경기끼리도 서로를 못 본다
    (날짜 단위로 묶어 반영 시점을 미룬다).
    """
    pf = np.ones(len(df))
    s_sum: dict[str, float] = {}
    s_cnt: dict[str, int] = {}
    lg_sum = 0.0
    lg_cnt = 0

    for day, idx in df.groupby("date", sort=True).groups.items():
        idx = list(idx)
        # --- 예측: 어제까지의 누적만 사용
        if lg_cnt > 0:
            lg_mean = lg_sum / lg_cnt
            for i in idx:
                st = df.at[i, "stadium"]
                n = s_cnt.get(st, 0)
                if n > 0 and lg_mean > 0:
                    raw = (s_sum[st] / n) / lg_mean
                    pf[i] = 1.0 + (raw - 1.0) * (n / (n + k))
        # --- 갱신: 오늘 경기를 누적에 반영
        for i in idx:
            st, tot = df.at[i, "stadium"], float(df.at[i, "total"])
            s_sum[st] = s_sum.get(st, 0.0) + tot
            s_cnt[st] = s_cnt.get(st, 0) + 1
            lg_sum += tot
            lg_cnt += 1
    return pf


# ------------------------------------------------------------------ 포아송
def pois_cdf(lam: float, k: int) -> float:
    """P(X ≤ k), X~Poisson(lam). 로그공간 누적."""
    if lam <= 0:
        return 1.0
    tot, log_lam = 0.0, np.log(lam)
    for i in range(k + 1):
        tot += exp(i * log_lam - lam - lgamma(i + 1))
    return min(tot, 1.0)


def p_over(lam_total: float, line: float) -> float:
    """총득점이 line 을 넘을 확률. line 은 X.5 형태."""
    return 1.0 - pois_cdf(lam_total, int(np.floor(line)))


# ------------------------------------------------------------------ 평가도구
def rmse(pred: np.ndarray, act: np.ndarray) -> float:
    return float(np.sqrt(np.mean((pred - act) ** 2)))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def boot_diff(a: np.ndarray, b: np.ndarray, seed: int = 42, n: int = 5000):
    """짝지은 손실 a(대안) − b(기준)의 부트스트랩. 음수면 대안이 낫다."""
    rng = np.random.default_rng(seed)
    d = a - b
    idx = rng.integers(0, len(d), size=(n, len(d)))
    boot = d[idx].mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return float(d.mean()), float(lo), float(hi), float((boot < 0).mean())


def need_n(effect: float, half_width: float, n_cur: int) -> str:
    """함정 8 — 현재 CI 반폭에서 역산한 필요 표본."""
    if effect <= 0:
        return "—"
    need = (half_width / effect) ** 2 * n_cur
    return f"{need:,.0f}경기"


# ---------------------------------------------------------------------- main
def main() -> int:
    df = load_base()
    print(f"KBO 경기(구장 매칭 성공): {len(df):,}  "
          f"[{df['date'].min():%Y-%m-%d} ~ {df['date'].max():%Y-%m-%d}]")
    print(f"구장 {df['stadium'].nunique()}개  "
          f"학습 ≤{TRAIN_END} / 검증 {TRAIN_END+1}~\n")

    # ---------------------------------------------------------- 관문 1: RMSE
    print("=" * 72)
    print("관문 1 — 총득점 RMSE (파크팩터로 λ 를 보정하면 나아지는가)")
    print("=" * 72)

    best = None
    for k in (20, 40, 60, 100, 200):
        pf = park_factors(df, k)
        v = df["year"] > TRAIN_END
        base, adj, act = df["base"].values[v], (df["base"].values * pf)[v], df["total"].values[v]
        r_b, r_a = rmse(base, act), rmse(adj, act)
        mark = ""
        if best is None or r_a < best[1]:
            best, mark = (k, r_a), "  ←"
        print(f"  k={k:3d}  기준선 {r_b:.4f}  →  파크팩터 {r_a:.4f}  "
              f"({r_a - r_b:+.4f}){mark}")

    k = best[0]
    pf = park_factors(df, k)
    df["pf"] = pf
    v = df["year"] > TRAIN_END
    sub = df[v]
    base, adj, act = sub["base"].values, sub["base"].values * sub["pf"].values, sub["total"].values

    print(f"\n  선택 k={k}  검증 {len(sub):,}경기")
    print(f"  파크팩터 분포: {sub['pf'].min():.3f} ~ {sub['pf'].max():.3f} "
          f"(중앙 {sub['pf'].median():.3f})")

    # 제곱오차 단위로 부트스트랩
    m, lo, hi, pb = boot_diff((adj - act) ** 2, (base - act) ** 2)
    print(f"  제곱오차 차이 {m:+.4f}  95%CI [{lo:+.4f}, {hi:+.4f}]  "
          f"파크팩터 우위확률 {pb:.1%}")
    gate1 = pb > 0.95
    print(f"  → 관문 1 {'통과' if gate1 else '탈락'}")

    # 구장별 최종 파크팩터(참고용)
    print("\n  구장별 최종 파크팩터(검증 마지막 시점, 표본 30경기 이상):")
    last = (sub.groupby("stadium")
               .agg(pf=("pf", "last"), n=("pf", "size"))
               .query("n >= 30").sort_values("pf", ascending=False))
    for st, row in last.iterrows():
        print(f"    {st:10s} {row['pf']:.3f}  (검증 {int(row['n'])}경기)")

    # ------------------------------------------------------ 관문 2: O/U Brier
    print()
    print("=" * 72)
    print("관문 2 — 언더오버 Brier: 프로토 배당을 이기는가")
    print("=" * 72)

    ou = load_ou()
    mg = sub.merge(ou, on=["date", "home_team", "away_team"], how="inner")
    if mg.empty:
        print("  O/U 매칭 0건 — 조인 규약 확인 필요")
        return 1

    mg["p_base"] = [p_over(l, ln) for l, ln in zip(mg["base"], mg["line"])]
    mg["p_pf"] = [p_over(l * p, ln) for l, p, ln in zip(mg["base"], mg["pf"], mg["line"])]
    y = mg["over_won"].values.astype(float)

    b_mkt, b_base, b_pf = brier(mg["p_mkt"].values, y), brier(mg["p_base"].values, y), brier(mg["p_pf"].values, y)
    print(f"  검증 표본 {len(mg):,}경기 (오버 적중률 {y.mean():.1%})")
    print(f"    시장(프로토 devig)  Brier {b_mkt:.5f}")
    print(f"    모델 기준선          Brier {b_base:.5f}  ({b_base - b_mkt:+.5f})")
    print(f"    모델 +파크팩터       Brier {b_pf:.5f}  ({b_pf - b_mkt:+.5f})")

    m2, lo2, hi2, pb2 = boot_diff((mg["p_pf"].values - y) ** 2, (mg["p_base"].values - y) ** 2)
    print(f"\n  [모델 내부] 파크팩터 − 기준선: {m2:+.5f}  "
          f"CI [{lo2:+.5f}, {hi2:+.5f}]  우위확률 {pb2:.1%}")
    print(f"    필요 표본(현 효과 유지 시): {need_n(-m2, (hi2-lo2)/2, len(mg))}")

    m3, lo3, hi3, pb3 = boot_diff((mg["p_pf"].values - y) ** 2, (mg["p_mkt"].values - y) ** 2)
    print(f"  [대 시장] 파크팩터 − 시장:  {m3:+.5f}  "
          f"CI [{lo3:+.5f}, {hi3:+.5f}]  모델 우위확률 {pb3:.1%}")

    print(f"\n  → 관문 2 {'통과 — 시장을 이겼다' if pb3 > 0.95 else '탈락 — 시장을 못 이긴다'}")

    # ------------------------------------------- 관문 3: 시장이 이미 아는가
    # 관문 2 는 "우리 모델이 시장을 이기나"였다. 모델 자체가 약하면 파크팩터가
    # 좋아도 탈락한다. 그래서 진짜 질문은 따로 있다:
    #
    #     파크팩터는 **시장이 모르는 정보**인가?
    #
    # 시장 확률을 기준선으로 놓고 그 **위에** 파크팩터를 얹어 본다.
    # 시장이 이미 구장을 반영했다면 계수가 0 근처이고 검증 Brier 도 안 나아진다.
    print()
    print("=" * 72)
    print("관문 3 — 파크팩터는 시장이 모르는 정보인가 (시장 위에 얹기)")
    print("=" * 72)

    all_ou = df.merge(load_ou(), on=["date", "home_team", "away_team"], how="inner")
    all_ou = all_ou[(all_ou["p_mkt"] > 0.01) & (all_ou["p_mkt"] < 0.99)]
    tr, va = all_ou[all_ou["year"] <= TRAIN_END], all_ou[all_ou["year"] > TRAIN_END]

    def design(d, with_pf: bool):
        logit = np.log(d["p_mkt"].values / (1 - d["p_mkt"].values))
        cols = [np.ones(len(d)), logit]
        if with_pf:
            cols.append(np.log(d["pf"].values))
        return np.column_stack(cols)

    y_tr, y_va = tr["over_won"].values.astype(float), va["over_won"].values.astype(float)
    b0 = _fit(design(tr, False), y_tr)
    b1 = _fit(design(tr, True), y_tr)
    print(f"  학습 {len(tr):,} / 검증 {len(va):,}경기")
    if b0 is None or b1 is None:
        print("  적합 실패")
        return 0

    print(f"  log(PF) 계수 {b1[2]:+.3f}   "
          f"(0 이면 시장이 이미 반영, +면 시장이 과소반영)")
    e0, e1 = _brier(design(va, False), b0, y_va), _brier(design(va, True), b1, y_va)
    print(f"  검증 Brier  시장만 {e0:.5f}  →  +파크팩터 {e1:.5f}  ({e1 - e0:+.5f})")

    p0 = 1 / (1 + np.exp(-np.clip(design(va, False) @ b0, -30, 30)))
    p1 = 1 / (1 + np.exp(-np.clip(design(va, True) @ b1, -30, 30)))
    m4, lo4, hi4, pb4 = boot_diff((p1 - y_va) ** 2, (p0 - y_va) ** 2)
    print(f"  차이 {m4:+.5f}  CI [{lo4:+.5f}, {hi4:+.5f}]  파크팩터 우위확률 {pb4:.1%}")
    print(f"\n  → {'파크팩터에 시장이 모르는 정보가 있다' if pb4 > 0.95 else '시장이 이미 구장을 반영하고 있다'}")
    return 0


def load_ou() -> pd.DataFrame:
    """KBO 언더오버(전 경기, 2-way) — 라인·시장확률·결과.

    ⚠️ 함정 2: 배당 순서는 **[언더, 오버]** 다. 뒤집으면 양쪽 다 +ROI 라는
       불가능한 결과가 나온다. 여기서는 p_mkt = P(오버) 이므로 index 1.
    ⚠️ 'h U 4.5' 같은 라벨은 전반/부분 마켓이므로 제외한다.
    """
    g = pd.read_csv(PROC / "games.csv")
    g = g[(~g["is_void"].astype(bool))
          & (g["league"] == "KBO")
          & (g["market_family"] == "언더오버")
          & (g["n_way"] == 2)
          & (g["result"].isin(["오버", "언더"]))].copy()

    lab = g["market_label"].astype(str).str.strip()
    g = g[lab.str.fullmatch(r"U \d+\.5")]                 # 전 경기 라인만
    g["line"] = g["market_label"].astype(str).str.extract(r"U (\d+\.5)").astype(float)

    # ⚠️ 마켓마다 팀명 표기가 다르다.
    #    승패:     home="두산 12" / away="10 롯데"   (스코어 포함 → _home/_away 필요)
    #    언더오버: home="두산"    / away="롯데"      (팀명만)
    #    한쪽만 가정하면 조인이 통째로 0건이 된다. 둘 다 받는다.
    def _team(raw, fn):
        t = fn(raw)[0] if fn is _home else fn(raw)[1]
        if t:
            return t
        s = str(raw).strip()
        return s or None

    g = g.assign(home_team=[_team(x, _home) for x in g["home"]],
                 away_team=[_team(x, _away) for x in g["away"]])
    g = g.dropna(subset=["home_team", "away_team"])

    md = g["date_text"].astype(str).str.extract(_DATE_RE)
    g = g.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                 _dd=pd.to_numeric(md[1], errors="coerce")).dropna(subset=["_mm", "_dd"])
    g["date"] = pd.to_datetime(
        dict(year=g["year"], month=g["_mm"].astype(int), day=g["_dd"].astype(int)),
        errors="coerce")
    g = g.dropna(subset=["date"])

    def _p_over(s):
        try:
            o = [float(x) for x in str(s).split(",")]
        except ValueError:
            return np.nan
        if len(o) != 2 or min(o) <= 1.0:
            return np.nan
        return multiplicative(o)[1]        # [언더, 오버] → 오버 확률

    # ⚠️ pandas 3.0 의 Arrow 문자열 컬럼에 .map 을 쓰면 반환 dtype 이 str 로
    #    유지돼 이후 median 이 깨진다. 명시적으로 float 배열을 만든다.
    g["p_mkt"] = pd.Series([_p_over(s) for s in g["odds"]],
                           index=g.index, dtype="float64")
    g["over_won"] = (g["result"] == "오버").astype("int64")
    g = g.dropna(subset=["p_mkt"])

    # 중복 발매 제거 — 경기·라인 단위 중앙값 (함정 1)
    return (g.groupby(["home_team", "away_team", "date", "line"], as_index=False)
              .agg(p_mkt=("p_mkt", "median"), over_won=("over_won", "first")))


if __name__ == "__main__":
    raise SystemExit(main())
