"""라인업 타선 — 그날 나온 9명을 **선수 개인 성적으로** 합성한다.

왜 다시 보나
------------
`타선무용.md` 는 타선이 죽었다고 판정했다. 그런데 그 실험이 쓴 건
`kbo_batters_2023_2026.json` — **팀 합계**다. 즉 이렇게 잰 셈이다.

    투수: 개인 단위(선발 xFIP)  →  통했다 (+0.006, 박빙 +0.014)
    타선: 팀 시즌 평균          →  죽었다 (≈0)

**비교가 공정하지 않았다.** 타선도 개인 단위로 재 본 적이 없다.
`HANDOFF.md` 가 "타선은 매일 같은 라인업이라 팀 수준 주변 진동"이라고 적었지만
그건 **가정**이었다. 실제로는 부상·휴식·외국인 교체·대타로 매일 바뀐다.
`batOrder` 를 보면 그날 누가 몇 번에 섰는지 알 수 있다.

무엇을 만드나
-------------
선수별 walk-forward wOBA(장타 가중 타격 지표)를 누적하고,
**그날 선발 라인업 9명**의 wOBA 를 타순 가중으로 합성한다.

    wOBA = (0.690·BB + 0.888·1B + 1.271·2B + 1.616·3B + 2.101·HR) / (AB + BB)

타순 가중은 기대 타석 수다(1번이 가장 많이 친다).
표본이 적은 선수는 리그 평균으로 당긴다(shrinkage).

⚠️ 규율 — HANDOFF 의 새 순서를 따른다
--------------------------------------
**관문 3(시장 확률 위에 얹기)을 먼저 돌린다.** 파크팩터에서 배운 것:
모델 내부 개선(관문1·2)을 다 통과하고도 "시장이 이미 안다"로 죽을 수 있다.
관문 3 이 가장 싸고 가장 잘 거르므로 여기부터 본다.

    ① 경기마다 바뀌는가   → 라인업은 매일 바뀐다 ✅
    ② Elo 가 모르는가     → Elo 는 팀 단위라 그날 누가 빠졌는지 모른다 ✅
    ③ 시장도 모르는가     → **이 스크립트가 재는 것**

그 외: walk-forward · 시간 분리(≤2024 학습 / 2025~ 검증) · 부트스트랩 ·
중복 발매 제거(경기당 중앙값).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detail_paths import latest_detail_path                # noqa: E402
from devig import multiplicative                       # noqa: E402
from matches import _DATE_RE, _away, _home             # noqa: E402
from park_factor import boot_diff, brier               # noqa: E402
from variable_impact import _brier, _fit               # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"


def indiv_path() -> Path:
    return latest_detail_path("kbo", "batters_indiv")


# Compatibility snapshot only; loaders resolve the path again at call time.
INDIV = indiv_path()

TRAIN_END = 2024
SHRINK_PA = 150          # 이 타석 수만큼은 리그 평균으로 당긴다
ALLSTAR = {"나눔", "드림"}

# 타순별 기대 타석 (1번이 가장 많이 친다)
ORDER_PA = {1: 4.7, 2: 4.6, 3: 4.5, 4: 4.4, 5: 4.3,
            6: 4.2, 7: 4.1, 8: 4.0, 9: 3.9}

W = {"bb": 0.690, "s": 0.888, "d": 1.271, "t": 1.616, "hr": 2.101}


def woba(st: dict) -> float | None:
    den = st["ab"] + st["bb"]
    if den <= 0:
        return None
    num = sum(W[k] * st[k] for k in W)
    return num / den


# ------------------------------------------------------------------ 데이터
def load_games(path: Path | None = None) -> list[dict]:
    """선발 라인업(타순 1~9의 첫 등장)만 남긴 경기 목록, 날짜순."""
    path = indiv_path() if path is None else path
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    out = []
    for v in raw.values():
        if v["home"] in ALLSTAR or v["away"] in ALLSTAR:
            continue
        d = v.get("data") or {}
        rec = {"date": v["date"], "home": v["home"], "away": v["away"],
               "home_score": v["home_score"], "away_score": v["away_score"]}
        ok = True
        for side in ("home", "away"):
            seen, starters = set(), []
            for p in d.get(side) or []:
                o = p.get("order")
                # 대타·대주자는 같은 타순을 공유한다 → **첫 등장만** 선발
                if not o or o in seen or not (1 <= int(o) <= 9):
                    continue
                seen.add(o)
                starters.append(p)
            if len(starters) < 9:
                ok = False
                break
            rec[side + "_lineup"] = starters
            # 그 경기의 팀 전체 타격(개인 누적 갱신용 — 대타 포함 전원)
            rec[side + "_all"] = d.get(side) or []
        if ok:
            out.append(rec)
    out.sort(key=lambda r: r["date"])
    return out


# -------------------------------------------------- walk-forward 선수 누적
RECENT_G = 15            # '주전'을 정하는 최근 경기 창


def build(games: list[dict]) -> pd.DataFrame:
    """각 경기 시점에서 **그 경기 이전** 기록만으로 두 가지를 만든다.

    ① `woba_*`     — 그날 라인업 9명의 타순 가중 wOBA (타선의 **수준**)
    ② `missing_*`  — 최근 주전 9인 대비 오늘 라인업이 **얼마나 약해졌는가**

    ②가 핵심이다. ①은 팀 전력의 일부라 시장이 이미 알 가능성이 크지만,
    ②는 **그날 누가 빠졌는가**라서 라인업 발표 시점에 따라 시장이 모를 수 있다.
    (HANDOFF 새 조건 ③ — '시장도 모르는가'를 만족할 여지)
    """
    acc: dict[str, dict] = {}
    lg = {"ab": 0, "bb": 0, "s": 0, "d": 0, "t": 0, "hr": 0}
    recent: dict[str, list[list[str]]] = {}      # 팀 → 최근 선발 라인업(코드) 목록
    rows = []

    def pw(code: str, lg_w: float) -> tuple[float, bool]:
        """선수의 축소 wOBA 와 '이력 있음' 여부."""
        a = acc.get(code)
        if not a:
            return lg_w, False
        n = a["ab"] + a["bb"]
        raw = woba(a)
        if raw is None:
            return lg_w, False
        k = n / (n + SHRINK_PA)
        return k * raw + (1 - k) * lg_w, True

    for g in games:
        lg_w = woba(lg) if lg["ab"] + lg["bb"] > 0 else 0.32

        vals = {}
        for side in ("home", "away"):
            team = g[side]
            tot_w, tot_pa, known, today = 0.0, 0.0, 0, []
            for p in g[side + "_lineup"]:
                w, ok = pw(p["code"], lg_w)
                pa_w = ORDER_PA.get(int(p["order"]), 4.3)
                tot_w += w * pa_w
                tot_pa += pa_w
                known += ok
                today.append(p["code"])
            vals[side] = tot_w / tot_pa if tot_pa else np.nan
            vals[side + "_known"] = known

            # --- 주전 이탈: 최근 창에서 가장 자주 선발로 나온 9명 대비
            hist = recent.get(team) or []
            miss = np.nan
            if len(hist) >= 5:
                cnt: dict[str, int] = {}
                for lu in hist:
                    for c in lu:
                        cnt[c] = cnt.get(c, 0) + 1
                reg = [c for c, _ in sorted(cnt.items(), key=lambda x: -x[1])[:9]]
                reg_w = np.mean([pw(c, lg_w)[0] for c in reg])
                today_w = np.mean([pw(c, lg_w)[0] for c in today])
                miss = reg_w - today_w          # +면 주전이 빠져 약해졌다
            vals[side + "_miss"] = miss

            recent.setdefault(team, []).append(today)
            if len(recent[team]) > RECENT_G:
                recent[team].pop(0)

        rows.append({"date": g["date"], "home_team": g["home"], "away_team": g["away"],
                     "home_score": g["home_score"], "away_score": g["away_score"],
                     "woba_home": vals["home"], "woba_away": vals["away"],
                     "miss_home": vals["home_miss"], "miss_away": vals["away_miss"],
                     "known_min": min(vals["home_known"], vals["away_known"])})

        # --- 누적 갱신 (경기 끝난 뒤) : 대타 포함 출전 전원
        for side in ("home", "away"):
            for p in g[side + "_all"]:
                a = acc.setdefault(p["code"], {"ab": 0, "bb": 0, "s": 0, "d": 0,
                                               "t": 0, "hr": 0})
                for k in ("ab", "bb", "s", "d", "t", "hr"):
                    a[k] += int(p.get(k) or 0)
                    lg[k] += int(p.get(k) or 0)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    df["woba_diff"] = df["woba_home"] - df["woba_away"]
    df["woba_sum"] = df["woba_home"] + df["woba_away"]
    # 원정이 더 많이 빠졌으면 홈에 유리 → 부호를 홈 기준으로 맞춘다
    df["miss_diff"] = df["miss_away"] - df["miss_home"]
    df["miss_sum"] = df["miss_home"] + df["miss_away"]
    return df.dropna(subset=["woba_diff"])


# ------------------------------------------------------------------ 시장
def load_market_wl() -> pd.DataFrame:
    """KBO 승패(2-way) 시장확률 + 결과. 경기당 1행(중복 발매 중앙값)."""
    g = pd.read_csv(PROC / "games.csv")
    g = g[(~g["is_void"].astype(bool)) & (g["league"] == "KBO")
          & (g["market_family"] == "승패") & (g["n_way"] == 2)
          & (g["result"].isin(["홈승", "홈패"]))].copy()

    hs, aw = g["home"].map(_home), g["away"].map(_away)
    g = g.assign(home_team=[t for t, _ in hs], away_team=[t for _, t in aw])
    g = g.dropna(subset=["home_team", "away_team"])

    md = g["date_text"].astype(str).str.extract(_DATE_RE)
    g = g.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                 _dd=pd.to_numeric(md[1], errors="coerce")).dropna(subset=["_mm", "_dd"])
    g["date"] = pd.to_datetime(
        dict(year=g["year"], month=g["_mm"].astype(int), day=g["_dd"].astype(int)),
        errors="coerce")
    g = g.dropna(subset=["date"])

    def _p(s):
        try:
            o = [float(x) for x in str(s).split(",")]
        except ValueError:
            return np.nan
        return multiplicative(o)[0] if len(o) == 2 and min(o) > 1.0 else np.nan

    g["p_mkt"] = pd.Series([_p(x) for x in g["odds"]], index=g.index, dtype="float64")
    g["home_won"] = (g["result"] == "홈승").astype("int64")
    g = g.dropna(subset=["p_mkt"])
    return (g.groupby(["home_team", "away_team", "date"], as_index=False)
              .agg(p_mkt=("p_mkt", "median"), home_won=("home_won", "first")))


# ------------------------------------------------------------------- 관문 3
def gate3(d: pd.DataFrame, feat: str, y_col: str, title: str) -> None:
    d = d[(d["p_mkt"] > 0.01) & (d["p_mkt"] < 0.99)].dropna(subset=[feat])
    tr, va = d[d["year"] <= TRAIN_END], d[d["year"] > TRAIN_END]
    if len(tr) < 100 or len(va) < 100:
        print(f"  [{title}] 표본 부족 (학습 {len(tr)} / 검증 {len(va)})")
        return

    def design(x, with_f):
        lg = np.log(x["p_mkt"].values / (1 - x["p_mkt"].values))
        cols = [np.ones(len(x)), lg]
        if with_f:
            cols.append(x[feat].values)
        return np.column_stack(cols)

    y_tr, y_va = tr[y_col].values.astype(float), va[y_col].values.astype(float)
    b0, b1 = _fit(design(tr, False), y_tr), _fit(design(tr, True), y_tr)
    if b0 is None or b1 is None:
        print(f"  [{title}] 적합 실패")
        return

    e0, e1 = _brier(design(va, False), b0, y_va), _brier(design(va, True), b1, y_va)
    p0 = 1 / (1 + np.exp(-np.clip(design(va, False) @ b0, -30, 30)))
    p1 = 1 / (1 + np.exp(-np.clip(design(va, True) @ b1, -30, 30)))
    m, lo, hi, pb = boot_diff((p1 - y_va) ** 2, (p0 - y_va) ** 2)

    print(f"\n  [{title}]  학습 {len(tr):,} / 검증 {len(va):,}")
    print(f"    {feat} 계수 {b1[2]:+.4f}")
    print(f"    검증 Brier  시장만 {e0:.5f} → +{feat} {e1:.5f}  ({e1 - e0:+.5f})")
    print(f"    부트스트랩 {m:+.5f}  CI [{lo:+.5f}, {hi:+.5f}]  우위확률 {pb:.1%}")
    print(f"    → {'✅ 시장이 모르는 정보가 있다' if pb > 0.95 else '❌ 시장이 이미 안다'}")


def main() -> int:
    path = indiv_path()
    if not path.exists():
        print(f"선수별 타자 데이터가 없다: {path}")
        print("먼저:  python3 src/game_detail.py batters_indiv kbo 2023 "
              f"{path.stem.rsplit('_', 1)[-1]}")
        return 1

    games = load_games(path)
    print(f"선발 라인업 9명이 확인된 경기: {len(games):,}")
    df = build(games)
    print(f"walk-forward 라인업 wOBA 산출: {len(df):,}경기")
    print(f"  wOBA 차 분포: {df['woba_diff'].min():+.4f} ~ {df['woba_diff'].max():+.4f} "
          f"(SD {df['woba_diff'].std():.4f})")
    print(f"  라인업 9명 중 이력 있는 선수 수(최소): 중앙 {df['known_min'].median():.0f}명")

    # --- 타당성 확인: 라인업 wOBA 가 실제 득점과 상관이 있나
    #     여기서 상관이 0 이면 지표를 잘못 만든 것이므로 시장을 물을 필요도 없다.
    runs_diff = df["home_score"] - df["away_score"]
    tot_runs = df["home_score"] + df["away_score"]
    print(f"  [타당성] wOBA 차 vs 실제 득점차 상관 {np.corrcoef(df['woba_diff'], runs_diff)[0,1]:+.4f}")
    print(f"           wOBA 합 vs 실제 총득점 상관 {np.corrcoef(df['woba_sum'], tot_runs)[0,1]:+.4f}")

    mk = load_market_wl()
    d = df.merge(mk, on=["date", "home_team", "away_team"], how="inner")
    print(f"  시장 조인(승패): {len(d):,}경기")

    print("\n" + "=" * 72)
    print("관문 3 — 라인업 타선은 시장이 모르는 정보인가")
    print("=" * 72)

    print("\n  ── ① 타선 수준 (라인업 wOBA) — 시장이 알 가능성이 높은 쪽")
    gate3(d, "woba_diff", "home_won", "승패 · 전체")
    gate3(d[d["known_min"] >= 7], "woba_diff", "home_won", "승패 · 라인업 7명 이상 이력")
    close = d[(d["p_mkt"] >= 0.45) & (d["p_mkt"] <= 0.55)]
    gate3(close, "woba_diff", "home_won", "승패 · 박빙(시장 45~55%)")

    print("\n  ── ② 주전 이탈 (오늘 누가 빠졌나) — ③을 만족할 여지가 있는 쪽")
    dm = d.dropna(subset=["miss_diff"])
    print(f"     주전이탈 차 SD {dm['miss_diff'].std():.4f} "
          f"(0 이면 라인업이 안 바뀐다는 뜻 → 가설 자체가 틀린 것)")
    gate3(dm, "miss_diff", "home_won", "승패 · 전체")
    gate3(dm[(dm["p_mkt"] >= 0.45) & (dm["p_mkt"] <= 0.55)],
          "miss_diff", "home_won", "승패 · 박빙")

    # --- 언더오버: 타선 총합은 총득점 마켓에 직결된다
    try:
        from park_factor import load_ou           # noqa: PLC0415
        ou = df.merge(load_ou(), on=["date", "home_team", "away_team"], how="inner")
        print(f"\n  시장 조인(언더오버): {len(ou):,}경기")
        gate3(ou, "woba_sum", "over_won", "언더오버 · 전체")
    except Exception as e:                        # noqa: BLE001
        print(f"\n  언더오버 조인 실패: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
