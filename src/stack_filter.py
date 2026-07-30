"""규칙 누적 — 구조적 선택만으로 유효 마진을 어디까지 낮출 수 있나.

왜 이걸 하나
------------
오늘까지 11번의 실험이 전부 **"정보를 더 모으자"** 였고 전부 실패했다.
그리고 실패 이유가 숫자로 확정됐다.

    필요한 우위 6.8%p   >   정보의 크기 2.4%p   (샤프 마켓 자신의 24h 스윙)

정보를 더 모으는 길은 **상한이 막혀 있다.** 그런데 부등식의 오른쪽은 상수가 아니다.

    아무거나 베팅            −12.75%
    핸디캡 '홈'              −18.41%
    승①패 '중간' / 3.0-5.0   **−3.85%**

같은 프로토 안에서 **14.6%p 가 벌어진다.** 예측력 0 으로도 −12% 를 −4% 로 만든다.
그러면 필요한 우위가 6.8%p 가 아니라 **3.9%p** 가 된다. 격차가 1/3 로 준다.

무엇이 새로운가
---------------
`anomaly_scan.py` 는 상품·선택지 × 리그 × 배당대까지 **4중 교차**했다.
그런데 거기 안 들어간 축이 넷 있다.

    · 초대면 매치업 회피   (`세갈래_스캔.md` ③, 초과 −1.89%)
    · 회차 환급률 등급     (회차마다 86~89% 로 다르다)
    · 무효 경기 제외       (배당에 1.0 → 취소)
    · 단폴 강제            (조합은 마진이 곱해진다)

**이 규칙들을 AND 로 쌓아 본 적이 없다.** 표본은 줄지만 ROI 는 오를 수 있다.
이 스크립트는 규칙을 하나씩 얹으며 **어디서 얼마가 오르는지**를 본다.

⚠️ 규율
-------
- 규칙 중 일부는 데이터에서 골랐다 → **학습(≤2024)에서 고르고 검증(2025~)에서 확인.**
  검증에서 무너지면 과적합이다.
- 게임행 단위가 아니라 선택지 단위라 같은 경기의 선택지는 독립이 아니다
  → 부트스트랩은 **경기 단위 클러스터**로 묶는다.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import _WINNER                               # noqa: E402
from matches import clean_team, load_matches           # noqa: E402

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
TRAIN_END = 2024

# 🔴 여기 사본을 두면 안 된다. 정본은 `bets._WINNER` 하나뿐이다.
#
#    이 표는 원래 여기 손으로 적혀 있었고 **홀짝이 빠져 있었다.** 88~90줄이
#    `WIN_IDX.get(...) is None` 이면 `continue` 라서 홀 9,985 · 짝 9,027 = 19,012 게임행이
#    손실등급표 모집단에서 조용히 사라졌다 — 전체의 10.9%.
#    `bets.py:37-41` 주석이 "매핑 테이블이 두 군데 있으면 한쪽만 고치게 된다 —
#    실제로 그랬다" 고 경고했는데, 그게 **세 번째 사본에서 그대로 재발**했다.
#    (market_scan 의 같은 누락이 KBL 가짜 ROI +30% 를 만들었다)
WIN_IDX = _WINNER
SEL = {(2, 0): "홈/언더", (2, 1): "원정/오버",
       (3, 0): "홈", (3, 1): "중간", (3, 2): "원정"}


def build() -> pd.DataFrame:
    g = pd.read_csv(PROC / "games.csv")
    g = g[~g["is_void"].astype(bool)].copy()

    # --- 팀명·날짜 (초대면 계산용)
    g["home_team"] = [clean_team(x) for x in g["home"]]
    g["away_team"] = [clean_team(x) for x in g["away"]]
    md = g["date_text"].astype(str).str.extract(r"(\d{2})\.(\d{2})")
    g["mmdd"] = md[0] + md[1]

    m = load_matches().sort_values("date").reset_index(drop=True)
    cnt: dict = defaultdict(int)
    prior = []
    for r in m.itertuples():
        k = tuple(sorted([r.home_team, r.away_team]))
        prior.append(cnt[k])
        cnt[k] += 1
    m = m.assign(prior_meets=prior, mmdd=m["date"].dt.strftime("%m%d"))
    g = g.merge(m[["year", "league", "mmdd", "home_team", "away_team", "prior_meets"]],
                on=["year", "league", "mmdd", "home_team", "away_team"], how="left")

    rows = []
    for r in g.itertuples():
        nw = int(r.n_way)
        wi = WIN_IDX.get((nw, r.result))
        if wi is None:
            continue
        try:
            odds = [float(x) for x in str(r.odds).split(",")]
        except ValueError:
            continue
        # ⚠️ 배당에 1.0 이 있으면 무효 경기다(규정: 전 선택지 1.0배).
        if len(odds) != nw or any(o <= 1.001 for o in odds):
            continue
        ov = sum(1 / o for o in odds)
        gid = f"{r.year}-{r.round}-{r.game_no}"
        for i, o in enumerate(odds):
            rows.append({
                "gid": gid, "year": r.year, "round": r.round, "league": r.league,
                "fam": r.market_family, "n_way": nw, "sel": SEL.get((nw, i), "?"),
                "booking": r.booking_class, "odds": o,
                "prior_meets": r.prior_meets,
                "ret": (o - 1) if i == wi else -1.0,
                # 적중 여부 — 수익률과 별개로 봐야 한다.
                # "덜 잃기" 와 "자주 맞기" 는 낮은 배당에서 같은 방향이지만,
                # 목표 배당을 올리면 갈라진다(1.0–1.3 적중 77% vs 5.0+ 11%).
                "hit": 1.0 if i == wi else 0.0,
                "base": (1 / o) / ov * o - 1,
            })
    d = pd.DataFrame(rows)
    d["edge"] = d["ret"] - d["base"]

    # --- 회차 환급률: 그 회차·구조의 평균 1/오버라운드
    d["payout"] = 1 + d["base"]
    rp = d.groupby(["year", "round", "booking"])["payout"].mean().rename("round_payout")
    d = d.merge(rp, on=["year", "round", "booking"], how="left")
    return d


def report(d: pd.DataFrame, label: str, rng) -> dict | None:
    tr, te = d[d["year"] <= TRAIN_END], d[d["year"] > TRAIN_END]
    if len(te) < 100:
        print(f"{label:<34}{len(tr):>8,}{'':>10}{len(te):>8,}   표본 부족")
        return None
    # 경기 단위 클러스터 부트스트랩
    gids = te["gid"].values
    uniq = pd.unique(gids)
    idx_by_gid = {k: v.values for k, v in te.groupby("gid").indices.items()} \
        if False else None
    groups = te.groupby("gid")["ret"].apply(list)
    arrs = [np.array(v, dtype=float) for v in groups.values]
    means = np.array([a.mean() for a in arrs])
    ns = np.array([len(a) for a in arrs])
    B = rng.integers(0, len(arrs), size=(3000, len(arrs)))
    boot = (means[B] * ns[B]).sum(axis=1) / ns[B].sum(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    star = " ⭐" if lo > 0 else ""
    print(f"{label:<34}{len(tr):>8,}{tr['ret'].mean():>+9.2%}{len(te):>8,}"
          f"{te['ret'].mean():>+9.2%}  [{lo:+.2%}, {hi:+.2%}]{star}")
    return {"label": label, "n_te": len(te), "roi_te": te["ret"].mean(), "lo": lo, "hi": hi}


def main() -> int:
    d = build()
    print(f"선택지 {len(d):,} (무효 제외 후) · 경기 {d['gid'].nunique():,}")
    print(f"초대면 정보 있는 비율 {d['prior_meets'].notna().mean():.1%}\n")

    rng = np.random.default_rng(42)
    print(f"{'누적 규칙':<34}{'학습n':>8}{'학습ROI':>9}{'검증n':>8}{'검증ROI':>9}  검증 95%CI")
    print("-" * 96)

    cur = d
    report(cur, "① 전체 (무효만 제외)", rng)

    cur = cur[cur["booking"] != "3-way-핸디캡"]
    report(cur, "② + 3-way핸디캡 제외", rng)

    cur = cur[~((cur["fam"] == "핸디캡") & (cur["sel"] == "홈"))]
    report(cur, "③ + 핸디캡 '홈' 제외", rng)

    cur = cur[cur["odds"] < 5.0]
    report(cur, "④ + 배당 5.0 이상 제외", rng)

    cur = cur[~((cur["n_way"] == 3) & (cur["sel"] != "중간"))]
    report(cur, "⑤ + 3-way는 '중간'만", rng)

    cur = cur[(cur["prior_meets"].isna()) | (cur["prior_meets"] >= 4)]
    report(cur, "⑥ + 초대면·1~3회 제외", rng)

    hi_payout = cur["round_payout"] >= cur["round_payout"].quantile(0.5)
    cur = cur[hi_payout]
    report(cur, "⑦ + 회차 환급률 상위 50%", rng)

    print("\n" + "=" * 96)
    print("각 규칙을 **단독**으로 걸었을 때 (누적 아님) — 어느 규칙이 실제로 기여하나")
    print("-" * 96)
    base = d
    for lab, sub in [
        ("3-way핸디캡 제외", base[base["booking"] != "3-way-핸디캡"]),
        ("핸디캡 '홈' 제외", base[~((base["fam"] == "핸디캡") & (base["sel"] == "홈"))]),
        ("배당 5.0 이상 제외", base[base["odds"] < 5.0]),
        ("3-way는 '중간'만", base[~((base["n_way"] == 3) & (base["sel"] != "중간"))]),
        ("초대면·1~3회 제외", base[(base["prior_meets"].isna()) | (base["prior_meets"] >= 4)]),
    ]:
        report(sub, lab, rng)

    print("\n" + "=" * 96)
    print("⭐ = 검증 95%CI 하한 > 0 (실제로 이긴다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
