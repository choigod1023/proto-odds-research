"""컵대회 이종 등급 대결 — R1 규칙을 3년치로 백테스트한다.

가설 (findings/라인이동_FA컵.md §4, 사전등록 R1)
------------------------------------------------
> 컵대회에서 리그 등급이 다른 팀이 붙으면 **상위 등급 팀**을 고른다.

FA컵 4경기에서 라인이 4/4 로 상위 등급 쪽으로 움직인 걸 보고 세운 가설이다.
n=4 라 판정이 안 됐고, 07-29 결과를 기다리기로 했었다.

**그런데 기다릴 필요가 없다.** 리그 등급은 과거 데이터로 복원할 수 있다.
팀이 그 해에 주로 뛴 리그가 곧 그 팀의 등급이다. 3년치 컵대회를 다 훑으면
n 이 수백 건이 된다.

⚠️ 무엇을 검정하는가 — 두 개를 구분해야 한다
--------------------------------------------
① **라인이 상위 팀 쪽으로 움직이는가** (오프닝 → 클로징)
   FA컵에서 본 것. 오프닝 배당이 있어야 하는데 스냅샷은 2일치뿐이다.
② **최종 배당에서도 상위 팀이 저평가돼 있는가** (ROI)
   `games.csv` 의 정산 배당으로 3년치 검정 가능. **이 문서가 재는 것.**

②가 양수면 ①보다 훨씬 강한 결과다(언제 사든 이긴다는 뜻).
②가 음수라도 ①은 살아 있을 수 있다 — 그건 오프닝 스냅샷이 쌓여야 안다.

⚠️ 앞선 결과와의 관계
   `세갈래_스캔.md` ③ 에서 **초대면 매치업이 −1.89% 로 더 나쁘다**고 나왔다.
   컵대회 이종 대결은 대부분 초대면이다. 그러니 ②는 음수일 공산이 크다.
   그래도 재는 이유: 초대면 페널티는 **양 팀 모두**에 적용되지만, R1 은
   **한쪽 편을 드는** 규칙이라 별개의 질문이다.
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import _WINNER                               # noqa: E402
from matches import load_matches                       # noqa: E402
from runtime_db import read_frame                       # noqa: E402

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"

# 나라별 리그 등급. 컵대회에서 만나는 조합만 있으면 된다.
TIER = {
    "K리그1": ("KR", 1), "K리그2": ("KR", 2),
    "J1리그": ("JP", 1), "J2리그": ("JP", 2),
    "EPL": ("EN", 1), "EFL챔": ("EN", 2),
    "라리가": ("ES", 1), "세리에A": ("IT", 1), "분데스리": ("DE", 1),
    "프리그1": ("FR", 1), "에레디비": ("NL", 1), "엘리테세": ("NL", 2),
    "MLS": ("US", 1), "A리그": ("AU", 1),
}
# 컵대회 → 나라 (그 나라 리그 등급으로 비교한다)
CUP = {
    "한국FA컵": "KR", "일본FA컵": "JP", "일리그컵": "JP",
    "잉글FA컵": "EN", "잉리그컵": "EN",
    "스페FA컵": "ES", "이탈FA컵": "IT", "독일FA컵": "DE",
    "프랑FA컵": "FR", "네덜FA컵": "NL", "미국FA컵": "US", "호주FA컵": "AU",
}
UNRANKED = 3          # 리그 데이터에 안 잡히는 팀 = 하부리그로 본다

# 정본(`bets._WINNER`)에서 **이 스크립트가 다루는 결과만** 잘라 쓴다.
# 손으로 다시 적으면 정본이 바뀔 때 조용히 어긋난다(홀짝이 실제로 그랬다).
_CUP_RESULTS = ("홈승", "무승부", "홈패")      # 92줄 result 필터와 같은 집합
WIN_IDX = {k: v for k, v in _WINNER.items() if k[1] in _CUP_RESULTS}


def build_tiers(m: pd.DataFrame) -> dict:
    """(연도, 나라, 팀) → 등급. 그 해에 **가장 많이 뛴 리그**가 그 팀의 등급이다."""
    seen: dict = defaultdict(Counter)
    for r in m.itertuples():
        t = TIER.get(r.league)
        if not t:
            continue
        country, tier = t
        for team in (r.home_team, r.away_team):
            seen[(r.year, country, team)][tier] += 1
    return {k: v.most_common(1)[0][0] for k, v in seen.items()}


def clean(x: str, home: bool) -> str:
    s = str(x).strip()
    return re.sub(r"\s+-?\d+\s*$", "", s) if home else re.sub(r"^\s*-?\d+\s+", "", s)


def main() -> int:
    m = load_matches()
    tiers = build_tiers(m)
    print(f"등급 복원: {len(tiers):,} (연도·나라·팀)")

    g = read_frame("processed_games", PROC / "games.csv")
    g = g[(~g["is_void"].astype(bool)) & g["league"].isin(CUP)
          & g["result"].isin(["홈승", "홈패", "무승부"])].copy()
    g["home_team"] = [clean(x, True) for x in g["home"]]
    g["away_team"] = [clean(x, False) for x in g["away"]]
    print(f"컵대회 게임행: {len(g):,} (대회 {g['league'].nunique()}개)")

    rows = []
    for r in g.itertuples():
        country = CUP[r.league]
        th = tiers.get((r.year, country, r.home_team), UNRANKED)
        ta = tiers.get((r.year, country, r.away_team), UNRANKED)
        if th == ta:
            continue                       # 동급 대결은 R1 의 대상이 아니다
        nw = int(r.n_way)
        wi = WIN_IDX.get((nw, r.result))
        if wi is None:
            continue
        odds = [float(x) for x in str(r.odds).split(",")]
        if len(odds) != nw or any(o <= 1 for o in odds):
            continue
        # 상위 등급(숫자가 작은 쪽) 편에 건다
        side = 0 if th < ta else (1 if nw == 2 else 2)
        o = odds[side]
        ov = sum(1 / x for x in odds)
        rows.append({
            "year": r.year, "cup": r.league, "n_way": nw,
            "tier_gap": abs(th - ta), "stronger": "홈" if th < ta else "원정",
            "odds": o, "won": int(side == wi),
            "ret": (o - 1) if side == wi else -1.0,
            "base": (1 / o) / ov * o - 1,
        })

    d = pd.DataFrame(rows)
    if d.empty:
        print("이종 등급 대결 0건 — 등급 복원 또는 대회 매핑 확인")
        return 1
    d["edge"] = d["ret"] - d["base"]

    rng = np.random.default_rng(42)

    def ci(x):
        x = np.asarray(x, dtype=float)
        idx = rng.integers(0, len(x), size=(4000, len(x)))
        b = x[idx].mean(axis=1)
        return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))

    lo, hi = ci(d["ret"].values)
    print(f"\n{'='*78}")
    print("R1 — 컵대회 이종 등급 대결에서 상위 등급 팀에 베팅")
    print("=" * 78)
    print(f"  표본 {len(d):,}건 · 적중 {d['won'].mean():.1%} · 평균배당 {d['odds'].mean():.2f}")
    print(f"  ROI {d['ret'].mean():+.2%}  95%CI [{lo:+.2%}, {hi:+.2%}]")
    print(f"  기준선 {d['base'].mean():+.2%} · 초과 {d['edge'].mean():+.2%}")
    print(f"  → {'✅ 살아있다' if lo > 0 else '❌ 기각'}")

    print(f"\n{'구분':<14}{'n':>7}{'적중':>8}{'ROI':>9}{'초과':>9}  95%CI")
    print("-" * 78)
    for lab, sub in [("등급차 1", d[d["tier_gap"] == 1]),
                     ("등급차 2+", d[d["tier_gap"] >= 2]),
                     ("상위=홈", d[d["stronger"] == "홈"]),
                     ("상위=원정", d[d["stronger"] == "원정"]),
                     ("2-way", d[d["n_way"] == 2]),
                     ("3-way", d[d["n_way"] == 3])]:
        if len(sub) < 30:
            continue
        l, h = ci(sub["ret"].values)
        star = " ⭐" if l > 0 else ""
        print(f"{lab:<14}{len(sub):>7,}{sub['won'].mean():>7.1%}{sub['ret'].mean():>+9.2%}"
              f"{sub['edge'].mean():>+9.2%}  [{l:+.2%}, {h:+.2%}]{star}")

    print(f"\n{'대회':<12}{'n':>7}{'적중':>8}{'ROI':>9}{'초과':>9}")
    print("-" * 50)
    for cup, s in sorted(d.groupby("cup"), key=lambda x: -len(x[1])):
        if len(s) < 20:
            continue
        print(f"{cup:<12}{len(s):>7,}{s['won'].mean():>7.1%}{s['ret'].mean():>+9.2%}{s['edge'].mean():>+9.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
