"""타선의 과정 지표 — 투수에서 통한 논리를 타자에 적용한다.

왜
--
투수는 정교하다. ERA(결과) → FIP(과정) → xFIP(과정+잡음축소) 로 가면서
Brier 개선이 +0.00257 → +0.00432 → +0.00597 로 2.3배가 됐다.

그런데 **타선은 아직 '팀 득점 평균'뿐이다.** 그건 ERA 를 쓰던 것과 같은
실수다 — 결과 지표이고 운이 크게 섞인다.

무엇을 재나
-----------
결과 지표   팀 득점            ← 지금 쓰는 것
과정 지표   wOBA · K% · BB% · ISO
운 지표     BABIP = (안타−홈런)/(타수−삼진−홈런)

야구의 BABIP 는 축구의 '득점−xG' 에 해당하는 대표적 운 지표다.
(축구에서는 그게 죽었다 — `xG관문.md`. 야구에서는 어떤지 본다.)

⚠️ 규율 — walk-forward. 각 경기 피처는 그 경기 **이전** 기록만.
   학습/검증도 시간 분리. 박빙 구간은 **시장 아닌 모델 확률**이 아니라
   `pitcher_xfip.py` 와 같은 기준(스코어 모델 p_home)을 써서 비교 가능하게 둔다.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detail_paths import latest_detail_path                # noqa: E402
from features import build_features                     # noqa: E402
from matches import load_matches                        # noqa: E402
from variable_impact import _brier, _fit, _se           # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def detail_path() -> Path:
    return latest_detail_path("kbo", "batters")


# Compatibility snapshot only; loaders resolve the path again at call time.
DETAIL = detail_path()
PROC = ROOT / "data" / "processed"
TRAIN_END = 2024
WINDOW = 15            # 타선은 투수보다 표본이 빨리 쌓인다(매 경기 전원 출전)
MIN_GAMES = 8

# wOBA 가중치 — FanGraphs 표준값. HBP·SF 는 박스스코어에 없어 제외했다.
# 절대 수준이 아니라 팀 간 비교라서 상수 배율은 문제되지 않는다.
W = {"bb": 0.69, "s": 0.89, "d": 1.27, "t": 1.62, "hr": 2.10}


def load(path: Path | None = None) -> pd.DataFrame:
    path = detail_path() if path is None else path
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for g in raw.values():
        d = g.get("data") or {}
        h, a = d.get("home"), d.get("away")
        if not h or not a:
            continue
        rows.append({
            "date": pd.to_datetime(g["date"]), "stadium": d.get("stadium"),
            "home_team": g.get("home"), "away_team": g.get("away"),
            "home_score": g.get("home_score"), "away_score": g.get("away_score"),
            **{f"h_{k}": v for k, v in h.items()},
            **{f"a_{k}": v for k, v in a.items()},
        })
    return pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date")


def _rates(v: list[dict]) -> dict:
    """누적합에서 비율을 낸다 — 경기별 비율의 평균이 아니라 합계 기준.

    경기별로 내서 평균 내면 타석이 적은 경기가 과대 반영된다.
    """
    tot = {k: sum(x[k] for x in v) for k in v[0]}
    ab, bb, kk = tot["ab"], tot["bb"], tot["kk"]
    pa = ab + bb
    if pa <= 0 or ab <= 0:
        return {}
    s = tot["s"]
    woba_num = (W["bb"] * bb + W["s"] * s + W["d"] * tot["d"]
                + W["t"] * tot["t"] + W["hr"] * tot["hr_parsed"])
    bip = ab - kk - tot["hr_parsed"]
    return {
        "runs": tot["run"] / len(v),
        "woba": woba_num / pa,
        "k_rate": kk / pa,
        "bb_rate": bb / pa,
        "iso": (tot["d"] + 2 * tot["t"] + 3 * tot["hr_parsed"]) / ab,
        "babip": (tot["hit"] - tot["hr_parsed"]) / bip if bip > 0 else np.nan,
    }


def build_form(d: pd.DataFrame) -> pd.DataFrame:
    hist: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    keys = ("ab", "bb", "kk", "hit", "s", "d", "t", "hr_parsed", "run")
    rows = []

    def form(t):
        v = list(hist[t])
        return _rates(v) if len(v) >= MIN_GAMES else None

    for r in d.itertuples():
        h, a = form(r.home_team), form(r.away_team)
        out = {"date": r.date, "home_team": r.home_team, "away_team": r.away_team}
        if h and a:
            out["run_diff"] = h["runs"] - a["runs"]
            out["woba_diff"] = h["woba"] - a["woba"]
            out["k_diff"] = a["k_rate"] - h["k_rate"]     # 덜 삼진당하면 유리
            out["bb_diff"] = h["bb_rate"] - a["bb_rate"]
            out["iso_diff"] = h["iso"] - a["iso"]
            out["babip_diff"] = h["babip"] - a["babip"]
        else:
            for k in FEATS:
                out[k] = np.nan
        rows.append(out)

        for team, pre in ((r.home_team, "h_"), (r.away_team, "a_")):
            hist[team].append({k: int(getattr(r, pre + k) or 0) for k in keys})
    return pd.DataFrame(rows)


FEATS = ["run_diff", "woba_diff", "k_diff", "bb_diff", "iso_diff", "babip_diff"]
LABELS = {
    "run_diff": "팀 득점 차 (결과 지표)",
    "woba_diff": "wOBA 차 ⭐ 과정 지표",
    "k_diff": "삼진율 차 (과정·안정)",
    "bb_diff": "볼넷율 차 (과정·안정)",
    "iso_diff": "순장타율 차 (과정)",
    "babip_diff": "BABIP 차 (운 지표)",
}


def main() -> int:
    path = detail_path()
    if not path.exists():
        print(f"{path} 없음 — python src/game_detail.py batters kbo 2023 "
              f"{path.stem.rsplit('_', 1)[-1]}")
        return 1
    d = load(path)
    print(f"타자 박스스코어 {len(d):,}경기 "
          f"({d['date'].min().date()} ~ {d['date'].max().date()})")

    ff = build_form(d)
    m = load_matches()
    fe = build_features(m)
    kbo = fe[(fe["league"] == "KBO") & (fe["outcome"] != 0.5)].copy()
    kbo["date"] = pd.to_datetime(kbo["date"])
    tmap = json.loads((PROC / "team_map.json").read_text(encoding="utf-8")).get("KBO", {})
    for c in ("home_team", "away_team"):
        kbo[c] = kbo[c].map(lambda x: tmap.get(x, x))
        ff[c] = ff[c].map(lambda x: tmap.get(x, x))

    dd = kbo.merge(ff, on=["date", "home_team", "away_team"], how="inner")
    dd = dd.dropna(subset=["elo_diff"])
    tr, te = dd[dd["year"] <= TRAIN_END], dd[dd["year"] > TRAIN_END]
    print(f"프로토 결합 {len(dd):,} · 학습 {len(tr):,} / 검증 {len(te):,}\n")
    if len(tr) < 300 or len(te) < 150:
        print("표본 부족 — 판정 불가")
        return 1

    def mk(x, cols):
        return np.column_stack([np.ones(len(x))]
                               + [x[c].to_numpy(float) for c in cols])

    y_tr = (tr["outcome"] == 1.0).to_numpy(float)
    y_te = (te["outcome"] == 1.0).to_numpy(float)
    b0 = _fit(mk(tr, ["elo_diff"]), y_tr)
    base = _brier(mk(te, ["elo_diff"]), b0, y_te)
    print(f"기준 (Elo 단독) 검증 Brier = {base:.5f}\n")
    print(f"{'피처':<28}{'n':>7}{'계수':>10}{'z':>8}{'Brier':>10}{'개선':>11}")
    print("-" * 74)

    got = {}
    for f in FEATS:
        t2, v2 = tr.dropna(subset=[f]), te.dropna(subset=[f])
        if len(t2) < 250 or len(v2) < 120:
            print(f"{LABELS[f]:<28} 표본 부족")
            continue
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        b = _fit(mk(t2, ["elo_diff", f]), yt)
        z = b[2] / _se(mk(t2, ["elo_diff", f]), b)[2]
        b_ref = _fit(mk(t2, ["elo_diff"]), yt)
        imp = (_brier(mk(v2, ["elo_diff"]), b_ref, yv)
               - _brier(mk(v2, ["elo_diff", f]), b, yv))
        got[f] = imp
        print(f"{LABELS[f]:<28}{len(t2):>7,}{b[2]:>10.4f}{z:>8.2f}"
              f"{_brier(mk(v2, ['elo_diff', f]), b, yv):>10.5f}{imp:>+11.5f}")

    if "run_diff" in got and "woba_diff" in got:
        print(f"\n⭐ 결과 vs 과정 정면 비교 (투수 FIP 검증과 같은 방식)")
        print(f"   팀 득점 차 (결과) {got['run_diff']:+.5f}")
        print(f"   wOBA 차   (과정) {got['woba_diff']:+.5f}")
        w = "과정" if got["woba_diff"] > got["run_diff"] else "결과"
        print(f"   → {w} 지표 우위")

    # ---- 같이 쓰면? (xG관문.md 에서 단독 비교만으론 못 가른다는 걸 배웠다)
    print("\n" + "=" * 74)
    print("⭐ 같이 쓰면 — 과정 지표가 팀 득점 위에 정보를 더하는가")
    sub_tr = tr.dropna(subset=FEATS)
    sub_te = te.dropna(subset=FEATS)
    yt = (sub_tr["outcome"] == 1.0).to_numpy(float)
    yv = (sub_te["outcome"] == 1.0).to_numpy(float)
    combos = [
        (["elo_diff", "run_diff"], "Elo + 팀 득점"),
        (["elo_diff", "woba_diff"], "Elo + wOBA"),
        (["elo_diff", "run_diff", "woba_diff"], "Elo + 팀 득점 + wOBA"),
        (["elo_diff", "run_diff", "woba_diff", "babip_diff"], "  + BABIP(운)"),
        (["elo_diff", "woba_diff", "k_diff", "bb_diff", "iso_diff"], "Elo + 과정 전부"),
    ]
    ref = None
    print(f"  {'모델':<30}{'Brier':>10}{'팀득점대비':>12}")
    for cols, name in combos:
        b = _fit(mk(sub_tr, cols), yt)
        if b is None:
            continue
        br = _brier(mk(sub_te, cols), b, yv)
        if name == "Elo + 팀 득점":
            ref = br
        gap = f"{ref - br:>+12.5f}" if ref is not None and name != "Elo + 팀 득점" else ""
        print(f"  {name:<30}{br:>10.5f}{gap}")
        if len(cols) > 2:
            se = _se(mk(sub_tr, cols), b)
            print("      " + "  ".join(
                f"{c}: z={b[i+1]/se[i+1]:.2f}" for i, c in enumerate(cols)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
