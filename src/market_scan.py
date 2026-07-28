"""전 마켓 스캔 — 프로토는 어느 시장을 가장 못 매기는가.

왜 이게 핵심인가
----------------
지금까지 승패·승무패만 봤다. 그건 프로토 물량의 **38.8%** 다.
나머지 61.2%(언더오버 26.1%, 핸디캡 29.3%, 승①패 4.9%)는 손도 안 댔다.

스코어 분포 모델(`score_dist.py`)이 있으면 **하나의 예측으로 전 마켓을 가격 매길 수 있다.**
그러면 이 질문에 답할 수 있다:

> **프로토가 승패는 잘 매기는데 언더오버는 못 매길 수도 있지 않나?**

시장마다 난이도가 다르다. 총득점 예측은 승패 예측보다 어렵고,
북메이커가 어려워하는 곳이 곧 기회다.

측정
----
각 마켓에서 **모델 확률 vs 프로토 devig 확률**의 Brier 를 비교한다.
모델이 프로토보다 정확한 마켓이 있다면 거기가 공략 지점이다.

⚠️ 라인·핸디캡 값은 마켓 라벨에서 파싱한다: `U 5.5`, `H -1.0`
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_dist import (joint, p_handicap, p_margin_band, p_one_run,  # noqa: E402
                        p_over, p_win)

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
TRAIN_END = 2024

_LINE = re.compile(r"([-+]?\d+\.?\d*)")


def parse_line(label: str) -> float | None:
    m = _LINE.search(str(label or ""))
    return float(m.group(1)) if m else None


def load() -> pd.DataFrame:
    lam = pd.read_csv(PROC / "lambdas.csv", parse_dates=["date"])
    g = pd.read_csv(PROC / "games.csv")
    g = g[~g["is_void"].astype(bool)]
    md = g["date_text"].astype(str).str.extract(r"(\d{2})\.(\d{2})")
    g = g.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                 _dd=pd.to_numeric(md[1], errors="coerce")).dropna(subset=["_mm", "_dd"])
    g["date"] = pd.to_datetime(dict(year=g["year"], month=g["_mm"].astype(int),
                                    day=g["_dd"].astype(int)), errors="coerce")
    g = g.dropna(subset=["date"])

    # 팀명 정리 (스코어가 붙어 있다)
    g["home_team"] = g["home"].map(
        lambda x: (re.match(r"^(.+?)\s+-?\d+\s*$", str(x).strip()) or [None, str(x).strip()])[1]
        if re.match(r"^(.+?)\s+-?\d+\s*$", str(x).strip()) else str(x).strip())
    g["away_team"] = g["away"].map(
        lambda x: (re.match(r"^-?\d+\s+(.+?)\s*$", str(x).strip()) or [None, str(x).strip()])[1]
        if re.match(r"^-?\d+\s+(.+?)\s*$", str(x).strip()) else str(x).strip())

    key = ["date", "league", "home_team", "away_team"]
    return g.merge(lam[key + ["lam_home", "lam_away", "sport"]],
                   on=key, how="inner", suffixes=("", "_l"))


def model_probs(row) -> list[float] | None:
    """마켓별 모델 확률 벡터. 프로토 선택지 순서와 맞춘다."""
    M = joint(row["lam_home"], row["lam_away"], row["sport"])
    fam, nw = row["market_family"], int(row["n_way"])
    lab = row["market_label"]

    if fam == "승패" and nw == 2:
        h, d, a = p_win(M)
        s = h + a
        return [h / s, a / s] if s > 0 else None
    if fam == "승무패" and nw == 3:
        return list(p_win(M))
    if fam == "언더오버" and nw == 2:
        line = parse_line(lab)
        if line is None:
            return None
        po = p_over(M, line)
        return [1 - po, po]                 # [언더, 오버]
    if fam == "핸디캡":
        hc = parse_line(lab)
        if hc is None:
            return None
        w, d, l = p_handicap(M, hc)
        if nw == 2:
            s = w + l
            return [w / s, l / s] if s > 0 else None
        return [w, d, l]
    if fam == "승①패" and nw == 3:
        return list(p_one_run(M))
    if fam == "승⑤패" and nw == 3:
        # 농구·배구의 3-way 일반 마켓. 무승부가 아니라 **5점차 이내**다.
        return list(p_margin_band(M, 5))
    return None


WIN_IDX = {(2, "홈승"): 0, (2, "홈패"): 1, (2, "언더"): 0, (2, "오버"): 1,
           (2, "핸디승"): 0, (2, "핸디패"): 1,
           (3, "홈승"): 0, (3, "무승부"): 1, (3, "홈패"): 2,
           (3, "핸디승"): 0, (3, "핸디무"): 1, (3, "핸디패"): 2, (3, "①"): 1,
           (3, "⑤"): 1}


def main() -> int:
    df = load()
    print(f"프로토 게임행 × λ 결합 {len(df):,}건")

    rows = []
    for r in df.itertuples():
        d = r._asdict()
        wi = WIN_IDX.get((int(d["n_way"]), d["result"]))
        if wi is None:
            continue
        odds = [float(x) for x in str(d["odds"]).split(",") if x]
        if len(odds) != int(d["n_way"]) or any(o <= 1 for o in odds):
            continue
        pm = model_probs(d)
        if not pm or len(pm) != len(odds) or any(not np.isfinite(p) for p in pm):
            continue
        ov = sum(1 / o for o in odds)
        pp = [(1 / o) / ov for o in odds]        # 프로토 devig
        for i, (p_model, p_proto, o) in enumerate(zip(pm, pp, odds)):
            rows.append({
                # ⚠️ league 를 남기는 이유: 지금까지 종목(bs/sc/bk/vl)으로만
                #    쪼개 봤는데, 프로토 배당은 해외 북메이커를 참조해 만들어진다.
                #    같은 야구여도 MLB 는 해외 커버가 두껍고 NPB·KBO 는 얇다.
                #    시장이 허술한 곳을 찾으려면 리그 단위로 봐야 한다.
                "year": d["year"], "sport": d["sport"], "league": d["league"],
                "market": f"{d['market_family']}({int(d['n_way'])}-way)",
                "p_model": p_model, "p_proto": p_proto, "odds": o,
                # ⚠️ 선택지 위치가 없으면 '승①패의 1점차'처럼 **마켓 안의 특정
                #    선택지**를 못 집는다. Q0 의 최대 이상점(+6.46%p)이 바로
                #    그 형태였는데 마켓 단위로만 보면 +0.70% 로 희석된다.
                "sel_idx": i, "n_way": int(d["n_way"]),
                "won": 1.0 if i == wi else 0.0})

    L = pd.DataFrame(rows)
    te = L[L["year"] > TRAIN_END]
    print(f"선택지 {len(L):,} · 검증({TRAIN_END+1}~) {len(te):,}\n")

    print(f"{'마켓':<18}{'n':>9}{'모델 Brier':>12}{'프로토 Brier':>13}"
          f"{'차이':>10}  판정")
    print("-" * 74)
    out = []
    for mk, s in te.groupby("market"):
        if len(s) < 500:
            continue
        bm = float(np.mean((s["p_model"] - s["won"]) ** 2))
        bp = float(np.mean((s["p_proto"] - s["won"]) ** 2))
        out.append((mk, len(s), bm, bp))
    for mk, n, bm, bp in sorted(out, key=lambda x: x[2] - x[3]):
        v = "✅ 모델 우위" if bm < bp else "❌ 프로토 우위"
        print(f"{mk:<18}{n:>9,}{bm:>12.5f}{bp:>13.5f}{bm-bp:>+10.5f}  {v}")

    # 종목 × 마켓
    print(f"\n{'종목':<6}{'마켓':<18}{'n':>8}{'모델':>10}{'프로토':>10}{'차이':>10}")
    print("-" * 64)
    SP = {"bs": "야구", "sc": "축구", "bk": "농구", "vl": "배구"}
    for (sp, mk), s in te.groupby(["sport", "market"]):
        if len(s) < 400:
            continue
        bm = float(np.mean((s["p_model"] - s["won"]) ** 2))
        bp = float(np.mean((s["p_proto"] - s["won"]) ** 2))
        flag = " ⭐" if bm < bp else ""
        print(f"{SP.get(sp, sp):<6}{mk:<18}{len(s):>8,}{bm:>10.5f}"
              f"{bp:>10.5f}{bm-bp:>+10.5f}{flag}")

    L.to_csv(PROC / "market_scan.csv", index=False)
    print(f"\n저장: {PROC / 'market_scan.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
