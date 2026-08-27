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
from detail_paths import latest_detail_path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import build_features                    # noqa: E402
from matches import load_matches                       # noqa: E402
from pitcher_er import _inn                            # noqa: E402
from variable_impact import _brier, _fit, _se          # noqa: E402

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def detail_path() -> Path:
    return latest_detail_path("kbo", "baseball")


# Compatibility snapshot only; loaders resolve the path again at call time.
DETAIL = detail_path()
PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
TRAIN_END = 2024
WINDOW = 12
MIN_IP = 15.0
SHRINK_K = 40.0        # HR 축소 강도 — 이 이닝만큼 리그평균을 섞는다
PEN_DAYS = 3           # 불펜 소모도 집계 기간


def pitcher_key(pitcher: dict | None, team: str | None = None) -> str | None:
    """동명이인을 섞지 않도록 원본 선수 ID를 우선하는 내부 키를 만든다."""
    if not isinstance(pitcher, dict):
        return None
    code = pitcher.get("pcode")
    if code not in (None, ""):
        if isinstance(code, float) and code.is_integer():
            code = int(code)
        return f"pcode:{str(code).strip()}"
    name = str(pitcher.get("name") or "").strip()
    if not name:
        return None
    return f"name:{str(team or '').strip()}:{name}"


def load_full(path: Path | None = None) -> pd.DataFrame:
    """선발뿐 아니라 **전 투수**를 보관한다 (불펜 지표용)."""
    path = detail_path() if path is None else path
    raw = json.loads(path.read_text(encoding="utf-8"))
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

    def sp_stat(key):
        v = list(sp[key])
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
        hp = pitcher_key(r.home_sp, r.home_team)
        ap = pitcher_key(r.away_sp, r.away_team)
        sh, sa = sp_stat(hp), sp_stat(ap)
        ph, pa = pen_stat(r.home_team, r.date), pen_stat(r.away_team, r.date)
        out = {"date": r.date, "home_team": r.home_team, "away_team": r.away_team}
        out["fip_diff"] = (sa["fip"] - sh["fip"]) if sh and sa else np.nan
        out["xfip_diff"] = (sa["xfip"] - sh["xfip"]) if sh and sa else np.nan
        out["ip_diff"] = (sh["ip"] - sa["ip"]) if sh and sa else np.nan
        out["pen_fip_diff"] = (pa["fip"] - ph["fip"]) if ph and pa else np.nan
        out["pen_load_diff"] = (pa["load"] - ph["load"]) if ph and pa else np.nan
        rows.append(out)

        for key, one, allp, team in ((hp, r.home_sp, r.home_all, r.home_team),
                                     (ap, r.away_sp, r.away_all, r.away_team)):
            if key:
                sp[key].append({"ip": _inn(one.get("inn")),
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


def build_causal(df: pd.DataFrame) -> pd.DataFrame:
    """경기 시점까지 알려진 값만으로 xFIP 재생 피처를 만든다.

    리그 HR/9와 FIP 상수도 고정된 시즌 전체 값이 아니라 *이전 날짜에
    끝난 경기*의 선발 기록으로 계산한다. 원천 데이터에 경기 시작 시각이
    없으므로 같은 날짜 경기는 서로의 결과를 볼 수 없게 한꺼번에 계산한
    뒤 상태를 갱신한다. 과거 표본이 전혀 없는 초반 경기는 ``NaN``으로
    남겨 선택 규칙이 닫힌 상태(fail closed)가 되게 한다.
    """
    ordered = df.copy()
    ordered["date"] = pd.to_datetime(ordered["date"])
    ordered = ordered.dropna(subset=["date"]).sort_values("date", kind="stable")

    sp: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    pen: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    pen_days: dict = defaultdict(list)
    league = {key: 0.0 for key in ("ip", "er", "hr", "bb", "kk")}
    rows: list[dict] = []

    def league_rates() -> tuple[float, float] | None:
        ip = league["ip"]
        if ip <= 0:
            return None
        fip_c = (league["er"] / ip * 9
                 - (13 * league["hr"] + 3 * league["bb"]
                    - 2 * league["kk"]) / ip)
        return fip_c, league["hr"] / ip * 9

    def sp_stat(key: str | None, rates: tuple[float, float] | None):
        if not key or rates is None:
            return None
        values = list(sp[key])
        ip = sum(item["ip"] for item in values)
        if ip < MIN_IP:
            return None
        hr, bb, kk = (sum(item[key] for item in values)
                      for key in ("hr", "bb", "kk"))
        fip_c, lg_hr9 = rates
        weight = ip / (ip + SHRINK_K)
        hr_adjusted = weight * hr + (1 - weight) * (lg_hr9 * ip / 9)
        return {
            "fip": (13 * hr + 3 * bb - 2 * kk) / ip + fip_c,
            "xfip": (13 * hr_adjusted + 3 * bb - 2 * kk) / ip + fip_c,
            "ip": ip / len(values),
        }

    def pen_stat(team: str, date: pd.Timestamp,
                 rates: tuple[float, float] | None):
        if rates is None:
            return None
        values = list(pen[team])
        ip = sum(item["ip"] for item in values)
        if ip < 10:
            return None
        hr, bb, kk = (sum(item[key] for item in values)
                      for key in ("hr", "bb", "kk"))
        recent = sum(innings for prior_date, innings in pen_days[team]
                     if (date - prior_date).days <= PEN_DAYS)
        fip_c, _ = rates
        return {
            "fip": (13 * hr + 3 * bb - 2 * kk) / ip + fip_c,
            "load": recent,
        }

    for date, same_day in ordered.groupby("date", sort=True):
        # 같은 날짜 안에서는 상태를 갱신하지 않는다. 시작 시각이 없어서
        # 어느 경기가 먼저 끝났는지 확정할 수 없기 때문이다.
        rates = league_rates()
        pending = list(same_day.itertuples())
        for row in pending:
            home_key = pitcher_key(row.home_sp, row.home_team)
            away_key = pitcher_key(row.away_sp, row.away_team)
            home_sp = sp_stat(home_key, rates)
            away_sp = sp_stat(away_key, rates)
            home_pen = pen_stat(row.home_team, date, rates)
            away_pen = pen_stat(row.away_team, date, rates)
            rows.append({
                "date": date,
                "home_team": row.home_team,
                "away_team": row.away_team,
                "fip_diff": ((away_sp["fip"] - home_sp["fip"])
                             if home_sp and away_sp else np.nan),
                "xfip_diff": ((away_sp["xfip"] - home_sp["xfip"])
                              if home_sp and away_sp else np.nan),
                "ip_diff": ((home_sp["ip"] - away_sp["ip"])
                            if home_sp and away_sp else np.nan),
                "pen_fip_diff": ((away_pen["fip"] - home_pen["fip"])
                                 if home_pen and away_pen else np.nan),
                "pen_load_diff": ((away_pen["load"] - home_pen["load"])
                                  if home_pen and away_pen else np.nan),
            })

        # 해당 날짜의 모든 피처를 만든 뒤에만 경기 결과를 과거 상태에 넣는다.
        for row in pending:
            for key, one, all_pitchers, team in (
                    (pitcher_key(row.home_sp, row.home_team), row.home_sp,
                     row.home_all, row.home_team),
                    (pitcher_key(row.away_sp, row.away_team), row.away_sp,
                     row.away_all, row.away_team)):
                starter = {
                    "ip": _inn(one.get("inn")),
                    "er": float(one.get("er") or 0),
                    "hr": float(one.get("hr") or 0),
                    "bb": float(one.get("bb") or 0),
                    "kk": float(one.get("kk") or 0),
                }
                if key:
                    sp[key].append(starter)
                for key in league:
                    league[key] += starter[key]
                bullpen = agg(all_pitchers[1:])
                pen[team].append(bullpen)
                pen_days[team].append((date, bullpen["ip"]))
                pen_days[team] = [
                    (prior_date, innings)
                    for prior_date, innings in pen_days[team]
                    if (date - prior_date).days <= PEN_DAYS
                ]

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
