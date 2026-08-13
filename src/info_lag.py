"""정보 시차 결합 — 배당이 먼저 나왔나, 선발이 먼저 나왔나.

왜 이게 없으면 실험이 안 도나
------------------------------
`info_watch.py` 는 **선발 예고 시각**을, `snapshot.py` 는 **배당 등장·변동 시각**을
각각 잘 모은다. 그런데 **둘을 잇는 코드가 없었다.**

    info_watch      키 = 네이버 gameId (`20260728HTSS02026`)
    odds_timeseries 키 = 프로토 game_no + 팀명 (`9079`, `LG` vs `키움`)

키가 달라 사람이 눈으로 맞춰야 했다. 그래서 데이터가 쌓여도 판정이 안 됐다.
이 스크립트가 그 조인을 만든다. 한 번 만들어 두면 **수집이 쌓이는 만큼 자동으로**
표본이 늘고, 어느 시점에 돌려도 현재까지의 답이 나온다.

무엇을 판정하나
---------------
경기마다 두 시각을 뽑아 순서를 본다.

    t_odds    = 프로토 배당이 처음 관측된 시각
    t_info    = 선발 예고가 '비어 있다 → 채워진' 시각

    t_info > t_odds  →  **배당 선행** = 프로토가 선발을 모르고 값을 매겼다
                        → 여기가 정보 시차 구간. 공략 후보.
    t_info ≤ t_odds  →  정보 선행 = 프로토가 알고 매겼다. 시차 없음.

⚠️ 절단(censoring) 을 반드시 표시한다
-------------------------------------
수집기가 켜지기 전에 이미 있던 값은 '그때 생긴 것'이 아니다.
  · `is_baseline=1` 인 선발 관측 → t_info 를 **알 수 없음**
  · 배당 첫 관측이 수집 시작 시각과 같으면 → t_odds 를 **알 수 없음**
둘 중 하나라도 절단이면 **판정 불가**로 분류한다. 이걸 섞으면 가짜 결론이 난다.

사용:
    python src/info_lag.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
# ⚠️ 스냅샷은 2026-08-13 부터 **월별 샤드**다(단일 파일이 138MB 가 되어
#    GitHub 100MB 한도에 걸렸다). 경로를 직접 열지 말고 로더를 쓴다.
from snapshot import load_timeseries, ts_files      # noqa: E402
ANN = ROOT / "data" / "raw" / "info_watch" / "starter_announcements.csv"

# 네이버 gameId 의 팀 약어 → 프로토 표기. 야구만 대상(선발이 있는 종목).
TEAM = {
    # KBO
    "HT": "KIA", "SS": "삼성", "KT": "KT", "NC": "NC", "LG": "LG",
    "OB": "두산", "SK": "SSG", "WO": "키움", "HH": "한화", "LT": "롯데",
}
_SCORE = re.compile(r"^\s*-?\d+\s+|\s+-?\d+\s*$")


def clean_team(x: str) -> str:
    """'LG 12' · '10 키움' → 'LG' · '키움'."""
    return _SCORE.sub("", str(x)).strip()


def _team_map() -> dict:
    """프로토 표기 → 네이버 표기. `team_map.py` 가 스코어 대조로 만들어 둔 것.

    ⚠️ 이게 없으면 MLB·NPB 는 조인이 통째로 실패한다. 프로토는 4글자로 줄이고
       (`마이말린`) 네이버는 정식 표기(`마이애미`)를 쓰기 때문이다.
       KBO 만 우연히 양쪽 표기가 같아 붙는다.
    """
    p = ROOT / "data" / "processed" / "team_map.json"
    if not p.exists():
        return {}
    import json
    return json.loads(p.read_text(encoding="utf-8"))


def load_odds() -> pd.DataFrame:
    """경기별 배당 최초 관측 시각. 승패 2-way 기준(가장 먼저 열리는 마켓)."""
    t = load_timeseries()
    t["ts"] = pd.to_datetime(t["ts"], utc=True)
    start = t["ts"].min()

    t = t[(t["sport"] == "bs") & (t["market_family"] == "승패") & (t["n_way"] == 2)].copy()
    t["home_team"] = t["home"].map(clean_team)
    t["away_team"] = t["away"].map(clean_team)

    tm = _team_map()
    for col in ("home_team", "away_team"):
        t[col] = [tm.get(lg, {}).get(v, v) for lg, v in zip(t["league"], t[col])]
    md = t["date_text"].astype(str).str.extract(r"(\d{2})\.(\d{2})")
    t["mmdd"] = md[0] + md[1]

    g = (t.groupby(["league", "mmdd", "home_team", "away_team"], as_index=False)
           .agg(t_odds=("ts", "min"), n_snap=("ts", "size")))
    # 수집 시작과 같은 시각에 처음 보였다면 그 전부터 있었을 수 있다 → 절단
    g["odds_censored"] = (g["t_odds"] - start).dt.total_seconds() < 60
    return g, start


def load_ann() -> pd.DataFrame:
    """경기별 선발 예고 시각. 홈·원정 중 **늦은 쪽**(둘 다 나와야 정보가 완성된다)."""
    a = pd.read_csv(ANN)
    a["observed_at"] = pd.to_datetime(a["observed_at"], utc=True)
    a["mmdd"] = a["gameId"].astype(str).str[4:8]
    # gameId = YYYYMMDD + 원정2 + 홈2 + ...  (네이버 규약)
    a["away_team"] = a["gameId"].astype(str).str[8:10].map(TEAM)
    a["home_team"] = a["gameId"].astype(str).str[10:12].map(TEAM)
    # 매핑 실패(해외리그)는 원본 팀명으로 대체
    a["home_team"] = a["home_team"].fillna(a["home"].map(clean_team))
    a["away_team"] = a["away_team"].fillna(a["away"].map(clean_team))

    # ⚠️ 절단 판정은 '**정보가 완성된 순간**'을 기준으로 한다.
    #    선발 한 명이 기준선(수집 전부터 알려짐)이어도, **나머지 한 명이 뒤에
    #    실제로 공개되는 걸 관측했다면** 그 시점이 곧 정보 완성 시각이고 절단이 아니다.
    #    (처음엔 '하나라도 기준선이면 절단'으로 짰는데, 그러면 쓸 수 있는 표본을
    #     통째로 버린다.)
    a = a.sort_values("observed_at")
    g = (a.groupby(["league", "mmdd", "home_team", "away_team"], as_index=False)
           .agg(t_info=("observed_at", "max"),
                last_is_baseline=("is_baseline", "last"),
                n_field=("field", "size")))
    g["info_censored"] = g["last_is_baseline"].astype(bool)
    return g


def main() -> int:
    if not ts_files() or not ANN.exists():
        print("수집 파일이 없다. snapshot.py / info_watch.py 를 먼저 돌릴 것.")
        return 1

    odds, snap_start = load_odds()
    ann = load_ann()
    print(f"배당 경기 {len(odds):,} · 선발 경기 {len(ann):,}")
    print(f"스냅샷 수집 시작 {snap_start:%Y-%m-%d %H:%M} UTC\n")

    m = odds.merge(ann, on=["league", "mmdd", "home_team", "away_team"], how="inner")
    print(f"조인 성공 {len(m):,}경기")
    if m.empty:
        print("\n조인 0건 — 팀명 매핑 또는 리그 표기를 확인할 것")
        print("  배당 쪽 예:", odds[["league", "mmdd", "home_team", "away_team"]].head(3).values.tolist())
        print("  선발 쪽 예:", ann[["league", "mmdd", "home_team", "away_team"]].head(3).values.tolist())
        return 0

    m["lag_h"] = (m["t_info"] - m["t_odds"]).dt.total_seconds() / 3600
    m["censored"] = m["odds_censored"] | m["info_censored"]

    ok = m[~m["censored"]]
    print(f"  절단 없음(판정 가능) {len(ok):,} · 절단됨 {int(m['censored'].sum()):,}\n")

    if ok.empty:
        print("=" * 66)
        print("아직 판정 가능한 경기가 없다.")
        print("=" * 66)
        print("두 수집기가 켜지기 전부터 값이 있던 경기뿐이다(좌측절단).")
        print("info_watch·snapshot 이 계속 돌면 **새로 열리는 회차부터** 자동으로 쌓인다.")
        print("\n리그별 절단 현황:")
        for lg, s in m.groupby("league"):
            print(f"  {lg:6s} {len(s):4d}경기  배당절단 {int(s['odds_censored'].sum()):3d} · "
                  f"선발절단 {int(s['info_censored'].sum()):3d}")
        return 0

    print("=" * 66)
    print("배당이 먼저인가, 선발이 먼저인가")
    print("=" * 66)
    lead = (ok["lag_h"] > 0)
    print(f"  ★ 배당 선행(프로토가 선발 모르고 매김) {lead.sum():4d}경기 ({lead.mean():.1%})")
    print(f"     정보 선행(알고 매김)                {(~lead).sum():4d}경기")
    print(f"  시차(선발 − 배당) 중앙 {ok['lag_h'].median():+.1f}h · "
          f"최대 {ok['lag_h'].max():+.1f}h")
    print("\n리그별:")
    for lg, s in ok.groupby("league"):
        print(f"  {lg:6s} n={len(s):4d}  배당선행 {(s['lag_h'] > 0).mean():5.1%}  "
              f"중앙시차 {s['lag_h'].median():+6.1f}h")

    _save(m, ok)
    return 0


def _save(m: pd.DataFrame, ok: pd.DataFrame) -> None:
    """상세는 CSV, 진행상황 요약은 JSON.

    ⚠️ `data/processed/*.csv` 는 gitignore 대상이라 그것만 쓰면 **표본이 쌓이는지
       밖에서 확인할 수 없다.** 요약은 추적되는 `docs/data/` 에 같이 남긴다.
    """
    import json

    out = ROOT / "data" / "processed" / "info_lag.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    m.to_csv(out, index=False)

    summary = {
        "joined": int(len(m)),
        "decidable": int(len(ok)),
        "censored": int(m["censored"].sum()),
        "by_league": {
            lg: {"n": int(len(s)), "decidable": int((~s["censored"]).sum())}
            for lg, s in m.groupby("league")
        },
    }
    if len(ok):
        summary["odds_first_rate"] = round(float((ok["lag_h"] > 0).mean()), 4)
        summary["median_lag_h"] = round(float(ok["lag_h"].median()), 2)
    js = ROOT / "docs" / "data" / "info_lag.json"
    js.parent.mkdir(parents=True, exist_ok=True)
    js.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n저장: {out}  ← lag_h·censored 상세")
    print(f"      {js}  ← 진행상황 요약(추적됨)")
    print("공략 후보는 `censored==False & lag_h>0` 인 경기다.")


if __name__ == "__main__":
    raise SystemExit(main())
