"""xFIP · 불펜 · 박빙 정교화 — 야구에서 통한 방향을 더 판다.

왜 야구를 더 파는가
-------------------
FIP 가 자책률을 1.7배 앞섰고 시장 격차를 1/3.6 까지 줄였다(`과정지표.md`).
축구는 xG 가 막혀 유효슈팅으로 대신했는데 판정 보류다.
**통한 곳을 더 파는 게 수익률이 높다.**

세 가지를 한 번에 검증한다.

① xFIP — FIP 에서 남은 잡음 제거
   `pitcher_fip.py` 에서 **HR/9 만 탈락**했다(z=1.89). 홈런은 표본이 작아 잡음이 크다.
   xFIP 는 실제 피홈런 대신 **뜬공 × 리그평균 HR/FB** 를 쓴다.
   우리에겐 뜬공 데이터가 없으므로, **투수의 최근 HR 을 리그평균으로 축소(shrink)** 해 근사한다.

       HR_적용 = w·HR_실제 + (1−w)·HR_리그평균×IP
       w = IP / (IP + k)      ← 표본이 작을수록 리그평균 쪽으로

② 불펜 — 선발 '평균 이닝'이 가장 강했던 이유
   이닝이 짧으면 불펜이 더 던진다. 그 불펜이 얼마나 좋은지·지쳤는지가 승패를 가른다.
   박스스코어에 **선발 외 전 투수**가 있으므로 팀 불펜 FIP 와 최근 소모 이닝을 만든다.

③ 박빙 정교화 — 판단이 안 서는 구간에서 무엇이 갈리는가
   `마켓선택.md`: 박빙(45~55%)은 어느 마켓이든 −13% 이하였다.
   "피하라"로 끝내지 않고 **박빙 구간에서만 어떤 변수가 유의한지** 따로 잰다.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import build_features                    # noqa: E402
from matches import load_matches                       # noqa: E402
from pitcher_er import _inn                            # noqa: E402
from variable_impact import _brier, _fit, _se          # noqa: E402

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
DETAIL = RAW / "detail" / "kbo_baseball_2023_2026.json"
PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
TRAIN_END = 2024
WINDOW = 12
MIN_IP = 15.0
SHRINK_K = 40.0        # HR 축소 강도 — 이 이닝만큼 리그평균을 섞는다
PEN_DAYS = 3           # 불펜 소모도 집계 기간


def load_full() -> pd.DataFrame:
    """선발뿐 아니라 **전 투수**를 보관한다 (불펜 지표용)."""
    raw = json.loads(DETAIL.read_text(encoding="utf-8"))
    rows = []
    for g in raw.values():
        d = g.get("data") or {}
        h, a = d.get("home") or [], d.get("away") or []
        if not h or not a:
            continue
        rows.append({"date": pd.to_datetime(g.get("date")),
                     "home_team": g.get("home"), "away_team": g.get("away"),
                     "home_all": h, "away_all": a,
                     "home_sp": h[0], "away_sp": a[0]})
    return pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def agg(ps: list[dict]) -> dict:
    o = {"ip": 0.0, "er": 0.0, "hr": 0.0, "bb": 0.0, "kk": 0.0}
    for p in ps:
        o["ip"] += _inn(p.get("inn"))
        for k, f in (("er", "er"), ("hr", "hr"), ("bb", "bb"), ("kk", "kk")):
            o[k] += float(p.get(f) or 0)
    return o


def build(df: pd.DataFrame, fip_c: float, lg_hr9: float) -> pd.DataFrame:
    sp: dict = defaultdict(lambda: deque(maxlen=WINDOW))     # 선발별
    pen: dict = defaultdict(lambda: deque(maxlen=WINDOW))    # 팀 불펜
    pen_days: dict = defaultdict(list)                       # (팀) → [(날짜, 이닝)]
    rows = []

    def sp_stat(p):
        v = list(sp[p])
        ip = sum(x["ip"] for x in v)
        if ip < MIN_IP:
            return None
        hr, bb, kk = (sum(x[k] for x in v) for k in ("hr", "bb", "kk"))
        # ⭐ xFIP 근사 — 표본이 작을수록 홈런을 리그평균으로 끌어당긴다
        w = ip / (ip + SHRINK_K)
        hr_adj = w * hr + (1 - w) * (lg_hr9 * ip / 9)
        return {
            "fip": (13 * hr + 3 * bb - 2 * kk) / ip + fip_c,
            "xfip": (13 * hr_adj + 3 * bb - 2 * kk) / ip + fip_c,
            "ip": ip / len(v),
        }

    def pen_stat(t, d):
        v = list(pen[t])
        ip = sum(x["ip"] for x in v)
        if ip < 10:
            return None
        hr, bb, kk = (sum(x[k] for x in v) for k in ("hr", "bb", "kk"))
        recent = sum(i for dt, i in pen_days[t] if (d - dt).days <= PEN_DAYS)
        return {"fip": (13 * hr + 3 * bb - 2 * kk) / ip + fip_c, "load": recent}

    for r in df.itertuples():
        hp, ap = r.home_sp.get("name"), r.away_sp.get("name")
        sh, sa = sp_stat(hp), sp_stat(ap)
        ph, pa = pen_stat(r.home_team, r.date), pen_stat(r.away_team, r.date)
        out = {"date": r.date, "home_team": r.home_team, "away_team": r.away_team}
        out["fip_diff"] = (sa["fip"] - sh["fip"]) if sh and sa else np.nan
        out["xfip_diff"] = (sa["xfip"] - sh["xfip"]) if sh and sa else np.nan
        out["ip_diff"] = (sh["ip"] - sa["ip"]) if sh and sa else np.nan
        out["pen_fip_diff"] = (pa["fip"] - ph["fip"]) if ph and pa else np.nan
        out["pen_load_diff"] = (pa["load"] - ph["load"]) if ph and pa else np.nan
        rows.append(out)

        for name, one, allp, team in ((hp, r.home_sp, r.home_all, r.home_team),
                                      (ap, r.away_sp, r.away_all, r.away_team)):
            sp[name].append({"ip": _inn(one.get("inn")),
                             "er": float(one.get("er") or 0),
                             "hr": float(one.get("hr") or 0),
                             "bb": float(one.get("bb") or 0),
                             "kk": float(one.get("kk") or 0)})
            b = agg(allp[1:])          # 선발 제외 = 불펜
            pen[team].append(b)
            pen_days[team].append((r.date, b["ip"]))
            pen_days[team] = [(d, i) for d, i in pen_days[team]
                              if (r.date - d).days <= PEN_DAYS]
    return pd.DataFrame(rows)


FEATS = ["fip_diff", "xfip_diff", "ip_diff", "pen_fip_diff", "pen_load_diff"]
LABELS = {"fip_diff": "선발 FIP 차", "xfip_diff": "선발 xFIP 차 ⭐ (홈런 축소)",
          "ip_diff": "선발 평균 이닝 차", "pen_fip_diff": "불펜 FIP 차 ⭐",
          "pen_load_diff": f"불펜 소모도 차 ⭐ (최근 {PEN_DAYS}일 이닝)"}


def main() -> int:
    df = load_full()
    tr_d = df[df["date"] < f"{TRAIN_END+1}-01-01"]
    t = {"ip": 0.0, "er": 0.0, "hr": 0.0, "bb": 0.0, "kk": 0.0}
    for r in tr_d.itertuples():
        for p in (r.home_sp, r.away_sp):
            t["ip"] += _inn(p.get("inn"))
            for k in ("er", "hr", "bb", "kk"):
                t[k] += float(p.get(k) or 0)
    fip_c = t["er"] / t["ip"] * 9 - (13 * t["hr"] + 3 * t["bb"] - 2 * t["kk"]) / t["ip"]
    lg_hr9 = t["hr"] / t["ip"] * 9
    print(f"학습 구간 · FIP 상수 {fip_c:.3f} · 리그 HR/9 {lg_hr9:.3f}")

    pf = build(df, fip_c, lg_hr9)
    m = load_matches()
    fe = build_features(m)
    kbo = fe[(fe["league"] == "KBO") & (fe["outcome"] != 0.5)].copy()
    kbo["date"] = pd.to_datetime(kbo["date"])
    tmap = json.loads((PROC / "team_map.json").read_text(encoding="utf-8")).get("KBO", {})
    for c in ("home_team", "away_team"):
        kbo[c] = kbo[c].map(lambda x: tmap.get(x, x))

    d = kbo.merge(pf, on=["date", "home_team", "away_team"], how="inner")
    d = d.dropna(subset=["elo_diff"])
    tr, te = d[d["year"] <= TRAIN_END], d[d["year"] > TRAIN_END]
    print(f"결합 {len(d):,} · 학습 {len(tr):,} / 검증 {len(te):,}\n")

    def mk(x, cols):
        return np.column_stack([np.ones(len(x))]
                               + [x[c].to_numpy(float) for c in cols])

    def run(tr, te, title):
        y_tr = (tr["outcome"] == 1.0).to_numpy(float)
        y_te = (te["outcome"] == 1.0).to_numpy(float)
        b0 = _fit(mk(tr, ["elo_diff"]), y_tr)
        base = _brier(mk(te, ["elo_diff"]), b0, y_te)
        print(f"── {title} · 학습 {len(tr):,}/검증 {len(te):,} · "
              f"기준 Brier {base:.5f}")
        print(f"{'피처':<28}{'n':>7}{'계수':>10}{'z':>8}{'개선':>11}")
        got = {}
        for f in FEATS:
            t2, v2 = tr.dropna(subset=[f]), te.dropna(subset=[f])
            if len(t2) < 200 or len(v2) < 100:
                continue
            yt = (t2["outcome"] == 1.0).to_numpy(float)
            yv = (v2["outcome"] == 1.0).to_numpy(float)
            b = _fit(mk(t2, ["elo_diff", f]), yt)
            z = b[2] / _se(mk(t2, ["elo_diff", f]), b)[2]
            b_ref = _fit(mk(t2, ["elo_diff"]), yt)
            imp = _brier(mk(v2, ["elo_diff"]), b_ref, yv) - \
                _brier(mk(v2, ["elo_diff", f]), b, yv)
            got[f] = imp
            print(f"{LABELS[f]:<28}{len(t2):>7,}{b[2]:>10.4f}{z:>8.2f}{imp:>+11.5f}")
        return got

    all_s = run(tr, te, "전체")

    if "fip_diff" in all_s and "xfip_diff" in all_s:
        print(f"\n⭐ FIP vs xFIP: {all_s['fip_diff']:+.5f} vs {all_s['xfip_diff']:+.5f}"
              f"  → {'xFIP' if all_s['xfip_diff'] > all_s['fip_diff'] else 'FIP'} 우위")

    # ---- 박빙 구간만
    print()
    from score_dist import joint, p_win
    lam = pd.read_csv(PROC / "lambdas.csv", parse_dates=["date"])
    lam = lam[lam["league"] == "KBO"].copy()
    for c in ("home_team", "away_team"):
        lam[c] = lam[c].map(lambda x: tmap.get(x, x))
    d2 = d.merge(lam[["date", "home_team", "away_team", "lam_home", "lam_away"]],
                 on=["date", "home_team", "away_team"], how="inner")
    ph = []
    for r in d2.itertuples():
        h, _, a = p_win(joint(r.lam_home, r.lam_away, "bs"))
        ph.append(h / (h + a) if h + a > 0 else .5)
    d2["p_home"] = ph
    close = d2[(d2["p_home"] >= 0.45) & (d2["p_home"] <= 0.55)]
    ctr, cte = close[close["year"] <= TRAIN_END], close[close["year"] > TRAIN_END]
    if len(ctr) >= 200 and len(cte) >= 100:
        run(ctr, cte, "박빙 구간(45~55%)만")
    else:
        print(f"── 박빙 구간 표본 부족 (학습 {len(ctr)} / 검증 {len(cte)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
