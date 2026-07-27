"""선발투수 재검증 — 대리지표(팀 실점)를 **자책점**으로 교체.

왜 다시 하는가
--------------
`findings/선발투수.md` 에서 선발 효과가 3개 리그에서 재현되지 않았다. 그런데 그때 쓴 지표는
**"그 투수 등판 경기에서 팀이 내준 점수"** 였고, 거기엔 **불펜 실점이 통째로 섞인다.**
선발이 6이닝 1실점하고 불펜이 5실점해도 6실점으로 기록됐다.

이제 박스스코어에서 **투수 개인 자책점·이닝**을 확보했으므로(2,884경기) 공정하게 다시 묻는다.

피처 (walk-forward, 최근 등판만)
    p_era9    (자책점 합 / 이닝 합) × 9      — 자책점 기준 방어율
    p_ip      평균 이닝                       — 얼마나 길게 끌어주는가
    p_k9      (삼진 / 이닝) × 9
    p_whip    (안타 + 볼넷) / 이닝

    각각 (원정 선발 − 홈 선발) 또는 그 반대로 홈 유리 방향을 맞춘다.

⚠️ 선발 판별: 박스스코어의 **첫 번째 투수**가 선발이라고 가정하고,
   `kbo_starters.json` 의 예고 선발명과 대조해 검증한다.
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
from variable_impact import _brier, _fit, _se          # noqa: E402

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
DETAIL = RAW / "detail" / "kbo_baseball_2023_2026.json"
TEAM_MAP = Path(__file__).resolve().parent.parent / "data" / "processed" / "team_map.json"
TRAIN_END = 2024
WINDOW = 12
MIN_START = 4


def _inn(x) -> float:
    """'6' 또는 '5 1/3' 형태 → 이닝(float)."""
    s = str(x or "").strip()
    if not s:
        return 0.0
    tot = 0.0
    for part in s.split():
        if "/" in part:
            a, b = part.split("/")
            try:
                tot += float(a) / float(b)
            except (ValueError, ZeroDivisionError):
                pass
        else:
            try:
                tot += float(part)
            except ValueError:
                pass
    return tot


def load_detail() -> pd.DataFrame:
    raw = json.loads(DETAIL.read_text(encoding="utf-8"))
    rows = []
    for g in raw.values():
        d = g.get("data") or {}
        h, a = d.get("home") or [], d.get("away") or []
        if not h or not a:
            continue
        rows.append({
            "gameId": g["gameId"], "date": pd.to_datetime(g.get("date")),
            "home_team": g.get("home"), "away_team": g.get("away"),
            "home_score": g.get("home_score"), "away_score": g.get("away_score"),
            # 박스스코어 첫 투수 = 선발 (아래에서 검증)
            "home_sp": h[0], "away_sp": a[0],
        })
    return pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def verify_starter(df: pd.DataFrame) -> None:
    """'첫 투수 = 선발' 가정을 예고 선발명과 대조."""
    p = RAW / "kbo_starters.json"
    if not p.exists():
        print("  (kbo_starters.json 없음 — 검증 생략)")
        return
    ann = {g["gameId"]: (g.get("home_starter"), g.get("away_starter"))
           for g in json.loads(p.read_text(encoding="utf-8"))}
    hit = tot = 0
    for r in df.itertuples():
        a = ann.get(r.gameId)
        if not a or not a[0]:
            continue
        tot += 1
        if r.home_sp.get("name") == a[0] and r.away_sp.get("name") == a[1]:
            hit += 1
    if tot:
        print(f"  선발 판별 검증: 예고와 일치 {hit:,}/{tot:,} ({hit/tot:.1%})")


def build_pitcher_er(df: pd.DataFrame) -> pd.DataFrame:
    """날짜순 1패스. 각 경기 피처는 **그 경기 이전** 등판만 쓴다."""
    hist: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    rows = []

    def stat(p):
        v = list(hist[p])
        if len(v) < MIN_START:
            return None
        ip = sum(x["ip"] for x in v)
        if ip < 5:
            return None
        return {
            "era9": sum(x["er"] for x in v) / ip * 9,
            "ip": ip / len(v),
            "k9": sum(x["kk"] for x in v) / ip * 9,
            "whip": (sum(x["hit"] for x in v) + sum(x["bb"] for x in v)) / ip,
        }

    for r in df.itertuples():
        hp, ap = r.home_sp.get("name"), r.away_sp.get("name")
        sh, sa = stat(hp), stat(ap)
        out = {"date": r.date, "home_team": r.home_team, "away_team": r.away_team}
        if sh and sa:
            # 홈에 유리한 방향으로 부호를 맞춘다
            out["sp_era_diff"] = sa["era9"] - sh["era9"]      # 원정 선발이 나쁠수록 +
            out["sp_ip_diff"] = sh["ip"] - sa["ip"]
            out["sp_k9_diff"] = sh["k9"] - sa["k9"]
            out["sp_whip_diff"] = sa["whip"] - sh["whip"]
        else:
            for k in ("sp_era_diff", "sp_ip_diff", "sp_k9_diff", "sp_whip_diff"):
                out[k] = np.nan
        rows.append(out)

        for name, p in ((hp, r.home_sp), (ap, r.away_sp)):
            hist[name].append({
                "ip": _inn(p.get("inn")), "er": float(p.get("er") or 0),
                "kk": float(p.get("kk") or 0), "bb": float(p.get("bb") or 0),
                "hit": float(p.get("hit") or 0)})

    return pd.DataFrame(rows)


FEATS = ["sp_era_diff", "sp_ip_diff", "sp_k9_diff", "sp_whip_diff"]
LABELS = {"sp_era_diff": "선발 자책률 차 (원정−홈) ⭐",
          "sp_ip_diff": "선발 평균 이닝 차 (홈−원정)",
          "sp_k9_diff": "선발 K/9 차 (홈−원정)",
          "sp_whip_diff": "선발 WHIP 차 (원정−홈)"}


def main() -> int:
    if not DETAIL.exists():
        print("먼저 python src/game_detail.py baseball kbo 2023 2026")
        return 1
    df = load_detail()
    print(f"KBO 투수 박스스코어 {len(df):,}경기 "
          f"({df['date'].min().date()} ~ {df['date'].max().date()})")
    verify_starter(df)

    pf = build_pitcher_er(df)
    m = load_matches()
    fe = build_features(m)
    kbo = fe[(fe["league"] == "KBO") & (fe["outcome"] != 0.5)].copy()
    kbo["date"] = pd.to_datetime(kbo["date"])
    tmap = json.loads(TEAM_MAP.read_text(encoding="utf-8")).get("KBO", {}) \
        if TEAM_MAP.exists() else {}
    kbo["home_team"] = kbo["home_team"].map(lambda x: tmap.get(x, x))
    kbo["away_team"] = kbo["away_team"].map(lambda x: tmap.get(x, x))

    d = kbo.merge(pf, on=["date", "home_team", "away_team"], how="inner")
    d = d.dropna(subset=["elo_diff"])
    tr, te = d[d["year"] <= TRAIN_END], d[d["year"] > TRAIN_END]
    print(f"프로토 결합 {len(d):,}건 · 학습 {len(tr):,} / 검증 {len(te):,}\n")
    if len(tr) < 300 or len(te) < 200:
        print("표본 부족")
        return 1

    def mk(x, cols):
        return np.column_stack([np.ones(len(x))]
                               + [x[c].to_numpy(float) for c in cols])

    y_tr = (tr["outcome"] == 1.0).to_numpy(float)
    y_te = (te["outcome"] == 1.0).to_numpy(float)
    b0 = _fit(mk(tr, ["elo_diff"]), y_tr)
    print(f"기준 (Elo 단독) 검증 Brier = "
          f"{_brier(mk(te, ['elo_diff']), b0, y_te):.5f}\n")

    print(f"{'피처':<28}{'n':>7}{'계수':>10}{'z':>8}{'Brier':>10}{'개선':>11}  판정")
    print("-" * 78)
    ok = []
    for f in FEATS:
        t2, v2 = tr.dropna(subset=[f]), te.dropna(subset=[f])
        if len(t2) < 300 or len(v2) < 150:
            print(f"{LABELS[f]:<28} 표본 부족 ({len(t2)}/{len(v2)})")
            continue
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        b = _fit(mk(t2, ["elo_diff", f]), yt)
        z = b[2] / _se(mk(t2, ["elo_diff", f]), b)[2]
        b_ref = _fit(mk(t2, ["elo_diff"]), yt)
        ref = _brier(mk(v2, ["elo_diff"]), b_ref, yv)
        br = _brier(mk(v2, ["elo_diff", f]), b, yv)
        good = abs(z) >= 2.58 and (ref - br) > 0
        if good:
            ok.append(f)
        print(f"{LABELS[f]:<28}{len(t2):>7,}{b[2]:>10.4f}{z:>8.2f}"
              f"{br:>10.5f}{ref-br:>+11.5f}  {'✅ 채택' if good else '❌'}")

    if not ok:
        print("\n채택된 투수 피처 없음.")
        return 0

    t2, v2 = tr.dropna(subset=ok), te.dropna(subset=ok)
    yt = (t2["outcome"] == 1.0).to_numpy(float)
    yv = (v2["outcome"] == 1.0).to_numpy(float)
    b = _fit(mk(t2, ["elo_diff"] + ok), yt)
    print(f"\n채택 전부: Brier {_brier(mk(v2, ['elo_diff'] + ok), b, yv):.5f}")

    from model_v2 import attach_odds
    v3 = attach_odds(v2.assign(league="KBO"))
    if len(v3) > 150:
        yv3 = (v3["outcome"] == 1.0).to_numpy(float)
        p = 1 / (1 + np.exp(-np.clip(mk(v3, ["elo_diff"] + ok) @ b, -30, 30)))
        ov = 1 / v3["o_home"] + 1 / v3["o_away"]
        pm = ((1 / v3["o_home"]) / ov).to_numpy(float)
        bm, bk = float(np.mean((p - yv3) ** 2)), float(np.mean((pm - yv3) ** 2))
        print(f"\n⭐ 시장 비교 (검증 {len(v3):,}경기)")
        print(f"   모델(Elo+투수) Brier {bm:.5f}   시장 Brier {bk:.5f}   "
              f"{'✅ 모델 우위' if bm < bk else '❌ 시장 우위'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
