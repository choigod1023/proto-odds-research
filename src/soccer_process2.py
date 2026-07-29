"""K리그 과정지표 결합 검정 — 유효슈팅과 득실차를 **같이** 넣으면 어떻게 되나.

왜 다시 하나
-----------
`findings/xG확보.md` 는 둘을 **따로** 넣고 비교했다.

    득실 차(결과)   계수 0.3097  z 1.30  Brier +0.00299
    유효슈팅 차(과정) 계수 0.1823  z 2.63  Brier +0.00062

유효슈팅의 z 가 **더 큰데** Brier 는 뒤집혔다 — 신호는 있는데 단독으로는 못 이긴다.
그리고 `findings/xG관문.md`(StatsBomb 1,500경기)의 결론은 이랬다:
**"단독으론 무승부, 같이 쓰면 xG 가 득실차를 밀어낸다."**

즉 K리그에서 안 해본 건 **결합**이다. 검증 표본도 241경기로 작았다.

무엇이 베팅에 쓸 수 있는 정보인가
--------------------------------
⚠️ 이번 경기 라인업은 못 쓴다(킥오프 1시간 전 공개, 배당은 최대 60시간 전 확정).
   그러나 **과거 경기의 슈팅·유효슈팅은 이미 공개돼 있다.** 배당 시점에 안다.
   그래서 이건 검증 5번(라인업)과 달리 실제로 베팅에 쓸 수 있는 정보다.

사용:
    python3 src/soccer_process2.py
    python3 src/soccer_process2.py --selftest
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "data" / "raw" / "detail" / "kleague_shots_2023_2026.json"
GAMES = ROOT / "data" / "processed" / "games.csv"
WINDOW = 10          # 최근 N경기 폼

_H = re.compile(r"^(.+?)\s+(-?\d+)\s*$")
_A = re.compile(r"^(-?\d+)\s+(.+?)\s*$")


# ⚠️ 프로토와 네이버의 팀 표기가 다르다. 접미사만 떼면 안 된다 —
#    **FC서울은 접두사**고, 울산HD·대전하나·제주SK 는 스폰서명이 붙는다.
#    처음에 접미사만 처리했다가 386개 키 중 139개만 붙었다(64% 손실).
_PREFIX = ("FC",)
_SUFFIX = ("FC", "HD", "SK", "하나", "상무", "유나", "아이", "시티",
           "삼성", "현대", "스틸", "그린", "시민")


def _norm(n: str) -> str:
    n = str(n).strip()
    for p in _PREFIX:
        if n.startswith(p) and len(n) > len(p):
            n = n[len(p):]
            break
    for s in _SUFFIX:
        if n.endswith(s) and len(n) > len(s):
            n = n[: -len(s)]
            break
    return n


def build() -> pd.DataFrame:
    """경기별 **직전까지의** 폼 → 그 경기 결과. 워크포워드라 누수가 없다."""
    raw = json.loads(SHOTS.read_text(encoding="utf-8"))
    rows = sorted(raw.values(), key=lambda g: g["date"])

    gf, ga = defaultdict(lambda: deque(maxlen=WINDOW)), defaultdict(lambda: deque(maxlen=WINDOW))
    sf, sa = defaultdict(lambda: deque(maxlen=WINDOW)), defaultdict(lambda: deque(maxlen=WINDOW))
    out = []
    for g in rows:
        d = g.get("data") or {}
        h, a = _norm(g["home"]), _norm(g["away"])
        H, A = d.get("home") or {}, d.get("away") or {}
        if H.get("sog") is None or A.get("sog") is None:
            continue
        if len(gf[h]) >= 5 and len(gf[a]) >= 5:
            out.append({
                "date": g["date"], "home": h, "away": a,
                # 득실 차 — 결과 지표
                "gd": (np.mean(gf[h]) - np.mean(ga[h])) - (np.mean(gf[a]) - np.mean(ga[a])),
                # 유효슈팅 차 — 과정 지표
                "sd": (np.mean(sf[h]) - np.mean(sa[h])) - (np.mean(sf[a]) - np.mean(sa[a])),
                "y": 1 if g["home_score"] > g["away_score"] else 0,
                "draw": g["home_score"] == g["away_score"],
            })
        gf[h].append(H["goals"]); ga[h].append(A["goals"])
        gf[a].append(A["goals"]); ga[a].append(H["goals"])
        sf[h].append(H["sog"]);   sa[h].append(A["sog"])
        sf[a].append(A["sog"]);   sa[a].append(H["sog"])
    return pd.DataFrame(out)


def with_market(df: pd.DataFrame) -> pd.DataFrame:
    """프로토 승무패 배당과 붙인다.

    ⚠️ K리그는 **승패(2-way) 가 아예 없다** — 축구는 무승부가 있어 3-way 로 발매된다.
       처음에 2-way 로 찾다가 결합 0건이 나왔다.
       무승부는 '홈이 이기는가' 질문의 대상이 아니므로 제외하고,
       시장 확률도 홈/원정 조건부로 정규화해 같은 기준으로 맞춘다.
    """
    g = pd.read_csv(GAMES)
    g = g[(g.market_family == "승무패") & (g.n_way == 3) & (g.sport == "sc") & (~g.is_void)]
    g = g[g.result.isin(["홈승", "홈패"])].copy()
    g["h"] = [_norm(_H.match(str(x)).group(1) if _H.match(str(x)) else x) for x in g.home]
    g["a"] = [_norm(_A.match(str(x)).group(2) if _A.match(str(x)) else x) for x in g.away]
    # ⚠️ pandas 3.0 은 문자열이 Arrow 라 Series + "|" 연결이 터진다.
    #    이 프로젝트에서 전에도 걸린 함정이라 리스트로 만든다.
    g["key"] = [f"{y}|{h}|{a}" for y, h, a in zip(g.year, g.h, g.a)]
    df = df.copy()
    df["key"] = [f"{d[:4]}|{h}|{a}" for d, h, a in zip(df.date, df.home, df.away)]
    j = g.merge(df[df.draw == False], on="key", how="inner").drop_duplicates("key")  # noqa: E712
    o = j.odds.astype(str).str.split(",", expand=True)
    if o.shape[1] < 3:
        return j.iloc[0:0]
    j["o_home"], j["o_away"] = o[0].astype(float), o[2].astype(float)   # 3-way: 홈·무·원정
    j["p_mkt"] = (1 / j.o_home) / (1 / j.o_home + 1 / j.o_away)
    j["y"] = (j.result == "홈승").astype(int)
    return j


def _fit(X, y, iters=400, lr=0.25):
    """작은 로지스틱. 표본이 수백 건이라 라이브러리 없이 충분하다."""
    X = np.c_[np.ones(len(X)), X]
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        w += lr * X.T @ (y - p) / len(X)
    return w


def _pred(w, X):
    return 1 / (1 + np.exp(-(np.c_[np.ones(len(X)), X] @ w)))


def run() -> pd.DataFrame:
    j = with_market(build())
    j = j.sort_values("date").reset_index(drop=True)
    cut = int(len(j) * 0.55)
    tr, te = j.iloc[:cut], j.iloc[cut:]
    y = te.y.values
    base = np.mean((te.p_mkt.values - y) ** 2)          # 시장 단독

    res = [("시장 단독", base, 0.0, None)]
    # 시장 확률 위에 피처를 얹는다 — 시장을 못 이기면 의미가 없다
    for name, cols in (("+ 득실차", ["gd"]), ("+ 유효슈팅차", ["sd"]),
                       ("+ 둘 다", ["gd", "sd"])):
        Xtr = np.c_[np.log(tr.p_mkt / (1 - tr.p_mkt)), tr[cols].values]
        Xte = np.c_[np.log(te.p_mkt / (1 - te.p_mkt)), te[cols].values]
        w = _fit(Xtr, tr.y.values)
        b = np.mean((_pred(w, Xte) - y) ** 2)
        res.append((name, b, base - b, w[2:]))
    return pd.DataFrame(res, columns=["모델", "Brier", "시장대비개선", "계수"]), len(tr), len(te)


def _selftest() -> int:
    df = build()
    bad = []
    print("과정지표 결합 자기검사")
    print(f"  폼 확보 경기 {len(df):,}건")
    if df.empty:
        bad.append("결합 결과가 비었다 — 팀 이름 정규화를 확인할 것")
    j = with_market(df)
    print(f"  프로토 결합 {len(j):,}건")
    if len(j) < 100:
        bad.append(f"프로토 결합 {len(j)}건 — 너무 적다(이름 매칭 실패 의심)")
    if not j.empty and not ((j.p_mkt > 0) & (j.p_mkt < 1)).all():
        bad.append("시장 확률이 0~1 범위 밖")
    print("  ✅ 시장 확률 범위")
    if bad:
        print("\n🔴 " + "\n🔴 ".join(bad))
        return 1
    print("\n✅ 통과")
    return 0


def main() -> int:
    tbl, n_tr, n_te = run()
    print(f"K리그1 승무패(무승부 제외) · 학습 {n_tr} / 검증 {n_te}\n")
    for r in tbl.itertuples():
        c = "" if r.계수 is None else "  계수 " + " ".join(f"{x:+.4f}" for x in r.계수)
        mark = "  ← 시장을 이김" if r.시장대비개선 > 0 and r.모델 != "시장 단독" else ""
        print(f"  {r.모델:<12} Brier {r.Brier:.5f}  개선 {r.시장대비개선:+.5f}{c}{mark}")
    print("\n⚠️ 개선이 양수라도 마진(12%)을 못 넘으면 베팅에는 못 쓴다.")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
