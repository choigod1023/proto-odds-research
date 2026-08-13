"""배당 이동이 결과를 예고하는가 — 두 소스를 합쳐서 본다.

가설
----
사용자 질문: "시장이 사지 않아 배당이 높아진 것을 고르면 적중률이 오르나."

프로토는 **고정배당**이라 파리뮤추얼처럼 물량이 배당을 직접 만들지는 않는다.
그러나 발매사가 물량·정보에 반응해 배당을 조정한다면, 그 **이동 방향**에
정보가 담긴다. 문헌에서는 이걸 CLV(closing line value)라 부른다.

두 소스가 있다 — 해상도가 달라 나눠서도 보고 합쳐서도 본다.

1. **회차 겹침** (`games.csv`)
   프로토는 회차를 겹쳐 발매해 **같은 경기가 여러 회차에 다른 배당으로** 걸린다.
   이른 회차 = 먼저 매긴 가격, 늦은 회차 = 나중 가격.
   ⚠️ 이 관행은 **2026년에 시작**됐다. 회차당 발매 행이 248(2023) → 427(2026)로
      늘면서 생겼다. 그래서 아카이브가 553회차여도 여기 쓸 수 있는 건 2026년분뿐이다.

2. **스냅샷** (`data/raw/snapshots/odds_timeseries.csv`)
   15분 간격 수집. 시점 해상도가 높아 **마감 직전 움직임**을 볼 수 있다.
   대신 수집을 시작한 뒤부터만 있다.

⚠️ 판정 기준을 데이터 보기 전에 적어둔다
   · 클러스터(마켓) 부트스트랩 95%CI 가 0 을 포함하면 **기각**
   · 두 소스의 부호가 다르면 **기각** (한쪽은 잡음이라는 뜻)
   · 상위 적중 3건을 빼서 부호가 뒤집히면 **기각**

사용:
    python3 src/line_move.py
    python3 src/line_move.py --selftest
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from stack_filter import WIN_IDX  # noqa: E402

GAMES = ROOT / "data" / "processed" / "games.csv"
# ⚠️ 스냅샷은 2026-08-13 부터 **월별 샤드**다(단일 파일이 138MB 가 되어
#    GitHub 100MB 한도에 걸렸다). 경로를 직접 열지 말고 로더를 쓴다.
from snapshot import load_timeseries, ts_files      # noqa: E402
SKIP = ("경기전", "취소", "")


def _pair(o0: str, o1: str, n_way: int, result: str, key: str, src: str) -> list[dict]:
    """가격 두 개(이전/이후)를 선택지별 행으로 편다."""
    wi = WIN_IDX.get((int(n_way), str(result)))
    if wi is None:
        return []
    try:
        a = [float(x) for x in str(o0).split(",")]
        b = [float(x) for x in str(o1).split(",")]
    except ValueError:
        return []
    if len(a) != len(b) or len(b) != int(n_way) or any(x <= 1.001 for x in b):
        return []
    out = []
    for i, (p, q) in enumerate(zip(a, b)):
        if abs(q / p - 1) < 1e-9:
            continue                      # 안 움직인 선택지는 정보가 없다
        out.append({"key": key, "src": src, "drift": q / p, "odds": q,
                    "hit": 1 if i == wi else 0,
                    "ret": (q - 1) if i == wi else -1.0})
    return out


def from_rounds() -> pd.DataFrame:
    """회차 겹침 — 같은 경기가 여러 회차에 다른 배당으로 걸린 경우."""
    g = pd.read_csv(GAMES)
    g = g[(~g["is_void"].astype(bool)) & (g["n_way"] > 0)].copy()
    g = g[~g["result"].isin(SKIP)]
    g["team_h"] = g["home"].astype(str).str.replace(r"\s+-?\d+\s*$", "", regex=True).str.strip()
    g["team_a"] = g["away"].astype(str).str.replace(r"^\s*-?\d+\s+", "", regex=True).str.strip()
    md = g["date_text"].astype(str).str.extract(r"(\d{2})\.(\d{2})")
    g["mmdd"] = md[0].fillna("") + md[1].fillna("")
    g["k"] = (g["year"].astype(str) + "|" + g["league"].astype(str) + "|" + g["team_h"]
              + "|" + g["team_a"] + "|" + g["mmdd"] + "|"
              + g["market_family"].astype(str) + g["market_label"].astype(str))
    rows = []
    for k, x in g.groupby("k"):
        if x["round"].nunique() < 2 or x["odds"].nunique() < 2:
            continue
        x = x.sort_values("round")
        a, b = x.iloc[0], x.iloc[-1]
        rows += _pair(a["odds"], b["odds"], b["n_way"], b["result"], k, "회차겹침")
    return pd.DataFrame(rows)


def from_snapshots() -> pd.DataFrame:
    """스냅샷 — 15분 간격. 첫 관측 대비 마지막 관측."""
    if not ts_files():
        return pd.DataFrame()
    t = load_timeseries()
    t["ts"] = pd.to_datetime(t["ts"])
    t = t.sort_values("ts")
    # ⚠️ 결과로 먼저 거르면 안 된다. 스냅샷은 시점별 스냅이라 **경기 전 행의 result 는
    #    '경기전'** 이다. 미리 거르면 경기 전 가격이 통째로 날아가고 first==last 가 돼
    #    이동이 0 건이 된다(실제로 그랬다). 첫/마지막 배당은 전체에서 잡고,
    #    결과만 마지막 행(정산 후)에서 가져온다.
    key = ["year", "round", "game_no"]
    first = t.groupby(key).head(1)[key + ["odds"]].rename(columns={"odds": "o_first"})
    settled = t[~t["result"].isin(SKIP)].groupby(key).tail(1)
    last = settled.merge(first, on=key)
    rows = []
    for r in last.itertuples():
        k = f"{r.year}-{r.round}-{r.game_no}"
        rows += _pair(r.o_first, r.odds, r.n_way, r.result, k, "스냅샷")
    return pd.DataFrame(rows)


def _boot(d: pd.DataFrame, n: int = 4000, seed: int = 42):
    """마켓 단위 클러스터 부트스트랩 — 같은 마켓의 선택지는 독립이 아니다."""
    rng = np.random.default_rng(seed)
    codes, uniq = pd.factorize(d["key"])
    groups = [np.where(codes == i)[0] for i in range(len(uniq))]
    ret, isup = d["ret"].values, (d["drift"] > 1).values
    out = []
    for _ in range(n):
        ii = np.concatenate([groups[j] for j in rng.integers(0, len(uniq), len(uniq))])
        r, u = ret[ii], isup[ii]
        if u.sum() < 10 or (~u).sum() < 10:
            continue
        out.append(r[u].mean() - r[~u].mean())
    return np.array(out)


def _report(name: str, d: pd.DataFrame) -> dict:
    up, dn = d[d["drift"] > 1], d[d["drift"] < 1]
    if len(up) < 20 or len(dn) < 20:
        print(f"  {name:<12} 표본 부족 (상승 {len(up)} · 하락 {len(dn)})")
        return {}
    obs = up["ret"].mean() - dn["ret"].mean()
    bs = _boot(d)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    print(f"  {name:<12} n={len(d):>5,}  상승 적중 {up['hit'].mean()*100:5.2f}% ROI {up['ret'].mean()*100:+6.2f}%"
          f" | 하락 적중 {dn['hit'].mean()*100:5.2f}% ROI {dn['ret'].mean()*100:+6.2f}%")
    print(f"  {'':12} 차이 {obs*100:+6.2f}%p  95%CI [{lo*100:+.2f}, {hi*100:+.2f}]"
          f"  {'✅ 유의' if lo > 0 else '🔴 기각'}")
    return {"obs": obs, "lo": lo, "hi": hi, "n": len(d)}


def _selftest() -> int:
    r = from_rounds()
    s = from_snapshots()
    bad = []
    print("배당 이동 자기검사")
    print(f"  회차겹침 {len(r):,} · 스냅샷 {len(s):,}")
    for nm, d in (("회차겹침", r), ("스냅샷", s)):
        if d.empty:
            continue
        if not ((d["drift"] > 0).all()):
            bad.append(f"{nm}: drift 가 0 이하")
        if (abs(d["drift"] - 1) < 1e-9).any():
            bad.append(f"{nm}: 안 움직인 행이 섞였다")
    print("  ✅ drift 성질 (양수 · 이동한 것만)")
    if bad:
        print("\n🔴 " + "\n🔴 ".join(bad))
        return 1
    print("\n✅ 통과")
    return 0


def main() -> int:
    r, s = from_rounds(), from_snapshots()
    both = pd.concat([r, s], ignore_index=True)
    print("배당이 오른 쪽(안 팔림)이 더 잘 맞는가\n")
    res = {}
    for nm, d in (("회차겹침", r), ("스냅샷", s), ("합계", both)):
        if not d.empty:
            res[nm] = _report(nm, d)

    a, b = res.get("회차겹침", {}), res.get("스냅샷", {})
    if a and b:
        same = np.sign(a["obs"]) == np.sign(b["obs"])
        print(f"\n두 소스 부호 {'일치' if same else '**불일치**'} "
              f"({a['obs']*100:+.1f}%p vs {b['obs']*100:+.1f}%p)"
              f"{'' if same else ' → 한쪽은 잡음이다. 기각.'}")

    # 지금 표본으로 얼마짜리 효과를 잡을 수 있나 (검정력)
    if not both.empty:
        sd = both["ret"].std()
        n_up = (both["drift"] > 1).sum()
        n_dn = (both["drift"] < 1).sum()
        mde = 2.8 * sd * np.sqrt(1 / n_up + 1 / n_dn)     # 80% 검정력 근사
        print(f"\n지금 표본으로 탐지 가능한 최소 효과(MDE, 80% 검정력) ≈ {mde*100:.1f}%p")
        for mult in (2, 4, 10):
            print(f"  표본 {mult}배 →  {mde/np.sqrt(mult)*100:5.1f}%p")
        print("  ※ 프로토 마진이 12% 다. 이걸 넘으려면 최소 그 이상을 잡아야 한다.")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
