"""λ 에 선발투수를 반영한다 — 언더오버와 선발투수를 잇는 지점.

왜 이 둘이 연결되는가
----------------------
`market_scan.py`: **언더오버가 모델·프로토 둘 다 가장 어려워하는 시장**이다
(모델 −프로토 = +0.0324, 승패의 2.3배). 개선 여지가 가장 크다.

`pitcher_er.py`: 선발 변수 중 **'평균 이닝'이 가장 강했다**(Brier +0.00613).
자책률(+0.00301)보다 강하다는 게 힌트다 —
**선발이 일찍 내려가면 불펜이 더 던지고, 그게 총득점을 올린다.**

즉 선발 이닝은 승패보다 **총득점(언더오버)에 더 직접적**일 수 있다.
지금 λ 는 팀 최근 득실 이동평균뿐이라 그걸 전혀 못 본다.

방법
----
학습 구간에서 회귀:

    실제 실점 ~ a + b·λ_base + c·(상대 선발 자책률) + d·(상대 선발 이닝)

적합된 계수로 λ 를 보정한 뒤, 언더오버·승패 Brier 가 실제로 좋아지는지 검증한다.

⚠️ 계수는 **학습 구간에서만** 적합한다. 개선이 없으면 채택하지 않는다.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pitcher_er import _inn, load_detail                 # noqa: E402
from score_dist import joint, p_over, p_win              # noqa: E402

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
TEAM_MAP = PROC / "team_map.json"
TRAIN_END = 2024
WINDOW = 12
MIN_START = 4
_LINE = re.compile(r"([-+]?\d+\.?\d*)")


def starter_form(df: pd.DataFrame) -> pd.DataFrame:
    """경기별 양 팀 선발의 최근 성적 (walk-forward)."""
    hist: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    rows = []

    def stat(p):
        v = list(hist[p])
        if len(v) < MIN_START:
            return None
        ip = sum(x["ip"] for x in v)
        if ip < 5:
            return None
        return {"era9": sum(x["er"] for x in v) / ip * 9, "ip": ip / len(v)}

    for r in df.itertuples():
        hp, ap = r.home_sp.get("name"), r.away_sp.get("name")
        sh, sa = stat(hp), stat(ap)
        rows.append({
            "date": r.date, "home_team": r.home_team, "away_team": r.away_team,
            "h_era": sh["era9"] if sh else np.nan,
            "h_ip": sh["ip"] if sh else np.nan,
            "a_era": sa["era9"] if sa else np.nan,
            "a_ip": sa["ip"] if sa else np.nan})
        for name, p in ((hp, r.home_sp), (ap, r.away_sp)):
            hist[name].append({"ip": _inn(p.get("inn")),
                               "er": float(p.get("er") or 0)})
    return pd.DataFrame(rows)


def main() -> int:
    lam = pd.read_csv(PROC / "lambdas.csv", parse_dates=["date"])
    lam = lam[lam["league"] == "KBO"]
    det = load_detail()
    sf = starter_form(det)

    tmap = json.loads(TEAM_MAP.read_text(encoding="utf-8")).get("KBO", {}) \
        if TEAM_MAP.exists() else {}
    lam["home_team"] = lam["home_team"].map(lambda x: tmap.get(x, x))
    lam["away_team"] = lam["away_team"].map(lambda x: tmap.get(x, x))

    d = lam.merge(sf, on=["date", "home_team", "away_team"], how="inner")
    d = d.dropna(subset=["lam_home", "lam_away", "h_era", "a_era", "h_ip", "a_ip"])
    tr, te = d[d["year"] <= TRAIN_END], d[d["year"] > TRAIN_END]
    print(f"KBO λ × 선발 결합 {len(d):,}건 · 학습 {len(tr):,} / 검증 {len(te):,}\n")
    if len(tr) < 300 or len(te) < 200:
        print("표본 부족")
        return 1

    def ols(X, y):
        return np.linalg.lstsq(X, y, rcond=None)[0]

    # 홈 득점 = f(λ_home, 원정 선발 자책률, 원정 선발 이닝)
    # 원정 득점 = f(λ_away, 홈 선발 자책률, 홈 선발 이닝)
    def design(x, side):
        opp = "a" if side == "home" else "h"
        return np.column_stack([np.ones(len(x)), x[f"lam_{side}"],
                                x[f"{opp}_era"], x[f"{opp}_ip"]])

    bh = ols(design(tr, "home"), tr["home_score"].to_numpy(float))
    ba = ols(design(tr, "away"), tr["away_score"].to_numpy(float))
    print("보정 계수 (실점 ~ λ + 상대선발 자책률 + 상대선발 이닝)")
    for lbl, b in (("홈 득점", bh), ("원정 득점", ba)):
        print(f"  {lbl}: 절편 {b[0]:+.3f} · λ {b[1]:+.3f} · 자책률 {b[2]:+.4f} "
              f"· 이닝 {b[3]:+.4f}")

    te = te.copy()
    te["lam_home_adj"] = np.clip(design(te, "home") @ bh, 0.5, None)
    te["lam_away_adj"] = np.clip(design(te, "away") @ ba, 0.5, None)

    print(f"\n총득점 예측 정확도 (검증 {len(te):,}경기)")
    act = te["home_score"] + te["away_score"]
    for lbl, cols in (("기존 λ", ("lam_home", "lam_away")),
                      ("선발 반영 λ", ("lam_home_adj", "lam_away_adj"))):
        pred = te[cols[0]] + te[cols[1]]
        print(f"  {lbl:<12} MAE {np.mean(np.abs(pred-act)):.4f} · "
              f"평균 {pred.mean():.2f} (실제 {act.mean():.2f})")

    # ---- 마켓 확률로 검증
    g = pd.read_csv(PROC / "games.csv")
    g = g[(~g["is_void"].astype(bool)) & (g["league"] == "KBO")]
    md = g["date_text"].astype(str).str.extract(r"(\d{2})\.(\d{2})")
    g = g.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                 _dd=pd.to_numeric(md[1], errors="coerce")).dropna(subset=["_mm", "_dd"])
    g["date"] = pd.to_datetime(dict(year=g["year"], month=g["_mm"].astype(int),
                                    day=g["_dd"].astype(int)), errors="coerce")
    g["home_team"] = g["home"].map(lambda x: re.sub(r"\s+-?\d+\s*$", "", str(x)).strip())
    g["away_team"] = g["away"].map(lambda x: re.sub(r"^\s*-?\d+\s+", "", str(x)).strip())
    g["home_team"] = g["home_team"].map(lambda x: tmap.get(x, x))
    g["away_team"] = g["away_team"].map(lambda x: tmap.get(x, x))

    mk = g.merge(te[["date", "home_team", "away_team", "lam_home", "lam_away",
                     "lam_home_adj", "lam_away_adj"]],
                 on=["date", "home_team", "away_team"], how="inner")
    print(f"\n마켓 검증 결합 {len(mk):,}행")

    res = defaultdict(lambda: {"base": [], "adj": [], "proto": [], "y": []})
    for r in mk.itertuples():
        odds = [float(x) for x in str(r.odds).split(",") if x]
        if len(odds) != int(r.n_way) or any(o <= 1 for o in odds):
            continue
        fam, nw = r.market_family, int(r.n_way)
        if fam == "언더오버" and nw == 2:
            m0 = _LINE.search(str(r.market_label))
            if not m0:
                continue
            line = float(m0.group(1))
            wi = {"언더": 0, "오버": 1}.get(r.result)
            if wi is None:
                continue
            pb = p_over(joint(r.lam_home, r.lam_away, "bs"), line)
            pa_ = p_over(joint(r.lam_home_adj, r.lam_away_adj, "bs"), line)
            vec_b, vec_a = [1 - pb, pb], [1 - pa_, pa_]
        elif fam == "승패" and nw == 2:
            wi = {"홈승": 0, "홈패": 1}.get(r.result)
            if wi is None:
                continue
            h0, _, a0 = p_win(joint(r.lam_home, r.lam_away, "bs"))
            h1, _, a1 = p_win(joint(r.lam_home_adj, r.lam_away_adj, "bs"))
            vec_b = [h0 / (h0 + a0), a0 / (h0 + a0)]
            vec_a = [h1 / (h1 + a1), a1 / (h1 + a1)]
        else:
            continue
        ov = sum(1 / o for o in odds)
        for i in range(nw):
            k = f"{fam}({nw}-way)"
            res[k]["base"].append(vec_b[i])
            res[k]["adj"].append(vec_a[i])
            res[k]["proto"].append((1 / odds[i]) / ov)
            res[k]["y"].append(1.0 if i == wi else 0.0)

    print(f"\n{'마켓':<18}{'n':>8}{'기존 λ':>11}{'선발 반영':>11}"
          f"{'프로토':>11}{'개선':>10}  판정")
    print("-" * 74)
    for k, v in res.items():
        y = np.array(v["y"])
        if len(y) < 400:
            continue
        b0 = float(np.mean((np.array(v["base"]) - y) ** 2))
        b1 = float(np.mean((np.array(v["adj"]) - y) ** 2))
        bp = float(np.mean((np.array(v["proto"]) - y) ** 2))
        ok = b1 < b0
        print(f"{k:<18}{len(y):>8,}{b0:>11.5f}{b1:>11.5f}{bp:>11.5f}"
              f"{b0-b1:>+10.5f}  {'✅ 개선' if ok else '❌'}"
              + ("  ⭐프로토 추월" if b1 < bp else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
