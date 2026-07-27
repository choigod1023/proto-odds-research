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
        hpl = [p for p in (h.get("players") or []) if p.get("name")]
        apl = [p for p in (a.get("players") or []) if p.get("name")]
        hp = [p["name"] for p in hpl]
        ap = [p["name"] for p in apl]
        if len(hp) < 7 or len(ap) < 7:
            continue          # 라인업이 온전치 않은 경기는 버린다
        rows.append({
            "gameId": g["gameId"], "date": pd.to_datetime(g.get("date")),
            "home_team": g.get("home"), "away_team": g.get("away"),
            "home_score": g.get("home_score"), "away_score": g.get("away_score"),
            "home_xi": hp, "away_xi": ap,
            "home_pl": hpl, "away_pl": apl,
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
    # 선수 가치 = 누적 (득점 + 도움). 에이스 1명 결장과 백업 3명 결장은 다르다.
    value: dict = defaultdict(float)         # (팀, 선수) → 누적 공격포인트
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
                miss = core - set(xi)
                out[f"{side}_core_absent"] = len(miss)
                # ⭐ 빠진 주전의 '가치' 합 — 에이스 결장에 더 큰 가중
                out[f"{side}_absent_value"] = float(
                    sum(value[(team, n)] for n in miss))
            else:
                out[f"{side}_core_absent"] = np.nan
                out[f"{side}_absent_value"] = np.nan
            # 라인업 무게 = 선발 11명의 과거 선발 누적 합
            out[f"{side}_xi_exp"] = float(sum(starts[team][n] for n in xi))

        rows.append(out)

        # ---- 상태 갱신 (피처 생성 이후)
        for team, xi, form, pl in (
                (r.home_team, r.home_xi, r.home_formation, r.home_pl),
                (r.away_team, r.away_xi, r.away_formation, r.away_pl)):
            hist[team].append(list(xi))
            last_xi[team] = list(xi)
            last_form[team] = form
            starts[team].update(xi)
            for p in pl:
                value[(team, p["name"])] += (float(p.get("goal") or 0)
                                             + float(p.get("assists") or 0))

    df = pd.DataFrame(rows)
    df["lineup_change_diff"] = df["a_change"] - df["h_change"]
    df["core_absent_diff"] = df["a_core_absent"] - df["h_core_absent"]
    df["formation_chg_diff"] = df["h_form_chg"] - df["a_form_chg"]
    df["xi_exp_diff"] = df["h_xi_exp"] - df["a_xi_exp"]
    df["absent_value_diff"] = df["a_absent_value"] - df["h_absent_value"]
    return df


def add_controls(df: pd.DataFrame, train_mask) -> pd.DataFrame:
    """역인과 통제 — 팀 간 차이를 빼고 **팀 안에서의 변동만** 남긴다.

    왜 필요한가
    ------------
    첫 측정에서 결장 계수의 부호가 예상과 일관되게 반대였다.
    가능한 설명은 **선택 효과**다: 강팀은 순위가 안정되면 주전을 쉬게 한다.
    그러면 '주전이 빠진 팀'이 오히려 강한 팀이 되어 부호가 뒤집힌다.

    Elo 가 팀 강함을 통제하지만, Elo 는 천천히 움직여서
    '이 팀은 원래 로테이션을 자주 한다' 같은 팀별 성향은 못 잡는다.

    해결: 팀별 평균을 빼서(**within-team demeaning**) 팀 간 비교를 제거한다.
          "로테이션 잦은 팀"이 아니라 **"이 팀 치고 유독 많이 빠진 경기"**만 남는다.

    ⚠️ 팀 평균은 **학습 구간에서만** 계산한다(검증 구간 정보 누수 방지).
    """
    out = df.copy()
    tr = out[train_mask]
    for side, teamcol in (("h", "home_team"), ("a", "away_team")):
        for col in ("core_absent", "absent_value", "change"):
            src = f"{side}_{col}"
            if src not in out.columns:
                continue
            mu = tr.groupby(teamcol)[src].mean()
            gm = float(tr[src].mean())
            out[f"{src}_dm"] = out[src] - out[teamcol].map(mu).fillna(gm)

    out["core_absent_dm_diff"] = out["a_core_absent_dm"] - out["h_core_absent_dm"]
    out["absent_value_dm_diff"] = out["a_absent_value_dm"] - out["h_absent_value_dm"]
    out["change_dm_diff"] = out["a_change_dm"] - out["h_change_dm"]
    return out


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
        if n >= 2 and n / sum(c.values()) >= 0.55:
            mapping[pteam] = nteam

    # 스코어 매칭으로 못 잡은 팀은 문자열 포함관계로 보조 매핑한다.
    # 프로토 `김천상무` ↔ 네이버 `김천` 처럼 한쪽이 다른 쪽을 포함하는 경우가 많다.
    nav_teams = set(lu["home_team"]) | set(lu["away_team"])
    used = set(mapping.values())
    for pteam in set(proto["home_team"]) | set(proto["away_team"]):
        if pteam in mapping:
            continue
        cands = [n for n in nav_teams if n in pteam or pteam in n]
        # 가장 긴 공통 후보 하나만, 그리고 아직 안 쓰인 것 우선
        cands.sort(key=lambda n: (n in used, -len(n)))
        if cands:
            mapping[pteam] = cands[0]
    return mapping


FEATS = ["lineup_change_diff", "core_absent_diff", "absent_value_diff",
         "formation_chg_diff", "xi_exp_diff",
         # 역인과 통제판 (팀별 평균 제거)
         "core_absent_dm_diff", "absent_value_dm_diff", "change_dm_diff"]
# 일정 변수는 통제항으로 같이 넣는다 (로테이션의 직접 원인)
CONTROLS = ["rest_diff"]
LABELS = {
    "lineup_change_diff": "선발 교체 인원 차 (원정−홈)",
    "core_absent_diff": "주전 결장 수 차 (원정−홈)",
    "absent_value_diff": "결장 주전의 가치 합 차",
    "core_absent_dm_diff": "주전 결장 (팀평균 제거) ⭐",
    "absent_value_dm_diff": "결장 가치 (팀평균 제거) ⭐",
    "change_dm_diff": "선발 교체 (팀평균 제거)",
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
    # ⭐ 무승부를 버리지 않는다. 종속변수가 득점차이므로 무승부는 0 이라는 정상값이다.
    #    (승패 이진으로 보면 K리그 경기의 28.4% 를 통째로 잃는다)
    sc = fe[fe["sport"] == "sc"].copy()
    sc["date"] = pd.to_datetime(sc["date"])

    # 프로토 팀명(강원FC) ↔ 네이버 팀명(강원) 자동 매핑
    # 득점차를 종속변수로 쓰기 위해 스코어를 붙인다
    scores = m[m["sport"] == "sc"][["date", "league", "home_team", "away_team",
                                    "home_score", "away_score"]].copy()
    scores["date"] = pd.to_datetime(scores["date"])
    sc = sc.merge(scores, on=["date", "league", "home_team", "away_team"],
                  how="left")
    sc["gd"] = sc["home_score"] - sc["away_score"]

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

    df = df.dropna(subset=["elo_diff", "gd"])
    df = add_controls(df, df["year"] <= TRAIN_END)
    tr, te = df[df["year"] <= TRAIN_END], df[df["year"] > TRAIN_END]
    print(f"학습 {len(tr):,} / 검증 {len(te):,}  "
          f"(무승부 {(df['gd'] == 0).sum():,}건 포함)\n")
    if len(tr) < 200 or len(te) < 150:
        print("학습/검증 표본 부족")
        return 1

    def mk(d, cols):
        return np.column_stack([np.ones(len(d))]
                               + [d[c].to_numpy(float) for c in cols])

    def ols(X, y):
        return np.linalg.lstsq(X, y, rcond=None)[0]

    def rmse(X, b, y):
        return float(np.sqrt(np.mean((X @ b - y) ** 2)))

    def tstat(X, b, y):
        n, k = X.shape
        resid = y - X @ b
        s2 = float(resid @ resid) / max(n - k, 1)
        cov = s2 * np.linalg.pinv(X.T @ X)
        return b / np.sqrt(np.maximum(np.diag(cov), 1e-18))

    # 기준 모델에 일정 변수(휴식일 차)를 통제항으로 포함한다.
    # 로테이션의 직접 원인이므로 여기서 걷어내야 결장의 순수 효과가 남는다.
    ctrl = [c for c in CONTROLS if c in tr.columns and tr[c].notna().mean() > 0.7]
    base_cols = ["elo_diff"] + ctrl
    tr = tr.dropna(subset=base_cols)
    te = te.dropna(subset=base_cols)
    print(f"통제항: {ctrl or '없음'}")
    y_tr, y_te = tr["gd"].to_numpy(float), te["gd"].to_numpy(float)
    b0 = ols(mk(tr, base_cols), y_tr)
    base_rmse = rmse(mk(te, base_cols), b0, y_te)
    print("종속변수 = 득점차 (홈−원정). 연속값이라 승패보다 정보량이 많다.")
    print(f"기준 (Elo 단독) 검증 RMSE = {base_rmse:.5f}\n")

    print(f"{'피처':<30}{'n':>7}{'계수':>10}{'t':>8}{'RMSE':>10}{'개선':>11}  판정")
    print("-" * 80)
    ok_feats = []
    for f in FEATS:
        t2, v2 = tr.dropna(subset=[f]), te.dropna(subset=[f])
        if len(t2) < 200 or len(v2) < 120 or t2[f].std() < 1e-9:
            print(f"{LABELS[f]:<30} 표본 부족 ({len(t2)}/{len(v2)})")
            continue
        yt, yv = t2["gd"].to_numpy(float), v2["gd"].to_numpy(float)
        X, Xv = mk(t2, base_cols + [f]), mk(v2, base_cols + [f])
        b = ols(X, yt)
        t = tstat(X, b, yt)[-1]
        b_ref = ols(mk(t2, base_cols), yt)
        ref = rmse(mk(v2, base_cols), b_ref, yv)
        cur = rmse(Xv, b, yv)
        good = abs(t) >= 2.58 and (ref - cur) > 0
        if good:
            ok_feats.append(f)
        print(f"{LABELS[f]:<30}{len(t2):>7,}{b[-1]:>10.4f}{t:>8.2f}"
              f"{cur:>10.5f}{ref-cur:>+11.5f}  {'✅ 채택' if good else '❌'}")

    if not ok_feats:
        print("\n채택된 라인업 피처 없음.")
        return 0

    t2, v2 = tr.dropna(subset=ok_feats), te.dropna(subset=ok_feats)
    yt, yv = t2["gd"].to_numpy(float), v2["gd"].to_numpy(float)
    b = ols(mk(t2, base_cols + ok_feats), yt)
    print(f"\n채택 피처 전부: 검증 RMSE {rmse(mk(v2, base_cols + ok_feats), b, yv):.5f}")

    # ---- 득점차 모델을 승패 확률로 바꿔 시장과 비교
    #      득점차 예측을 정규분포로 보고 P(홈승) = 1 − Φ(0.5 | mu, sigma)
    from math import erf, sqrt
    resid = yt - mk(t2, base_cols + ok_feats) @ b
    sigma = float(np.std(resid))
    mu_v = mk(v2, base_cols + ok_feats) @ b
    p_home = 0.5 * (1 - np.array([erf((0.5 - m) / (sigma * sqrt(2)))
                                  for m in mu_v]))

    from model_v2 import attach_odds
    v3 = v2.assign(_p=p_home)
    v3 = attach_odds(v3)
    v3 = v3[v3["gd"] != 0]          # 승패 시장이라 무승부는 비교 대상에서 제외
    if len(v3) > 120:
        yv3 = (v3["gd"] > 0).astype(float).to_numpy()
        p = v3["_p"].to_numpy(float)
        ov = 1 / v3["o_home"] + 1 / v3["o_away"]
        pm = ((1 / v3["o_home"]) / ov).to_numpy(float)
        bm, bk = float(np.mean((p - yv3) ** 2)), float(np.mean((pm - yv3) ** 2))
        print(f"\n⭐ 시장 비교 (검증 {len(v3):,}경기)")
        print(f"   모델(Elo+라인업) Brier {bm:.5f}   시장 Brier {bk:.5f}   "
              f"{'✅ 모델 우위' if bm < bk else '❌ 시장 우위'}")
        print("   ※ 라인업은 배당 확정 후 공개되므로 시장이 가질 수 없던 정보다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
