"""축구 라인업이 승패를 추가로 설명하는가.

왜 이게 결정적인가
------------------
라인업은 **경기 약 1시간 전**에 공개된다. 프로토 배당은 최대 60시간 전에 굳는다.
즉 라인업은 **배당이 매겨질 때 시장이 가질 수 없었던 정보**다.

    야구 선발 예고  경기 30시간 전 (KBO) · 5일 전 (MLB)  → 배당보다 이를 수 있음
    축구 라인업     경기 1시간 전                          → 배당보다 확실히 늦음

지금까지 네 번 막혔던 이유가 "정보가 이미 가격에 있어서"였다면,
라인업은 구조적으로 그럴 수 없다. **가장 깨끗한 검증 대상이다.**

피처 (walk-forward, 그 경기 이전 라인업만 사용)
    lineup_change   직전 경기 선발 11명 중 이번에 빠진 인원 수
    core_absent     최근 5경기 중 4경기 이상 선발이던 '주전'의 결장 수
    formation_chg   포메이션이 직전 경기와 다른가
    xi_experience   선발 11명의 시즌 누적 선발 출장 합 (라인업 무게)

    각각 홈−원정 차이로 만든다.

⚠️ 라인업 자체는 그 경기의 것이라 '미래 정보'가 아니다 —
   경기 시작 전에 공개되므로 예측 시점에 알 수 있다.
   다만 **주전 판정**은 과거 경기로만 해야 한다(그래서 walk-forward).
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import build_features                    # noqa: E402
from matches import load_matches                       # noqa: E402
from variable_impact import _brier, _fit, _se          # noqa: E402

DETAIL = Path(__file__).resolve().parent.parent / "data" / "raw" / "detail"
TRAIN_END = 2024
CORE_WINDOW = 5          # 주전 판정 창
CORE_MIN = 4             # 이 창에서 이만큼 선발이면 '주전'


def load_lineups(fname: str) -> pd.DataFrame:
    raw = json.loads((DETAIL / fname).read_text(encoding="utf-8"))
    rows = []
    for g in raw.values():
        d = g.get("data") or {}
        h, a = d.get("home") or {}, d.get("away") or {}
        hp = [p.get("name") for p in (h.get("players") or []) if p.get("name")]
        ap = [p.get("name") for p in (a.get("players") or []) if p.get("name")]
        if len(hp) < 7 or len(ap) < 7:
            continue          # 라인업이 온전치 않은 경기는 버린다
        rows.append({
            "gameId": g["gameId"], "date": pd.to_datetime(g.get("date")),
            "home_team": g.get("home"), "away_team": g.get("away"),
            "home_score": g.get("home_score"), "away_score": g.get("away_score"),
            "home_xi": hp, "away_xi": ap,
            "home_formation": h.get("formation"), "away_formation": a.get("formation"),
        })
    df = pd.DataFrame(rows).dropna(subset=["date"])
    return df.sort_values("date").reset_index(drop=True)


def build_lineup_features(lu: pd.DataFrame) -> pd.DataFrame:
    """날짜순 1패스. 주전 판정은 **그 경기 이전** 라인업으로만 한다."""
    hist: dict = defaultdict(lambda: deque(maxlen=CORE_WINDOW))  # 팀 → 과거 XI
    last_xi: dict = {}
    last_form: dict = {}
    starts: dict = defaultdict(Counter)      # 팀 → 선수별 누적 선발 수
    rows = []

    for r in lu.itertuples():
        out = {"gameId": r.gameId, "date": r.date,
               "home_team": r.home_team, "away_team": r.away_team}

        for side, team, xi, form in (("h", r.home_team, r.home_xi, r.home_formation),
                                     ("a", r.away_team, r.away_xi, r.away_formation)):
            prev = last_xi.get(team)
            out[f"{side}_change"] = (len(set(prev) - set(xi))
                                     if prev else np.nan)
            out[f"{side}_form_chg"] = (0.0 if last_form.get(team) in (None, form)
                                       else 1.0)
            # 주전 = 최근 CORE_WINDOW 경기 중 CORE_MIN 이상 선발
            past = hist[team]
            if len(past) >= CORE_WINDOW:
                cnt = Counter()
                for p in past:
                    cnt.update(p)
                core = {n for n, c in cnt.items() if c >= CORE_MIN}
                out[f"{side}_core_absent"] = len(core - set(xi))
            else:
                out[f"{side}_core_absent"] = np.nan
            # 라인업 무게 = 선발 11명의 과거 선발 누적 합
            out[f"{side}_xi_exp"] = float(sum(starts[team][n] for n in xi))

        rows.append(out)

        # ---- 상태 갱신 (피처 생성 이후)
        for team, xi, form in ((r.home_team, r.home_xi, r.home_formation),
                               (r.away_team, r.away_xi, r.away_formation)):
            hist[team].append(list(xi))
            last_xi[team] = list(xi)
            last_form[team] = form
            starts[team].update(xi)

    df = pd.DataFrame(rows)
    df["lineup_change_diff"] = df["a_change"] - df["h_change"]
    df["core_absent_diff"] = df["a_core_absent"] - df["h_core_absent"]
    df["formation_chg_diff"] = df["h_form_chg"] - df["a_form_chg"]
    df["xi_exp_diff"] = df["h_xi_exp"] - df["a_xi_exp"]
    return df


def build_team_map(proto: pd.DataFrame, lu: pd.DataFrame) -> dict:
    """프로토 팀명 → 네이버 팀명. 날짜+스코어로 경기를 잇고 동시출현으로 확정.

    프로토는 `강원FC`, 네이버는 `강원` 처럼 표기가 다르다.
    문자열 규칙을 추측하지 않고 데이터가 대응을 만들게 한다(team_map.py 와 같은 방식).
    """
    idx: dict = defaultdict(list)
    for r in lu.itertuples():
        if r.home_score is None or r.away_score is None:
            continue
        idx[(r.date.date(), int(r.home_score), int(r.away_score))].append(r)

    votes: dict = defaultdict(Counter)
    for r in proto.itertuples():
        cands = idx.get((r.date.date(), int(r.home_score), int(r.away_score)), [])
        if len(cands) != 1:
            continue                      # 같은 날 같은 스코어가 여럿이면 버린다
        c = cands[0]
        votes[r.home_team][c.home_team] += 1
        votes[r.away_team][c.away_team] += 1

    mapping = {}
    for pteam, c in votes.items():
        nteam, n = c.most_common(1)[0]
        if n >= 3 and n / sum(c.values()) >= 0.6:
            mapping[pteam] = nteam
    return mapping


FEATS = ["lineup_change_diff", "core_absent_diff", "formation_chg_diff",
         "xi_exp_diff"]
LABELS = {
    "lineup_change_diff": "선발 교체 인원 차 (원정−홈)",
    "core_absent_diff": "주전 결장 수 차 (원정−홈)",
    "formation_chg_diff": "포메이션 변경 (홈−원정)",
    "xi_exp_diff": "선발 11명 출장경험 차 (홈−원정)",
}


def main() -> int:
    files = sorted(DETAIL.glob("*_soccer_*.json"))
    if not files:
        print("라인업 데이터가 없습니다. python src/game_detail.py soccer kleague")
        return 1
    lu = pd.concat([load_lineups(f.name) for f in files], ignore_index=True)
    lu = lu.sort_values("date").reset_index(drop=True)
    print(f"라인업 확보 {len(lu):,}경기 "
          f"({lu['date'].min().date()} ~ {lu['date'].max().date()})")

    lf = build_lineup_features(lu)

    m = load_matches()
    fe = build_features(m)
    sc = fe[(fe["sport"] == "sc") & (fe["outcome"] != 0.5)].copy()
    sc["date"] = pd.to_datetime(sc["date"])

    # 프로토 팀명(강원FC) ↔ 네이버 팀명(강원) 자동 매핑
    proto_scored = m[m["sport"] == "sc"].copy()
    proto_scored["date"] = pd.to_datetime(proto_scored["date"])
    tmap = build_team_map(proto_scored, lu)
    print(f"팀명 매핑 확정 {len(tmap)}팀  예) " +
          ", ".join(f"{a}→{b}" for a, b in list(tmap.items())[:5]))
    sc["home_team"] = sc["home_team"].map(lambda x: tmap.get(x, x))
    sc["away_team"] = sc["away_team"].map(lambda x: tmap.get(x, x))

    df = sc.merge(lf, on=["date", "home_team", "away_team"], how="inner")
    print(f"프로토 경기와 결합: {len(df):,}건 "
          f"(리그: {', '.join(sorted(df['league'].unique())[:6])})")
    if len(df) < 400:
        print("표본 부족 — 팀명 표기가 다를 수 있다. team_map 확장 필요")
        print("  프로토 축구 팀 예:", sorted(sc['home_team'].unique())[:8])
        print("  네이버 라인업 팀 예:", sorted(lu['home_team'].unique())[:8])
        return 1

    df = df.dropna(subset=["elo_diff"])
    tr, te = df[df["year"] <= TRAIN_END], df[df["year"] > TRAIN_END]
    print(f"학습 {len(tr):,} / 검증 {len(te):,}\n")
    if len(tr) < 200 or len(te) < 150:
        print("학습/검증 표본 부족")
        return 1

    def mk(d, cols):
        return np.column_stack([np.ones(len(d))]
                               + [d[c].to_numpy(float) for c in cols])

    y_tr = (tr["outcome"] == 1.0).to_numpy(float)
    y_te = (te["outcome"] == 1.0).to_numpy(float)
    b0 = _fit(mk(tr, ["elo_diff"]), y_tr)
    print(f"기준 (Elo 단독) 검증 Brier = "
          f"{_brier(mk(te, ['elo_diff']), b0, y_te):.5f}\n")

    print(f"{'피처':<30}{'n':>7}{'계수':>10}{'z':>8}{'Brier':>10}{'개선':>11}  판정")
    print("-" * 80)
    ok_feats = []
    for f in FEATS:
        t2, v2 = tr.dropna(subset=[f]), te.dropna(subset=[f])
        if len(t2) < 200 or len(v2) < 120 or t2[f].std() < 1e-9:
            print(f"{LABELS[f]:<30} 표본 부족 ({len(t2)}/{len(v2)})")
            continue
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        b = _fit(mk(t2, ["elo_diff", f]), yt)
        se = _se(mk(t2, ["elo_diff", f]), b)
        z = b[2] / se[2] if se[2] > 0 else 0.0
        b_ref = _fit(mk(t2, ["elo_diff"]), yt)
        ref = _brier(mk(v2, ["elo_diff"]), b_ref, yv)
        br = _brier(mk(v2, ["elo_diff", f]), b, yv)
        good = abs(z) >= 2.58 and (ref - br) > 0
        if good:
            ok_feats.append(f)
        print(f"{LABELS[f]:<30}{len(t2):>7,}{b[2]:>10.4f}{z:>8.2f}"
              f"{br:>10.5f}{ref-br:>+11.5f}  {'✅ 채택' if good else '❌'}")

    if ok_feats:
        t2, v2 = tr.dropna(subset=ok_feats), te.dropna(subset=ok_feats)
        yt = (t2["outcome"] == 1.0).to_numpy(float)
        yv = (v2["outcome"] == 1.0).to_numpy(float)
        b = _fit(mk(t2, ["elo_diff"] + ok_feats), yt)
        br = _brier(mk(v2, ["elo_diff"] + ok_feats), b, yv)
        print(f"\n채택 피처 전부: Brier {br:.5f}")

        from model_v2 import attach_odds
        v3 = attach_odds(v2)
        if len(v3) > 120:
            yv3 = (v3["outcome"] == 1.0).to_numpy(float)
            p = 1 / (1 + np.exp(-np.clip(mk(v3, ["elo_diff"] + ok_feats) @ b,
                                         -30, 30)))
            ov = 1 / v3["o_home"] + 1 / v3["o_away"]
            pm = ((1 / v3["o_home"]) / ov).to_numpy(float)
            bm, bk = float(np.mean((p - yv3) ** 2)), float(np.mean((pm - yv3) ** 2))
            print(f"\n⭐ 시장 비교 (검증 {len(v3):,}경기)")
            print(f"   모델(Elo+라인업) Brier {bm:.5f}   시장 Brier {bk:.5f}   "
                  f"{'✅ 모델 우위' if bm < bk else '❌ 시장 우위'}")
            print("   ※ 라인업은 배당 확정 후 공개되므로 시장이 가질 수 없던 정보다.")
    else:
        print("\n채택된 라인업 피처 없음.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
