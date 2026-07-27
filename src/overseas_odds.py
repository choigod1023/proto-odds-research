"""해외 배당 수집 + 프로토와 대조 — 이 프로젝트의 원래 전략.

왜 이제야 하는가 (반성)
------------------------
사전조사(README §5, §9)의 결론은 명확했다:

> **Kaunitz et al. (2017)**: 자체 예측모델 없이 **북메이커 컨센서스 대비 이탈**을 찾아 베팅해
> 10년 백테스트·실자금 모두 수익.
> **Hubáček (2019)**: 정확도만 최대화하면 북메이커와 예측이 겹쳐 돈이 안 된다.
> 관통하는 한 문장 — **시장을 이긴 연구들은 예측을 더 잘해서 이긴 게 아니라
> 배당이 틀린 지점을 골라내서 이겼다.**

그런데 이 프로젝트는 이틀 내내 **자체 예측모델**만 만들었다(Elo → 변수 → pi-ratings →
선발투수 → 라인업). 전부 시장에 졌다. 문헌이 "그렇게 하면 진다"고 한 그 방식이었다.

빠진 조각은 **비교 대상**이다. 프로토 한쪽만 보면 "내 모델 vs 시장" 싸움밖에 못 한다.
해외 샤프북 컨센서스를 기준선으로 빌려오면 **내가 예측을 잘할 필요가 없다.**

    해외 배당 (마진 ~5%)  → devig → 공정 확률
              ↕ 괴리
    프로토 배당 (마진 12%)

데이터 경로 (2026-07-27 확인)
-----------------------------
BetExplorer 결과 페이지. 배당은 **`data-odd` 속성**에 들어 있어 JS 렌더링 없이 받힌다.

    <td class="table-main__odds" data-oid="..." data-odd="1.95"></td>

⚠️ named API 에도 `internationalWinLoseOdds` 필드가 있으나 **전부 비어 있다**(실측 0건).
   다만 named `representativeOdds.domestic.roundGameType == "PROTO_WIN_LOSS"` 로
   **named domestic 이 진짜 프로토 배당**임은 확인됐다(Q3 절반 해결).

팀명 매핑
---------
BetExplorer 는 `Doosan Bears`, 프로토는 `두산`. 문자열로는 못 잇는다.
야구·축구에서 이미 쓴 방식 그대로 **날짜 + 스코어**로 경기를 맞춘다.

사용:
    python src/overseas_odds.py                # 수집 + 대조
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matches import load_matches                       # noqa: E402

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "overseas"
BASE = "https://www.betexplorer.com"
GAP = 1.5

# 프로토 리그 → BetExplorer 경로
SOURCES = {
    "KBO": "/baseball/south-korea/kbo/results/",
    "MLB": "/baseball/usa/mlb/results/",
    "K리그1": "/football/south-korea/k-league-1/results/",
    "NPB": "/baseball/japan/npb/results/",
}

_TAG = re.compile(r"<[^>]+>")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml"})
    return s


def parse_results(html: str, today: pd.Timestamp | None = None) -> list[dict]:
    """결과 테이블 행 → (팀명, 날짜, 스코어, 배당).

    ⚠️ 팀명이 `<span><strong>Doosan Bears</strong></span> - <span>Samsung Lions</span>`
       처럼 중첩 태그로 감싸여 있어 단순 정규식으로는 못 잡는다. 태그를 걷어낸 뒤 자른다.
    ⚠️ 날짜는 `25.07.`(DD.MM., 연도 없음) 또는 `Yesterday` 같은 상대 표기다.
    """
    today = today or pd.Timestamp.today().normalize()
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        if "data-odd" not in tr:
            continue
        m_a = re.search(r'class="in-match"[^>]*>(.*?)</a>', tr, re.S)
        m_sc = re.search(r">(\d+):(\d+)<", tr)
        odds = re.findall(r'data-odd="([\d.]+)"', tr)
        if not (m_a and m_sc) or len(odds) < 2:
            continue
        txt = _TAG.sub("", m_a.group(1)).strip()
        if " - " not in txt:
            continue
        home_en, away_en = [t.strip() for t in txt.split(" - ", 1)]

        m_dt = re.search(r'no-wrap">([^<]*)<', tr)
        raw = (m_dt.group(1) if m_dt else "").strip()
        date = None
        if raw.lower() == "yesterday":
            date = today - pd.Timedelta(days=1)
        elif raw.lower() == "today":
            date = today
        else:
            m = re.match(r"(\d{2})\.(\d{2})\.", raw)
            if m:
                dd, mm = int(m.group(1)), int(m.group(2))
                yr = today.year - (1 if mm > today.month else 0)
                try:
                    date = pd.Timestamp(year=yr, month=mm, day=dd)
                except ValueError:
                    date = None
        rows.append({
            "home_en": home_en, "away_en": away_en,
            "date": date.isoformat() if date is not None else None,
            "home_score": int(m_sc.group(1)), "away_score": int(m_sc.group(2)),
            "odds": [float(o) for o in odds[:3]],
        })
    return rows


def collect(sess: requests.Session) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    out = {}
    for league, path in SOURCES.items():
        try:
            r = sess.get(BASE + path, timeout=25)
            rows = parse_results(r.text) if r.status_code == 200 else []
        except Exception as e:                       # noqa: BLE001
            print(f"  [{league}] 오류 {type(e).__name__}")
            rows = []
        out[league] = rows
        ov = [sum(1 / o for o in x["odds"][:2]) for x in rows
              if len(x["odds"]) >= 2 and all(o > 1 for o in x["odds"][:2])]
        pay = 100 / np.mean(ov) if ov else float("nan")
        print(f"  {league:8} {len(rows):4d}경기 · 해외 환급률 {pay:.2f}%")
        (RAW / f"{league}.json").write_text(json.dumps(rows, ensure_ascii=False),
                                            encoding="utf-8")
        time.sleep(GAP)
    return out


def compare(data: dict) -> None:
    """프로토 배당과 해외 배당을 같은 경기에서 맞대본다."""
    from matches import GAMES, _DATE_RE, _away, _home
    proto = pd.read_csv(GAMES)
    proto = proto[(~proto["is_void"].astype(bool)) & (proto["market_family"] == "승패")
                  & (proto["n_way"] == 2) & (proto["result"].isin(["홈승", "홈패"]))]
    parts = proto["odds"].str.split(",", expand=True)
    proto = proto.assign(p_home=pd.to_numeric(parts[0], errors="coerce"),
                         p_away=pd.to_numeric(parts[1], errors="coerce"))
    hs, aw = proto["home"].map(_home), proto["away"].map(_away)
    proto = proto.assign(home_team=[t for t, _ in hs], home_score=[s for _, s in hs],
                         away_score=[s for s, _ in aw], away_team=[t for _, t in aw])
    md = proto["date_text"].astype(str).str.extract(_DATE_RE)
    proto = proto.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                         _dd=pd.to_numeric(md[1], errors="coerce"))
    proto = proto.dropna(subset=["_mm", "_dd", "p_home", "p_away",
                                 "home_score", "away_score"])
    proto["date"] = pd.to_datetime(dict(year=proto["year"],
                                        month=proto["_mm"].astype(int),
                                        day=proto["_dd"].astype(int)), errors="coerce")
    proto = proto.dropna(subset=["date"])

    print("\n" + "=" * 78)
    print("프로토 vs 해외 — 같은 경기 배당 대조")
    print("=" * 78)

    allrows = []
    for league, rows in data.items():
        if not rows:
            continue
        sub = proto[proto["league"] == league]
        # 날짜를 모르므로 (스코어) 로만 후보를 좁히고, 최근 경기 위주로 맞춘다
        idx = defaultdict(list)
        for r in sub.itertuples():
            idx[(r.date.date(), int(r.home_score), int(r.away_score))].append(r)

        matched = 0
        for x in rows:
            if len(x["odds"]) < 2 or any(o <= 1 for o in x["odds"][:2]):
                continue
            if not x.get("date"):
                continue
            key = (pd.Timestamp(x["date"]).date(), x["home_score"], x["away_score"])
            cands = idx.get(key, [])
            if len(cands) != 1:
                continue          # 날짜+스코어로 유일하지 않으면 버린다
            c = cands[0]
            o_ov = x["odds"][:2]
            ov_o = sum(1 / o for o in o_ov)
            ov_p = 1 / c.p_home + 1 / c.p_away
            allrows.append({
                "league": league, "date": c.date,
                "proto_home": c.p_home, "proto_away": c.p_away,
                "os_home": o_ov[0], "os_away": o_ov[1],
                "p_proto": (1 / c.p_home) / ov_p,     # devig(multiplicative)
                "p_os": (1 / o_ov[0]) / ov_o,
                "pay_proto": 100 / ov_p, "pay_os": 100 / ov_o,
                "won": 1.0 if c.result == "홈승" else 0.0,
            })
            matched += 1
        print(f"  {league:8} 해외 {len(rows):4d}경기 → 프로토 결합 {matched:4d}")

    if not allrows:
        print("\n결합된 경기가 없습니다. 스코어 매칭이 유일하지 않았습니다.")
        return
    df = pd.DataFrame(allrows)
    df["edge"] = df["p_os"] - df["p_proto"]        # 해외가 더 높게 보는 정도
    df["ev_home"] = df["p_os"] * df["proto_home"] - 1
    df["ev_away"] = (1 - df["p_os"]) * df["proto_away"] - 1
    df["best_ev"] = df[["ev_home", "ev_away"]].max(axis=1)

    print(f"\n총 결합 {len(df):,}경기")
    print(f"  프로토 평균 환급률 {df['pay_proto'].mean():.2f}%")
    print(f"  해외   평균 환급률 {df['pay_os'].mean():.2f}%")
    print(f"\n괴리(해외확률 − 프로토확률) 분포")
    q = df["edge"].abs().quantile([0.5, 0.75, 0.9, 0.95, 0.99])
    for k, v in q.items():
        print(f"    {int(k*100)}분위 {v:.4f} ({v*100:.2f}%p)")

    print(f"\n⭐ 해외 확률로 계산한 프로토 베팅 EV")
    print(f"  EV > 0  경기 수: {(df['best_ev'] > 0).sum():,} / {len(df):,} "
          f"({(df['best_ev'] > 0).mean():.1%})")
    for th in (0.0, 0.02, 0.05):
        sel = df[df["best_ev"] > th]
        if len(sel) < 10:
            continue
        # 실제 수익률
        pick_home = sel["ev_home"] >= sel["ev_away"]
        odds = np.where(pick_home, sel["proto_home"], sel["proto_away"])
        won = np.where(pick_home, sel["won"] == 1, sel["won"] == 0)
        roi = float(np.mean(np.where(won, odds - 1, -1.0)))
        print(f"    EV>{th:.0%}: {len(sel):4d}경기 · 실제 ROI {roi:+.2%}")
    print("\n  ※ 표본이 작으면 ROI 는 참고치일 뿐이다. 핵심은 괴리 분포다.")


def main() -> int:
    sess = _session()
    print("해외 배당 수집 (BetExplorer)")
    data = collect(sess)
    compare(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
