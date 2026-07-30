""""근소 우위"를 어느 마켓으로 푸는가 — 그리고 핸디캡 라인별 헐거움.

두 질문
--------
**① 같은 판단을 마켓마다 다르게 표현할 수 있다.**
"삼성이 조금 낫다"(승패 55%)는 판단은
    · 승패 홈       — 가장 직접적이지만 배당이 짜다
    · 핸디캡 +1.5   — 안전하지만 배당이 더 짜다
    · 핸디캡 −1.5   — 배당은 좋지만 2점차 이상이어야 한다
    · 언더           — 접전이면 낮은 스코어일 수도
중 어디로 푸는 게 실제로 유리했나?

**② 같은 경기에 여러 핸디 라인이 걸린다.** 어느 라인이 가장 헐거운가?

방법
----
프로토 배당에서 devig 확률을 뽑고, 스코어 분포 모델 확률과 비교해
**실제 결과로 마켓별 ROI 를 잰다.** 판단 강도(모델 승률)로 층화한다.

⚠️ 표본이 작으면 ROI 는 잡음이다. 부트스트랩 신뢰구간을 같이 낸다.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matches import clean_team                           # noqa: E402
from score_dist import joint, p_handicap, p_over, p_win   # noqa: E402

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
TRAIN_END = 2024
SEED = 42
_LINE = re.compile(r"([-+]?\d+\.?\d*)")


def load() -> pd.DataFrame:
    lam = pd.read_csv(PROC / "lambdas.csv", parse_dates=["date"])
    g = pd.read_csv(PROC / "games.csv")
    g = g[~g["is_void"].astype(bool)]
    md = g["date_text"].astype(str).str.extract(r"(\d{2})\.(\d{2})")
    g = g.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                 _dd=pd.to_numeric(md[1], errors="coerce")).dropna(subset=["_mm", "_dd"])
    g["date"] = pd.to_datetime(dict(year=g["year"], month=g["_mm"].astype(int),
                                    day=g["_dd"].astype(int)), errors="coerce")
    g["home_team"] = [clean_team(x) for x in g["home"]]
    g["away_team"] = [clean_team(x) for x in g["away"]]
    key = ["date", "league", "home_team", "away_team"]
    # ⚠️ games.csv 에 이미 sport 가 있으므로 lam 쪽에서는 빼고 병합한다
    return g.merge(lam[key + ["lam_home", "lam_away"]], on=key, how="inner")


def boot_ci(prof: np.ndarray, n: int = 3000) -> tuple[float, float]:
    if len(prof) < 30:
        return float("nan"), float("nan")
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(prof), size=(n, len(prof)))
    d = prof[idx].mean(axis=1)
    return float(np.quantile(d, .025)), float(np.quantile(d, .975))


def main() -> int:
    df = load()
    df = df[df["year"] > TRAIN_END]
    print(f"검증 구간 게임행 {len(df):,}")

    # 경기별 모델 승률 (근소 우위 판정용)
    key = ["date", "league", "home_team", "away_team"]
    gm = df.drop_duplicates(key)[key + ["lam_home", "lam_away", "sport"]].copy()
    ph = []
    for r in gm.itertuples():
        h, d0, a = p_win(joint(r.lam_home, r.lam_away, r.sport))
        ph.append(h / (h + a) if h + a > 0 else np.nan)
    gm["p_home"] = ph
    df = df.merge(gm[key + ["p_home"]], on=key, how="left").dropna(subset=["p_home"])

    # 판단 강도 층
    df["band"] = pd.cut(df["p_home"], [0, .40, .45, .55, .60, 1.0],
                        labels=["원정 우세", "원정 근소", "박빙", "홈 근소", "홈 우세"])

    WIN = {(2, "홈승"): 0, (2, "홈패"): 1, (2, "언더"): 0, (2, "오버"): 1,
           (2, "핸디승"): 0, (2, "핸디패"): 1,
           (3, "홈승"): 0, (3, "무승부"): 1, (3, "홈패"): 2,
           (3, "핸디승"): 0, (3, "핸디무"): 1, (3, "핸디패"): 2, (3, "①"): 1}

    rows = []
    for r in df.itertuples():
        nw = int(r.n_way)
        wi = WIN.get((nw, r.result))
        if wi is None:
            continue
        odds = [float(x) for x in str(r.odds).split(",") if x]
        if len(odds) != nw or any(o <= 1 for o in odds):
            continue
        M = joint(r.lam_home, r.lam_away, r.sport)
        fam = r.market_family
        line = None
        if fam in ("언더오버", "핸디캡"):
            m0 = _LINE.search(str(r.market_label))
            if not m0:
                continue
            line = float(m0.group(1))
        if fam == "승패" and nw == 2:
            h, _, a = p_win(M); s = h + a
            pm = [h / s, a / s]
        elif fam == "승무패" and nw == 3:
            pm = list(p_win(M))
        elif fam == "언더오버":
            po = p_over(M, line); pm = [1 - po, po]
        elif fam == "핸디캡":
            w, d0, l = p_handicap(M, line)
            pm = [w / (w + l), l / (w + l)] if nw == 2 else [w, d0, l]
        else:
            continue
        if len(pm) != nw:
            continue
        ov = sum(1 / o for o in odds)
        for i in range(nw):
            rows.append({"band": r.band, "sport": r.sport,
                         "market": f"{fam}({nw}-way)", "line": line,
                         "p_model": pm[i], "p_proto": (1 / odds[i]) / ov,
                         "odds": odds[i], "won": i == wi})
    L = pd.DataFrame(rows)
    L["ev"] = L["p_model"] * L["odds"] - 1
    L["profit"] = np.where(L["won"], L["odds"] - 1, -1.0)
    print(f"선택지 {len(L):,}\n")

    # ---------- ① 근소 우위를 어느 마켓으로 푸는가
    print("=" * 78)
    print("① 판단 강도별 · 마켓별 실측 ROI  (모델이 EV>0 이라 한 것만)")
    print("=" * 78)
    print(f"{'판단':<10}{'마켓':<18}{'n':>7}{'적중':>8}{'ROI':>9}{'95% CI':>22}")
    for band in ("홈 근소", "박빙", "홈 우세"):
        sub = L[(L["band"] == band) & (L["ev"] > 0)]
        if len(sub) < 50:
            continue
        for mk, s in sub.groupby("market"):
            if len(s) < 50:
                continue
            p = s["profit"].to_numpy()
            lo, hi = boot_ci(p)
            print(f"{band:<10}{mk:<18}{len(s):>7,}{s['won'].mean():>8.1%}"
                  f"{p.mean():>9.2%}{f'[{lo:+.1%}, {hi:+.1%}]':>22}")
        print()

    # ---------- ② ③ 라인별 헐거움
    # ⚠️ 한 경기의 모든 선택지에서 (모델−프로토) 를 평균하면 **정의상 0** 이다
    #    (양쪽 확률이 각각 1로 정규화되므로). 적중률도 1/n_way 로 고정된다.
    #    → 선택지 단위가 아니라 **모델이 EV>0 이라 지목한 쪽**만 봐야 의미가 있다.
    for title, pref in (("② 핸디캡 라인별", "핸디캡"), ("③ 언더오버 라인별", "언더오버")):
        print("\n" + "=" * 78)
        print(f"{title} — 모델이 지목한 쪽의 실측 성적")
        print("=" * 78)
        sub = L[L["market"].str.startswith(pref)].copy()
        print(f"{'라인':>7}{'전체n':>8}{'지목n':>7}{'평균 |괴리|':>12}"
              f"{'적중':>8}{'ROI':>9}{'95% CI':>22}")
        for line, s0 in sub.groupby("line"):
            if len(s0) < 300:
                continue
            gap = (s0["p_model"] - s0["p_proto"]).abs().mean()
            pick = s0[s0["ev"] > 0]
            if len(pick) < 30:
                print(f"{line:>7.1f}{len(s0):>8,}{len(pick):>7,}{gap:>+12.4f}"
                      f"{'—':>8}{'—':>9}{'표본 부족':>22}")
                continue
            pr = pick["profit"].to_numpy()
            lo, hi = boot_ci(pr)
            print(f"{line:>7.1f}{len(s0):>8,}{len(pick):>7,}{gap:>+12.4f}"
                  f"{pick['won'].mean():>8.1%}{pr.mean():>9.2%}"
                  f"{f'[{lo:+.1%}, {hi:+.1%}]':>22}")

    print("\n기준선: 프로토에 아무거나 걸면 약 −12%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
