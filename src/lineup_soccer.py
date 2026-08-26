"""축구 라인업 — 로테이션을 **직접 센다**.

왜 만드나
---------
검증 5번("축구 라인업이 우위인가")을 '표본 부족'으로 닫아뒀다. 그런데 그건
**이번 경기 라인업**을 우위로 쓸 수 있느냐의 답이다 — 라인업은 킥오프 1시간 전에
나오고 프로토 배당은 최대 60시간 전에 굳으니 예측에는 늦다.

**과거 라인업을 분석하는 건 전혀 다른 얘기다.** 이미 공개된 816경기의 선발 11명이
그대로 있다. 여기서 나오는 건 예측이 아니라 **팀의 성질**이다:
  · 로테이션 폭 — 직전 경기 대비 선발을 몇 명 바꾸는가
  · 포메이션 — 무엇을 주로 쓰고 얼마나 자주 바꾸는가
  · 라인업 안정성 — 같은 XI 를 계속 쓰는 팀인가

그리고 이건 앞서 못 끝낸 질문과 직결된다. 컵대회에서 강팀 우위가 압축되는 걸
득실차 기울기로 봤을 때(리그 +1.258 vs 컵 +1.085) 방향은 맞았지만 95%CI 가
0 을 포함했다. **로테이션을 직접 세면 간접 추정이 필요 없다.**

⚠️ 다만 이 데이터는 **K리그1 정규시즌뿐**이다(연도당 232경기). 한국FA컵 라인업은
   없어서 컵 로테이션은 아직 직접 검정할 수 없다. 여기서 재는 건 리그 기준선이다.

사용:
    python3 src/lineup_soccer.py            # data/processed/lineup_soccer.csv
    python3 src/lineup_soccer.py --selftest
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detail_paths import latest_detail_path                # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def source_path() -> Path:
    return latest_detail_path("kleague", "soccer")


# Compatibility snapshot only; loaders resolve the path again at call time.
SRC = source_path()
OUT = ROOT / "data" / "processed" / "lineup_soccer.csv"


def load(path: Path | None = None) -> pd.DataFrame:
    """경기 × 팀 단위로 편다. 한 행이 '이 팀이 이 경기에 낸 XI'."""
    path = source_path() if path is None else path
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for g in raw.values():
        d = g.get("data") or {}
        for side in ("home", "away"):
            s = d.get(side) or {}
            ps = s.get("players") or []
            if len(ps) < 11:
                continue
            team = g[side]
            opp = g["away" if side == "home" else "home"]
            gf = g["home_score"] if side == "home" else g["away_score"]
            ga = g["away_score"] if side == "home" else g["home_score"]
            rows.append({
                "date": g["date"], "team": team, "opp": opp, "is_home": side == "home",
                "formation": s.get("formation") or "",
                "xi": tuple(sorted(p["playerId"] for p in ps)),
                "gf": gf, "ga": ga,
                "n_sub": len((d.get("substitution") or {}).get(side) or []),
            })
    df = pd.DataFrame(rows).sort_values(["team", "date"]).reset_index(drop=True)

    # 직전 경기 대비 선발 변경 인원 (0~11). 팀의 첫 경기는 비교 대상이 없다.
    churn, form_ch = [], []
    prev_xi: dict = {}
    prev_fm: dict = {}
    for r in df.itertuples():
        p = prev_xi.get(r.team)
        churn.append(len(set(r.xi) - set(p)) if p else np.nan)
        pf = prev_fm.get(r.team)
        form_ch.append((pf is not None and pf != r.formation) if pf else np.nan)
        prev_xi[r.team] = r.xi
        prev_fm[r.team] = r.formation
    df["churn"] = churn                    # 몇 명 바뀌었나
    df["formation_changed"] = form_ch

    # ⚠️ 교체 '인원 수' 만 세면 **주전 A ↔ 주전 B 스왑과 2군 투입이 같아진다.**
    #    로테이션의 실제 의미는 '주전을 안 내보냈다' 이므로 선수 등급을 봐야 한다.
    #    팀·시즌별 선발 횟수로 주전을 정한다 — 선발 지분 상위 11명이 그 팀의 1군 XI.
    df["season"] = df["date"].str[:4]
    starts: dict = defaultdict(int)
    for r in df.itertuples():
        for pid in r.xi:
            starts[(r.team, r.season, pid)] += 1
    regulars: dict = {}
    for (team, season, pid), n in starts.items():
        regulars.setdefault((team, season), []).append((n, pid))
    top11 = {k: {pid for _, pid in sorted(v, reverse=True)[:11]}
             for k, v in regulars.items()}
    team_games = df.groupby(["team", "season"]).size().to_dict()

    n_res, share = [], []
    for r in df.itertuples():
        core = top11.get((r.team, r.season), set())
        n_res.append(sum(1 for pid in r.xi if pid not in core))
        tg = team_games.get((r.team, r.season), 1)
        share.append(float(np.mean([starts[(r.team, r.season, pid)] / tg for pid in r.xi])))
    df["n_reserve"] = n_res      # XI 중 주전(상위11) 이 아닌 인원 = 2군 투입 규모
    df["xi_share"] = np.round(share, 3)   # XI 평균 선발 지분 (1.0 = 늘 나오는 선수들)
    df["margin"] = df["gf"] - df["ga"]
    df["win"] = (df["margin"] > 0).astype(int)
    return df


def team_profile(df: pd.DataFrame) -> pd.DataFrame:
    """팀별 로테이션 성향. 해설에 쓸 '이 팀은 어떤 팀인가'."""
    g = df.dropna(subset=["churn"]).groupby("team")
    p = g.agg(경기=("churn", "size"),
              평균교체=("churn", "mean"),
              평균비주전=("n_reserve", "mean"),
              XI지분=("xi_share", "mean"),
              최다교체=("churn", "max"),
              포메이션변경률=("formation_changed", "mean"),
              평균득점=("gf", "mean"),
              평균실점=("ga", "mean"))
    p["주포메이션"] = df.groupby("team")["formation"].agg(
        lambda s: s.value_counts().index[0] if len(s) else "")
    return p.sort_values("평균교체", ascending=False).round(2)


def churn_vs_result(df: pd.DataFrame) -> pd.DataFrame:
    """로테이션이 결과와 상관있나 — 해설에 '의미'를 붙이려면 먼저 재야 한다."""
    d = df.dropna(subset=["churn"]).copy()
    out = {}
    for name, col, bins, labs in (
        ("교체 인원", "churn", [-0.1, 1, 3, 5, 11], ["0–1명", "2–3명", "4–5명", "6명+"]),
        ("비주전 투입", "n_reserve", [-0.1, 1, 3, 5, 11], ["0–1명", "2–3명", "4–5명", "6명+"]),
    ):
        d["구간"] = pd.cut(d[col], bins, labels=labs)
        r = d.groupby("구간", observed=True).agg(
            n=("win", "size"), 승률=("win", "mean"),
            득점=("gf", "mean"), 실점=("ga", "mean"), 마진=("margin", "mean"))
        r["승률"] = (r["승률"] * 100).round(1)
        out[name] = r.round(2)
    return out


def _selftest() -> int:
    df = load()
    bad = []
    print("축구 라인업 자기검사")
    print(f"  경기×팀 {len(df):,}행 · 팀 {df['team'].nunique()}개 · "
          f"{df['date'].min()[:4]}~{df['date'].max()[:4]}")
    if (df["xi"].map(len) != 11).any():
        bad.append("선발이 11명이 아닌 행이 있다")
    print("  ✅ 모든 행이 선발 11명")
    ch = df["churn"].dropna()
    if not ((ch >= 0) & (ch <= 11)).all():
        bad.append("교체 인원이 0~11 범위를 벗어난다")
    print(f"  ✅ 교체 인원 0~11 (평균 {ch.mean():.2f}명)")
    # 홈/원정 행이 짝을 이뤄야 한다
    if df.groupby(["date", "team"]).size().max() > 1:
        bad.append("같은 팀이 같은 날 두 번 나온다")
    print("  ✅ 팀·날짜 중복 없음")
    if bad:
        print("\n🔴 " + "\n🔴 ".join(bad))
        return 1
    print("\n✅ 통과")
    return 0


def main() -> int:
    df = load()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["xi"]).to_csv(OUT, index=False)

    print(f"K리그1 라인업 {len(df):,}행 (경기 {len(df)//2:,})\n")
    print("=== 팀별 로테이션 성향 ===")
    print(team_profile(df).to_string())
    for name, r in churn_vs_result(df).items():
        print(f"\n=== {name}과 결과 ===")
        print(r.to_string())
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
