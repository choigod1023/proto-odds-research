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
from runtime_db import persist_document

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matches import load_matches                       # noqa: E402

RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "overseas"
BASE = "https://www.betexplorer.com"
GAP = 1.5

# 프로토 리그 → (BetExplorer 슬러그, 선택지 수)
# ⚠️ 축구는 3-way(승/무/패)다. 앞의 두 배당만 쓰면 오버라운드가
#    140% 같은 값으로 나온다(첫 측정의 K리그1 오류가 이것이었다).
SOURCES = {
    "KBO": ("/baseball/south-korea/kbo", 2),
    "MLB": ("/baseball/usa/mlb", 2),
    "NPB": ("/baseball/japan/npb", 2),
    "K리그1": ("/football/south-korea/k-league-1", 3),
}

# 과거 시즌은 슬러그에 연도가 붙는다: /kbo-2025/results/
SEASONS = [2026, 2025, 2024, 2023]

_TAG = re.compile(r"<[^>]+>")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml"})
    return s


def parse_results(html: str, today: pd.Timestamp | None = None,
                  nway: int = 2, season: int | None = None) -> list[dict]:
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
        if not (m_a and m_sc) or len(odds) < nway:
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
                # 과거 시즌 페이지면 그 시즌 연도를 쓴다
                yr = season if season is not None else (
                    today.year - (1 if mm > today.month else 0))
                try:
                    date = pd.Timestamp(year=yr, month=mm, day=dd)
                except ValueError:
                    date = None
        rows.append({
            "home_en": home_en, "away_en": away_en,
            "date": date.isoformat() if date is not None else None,
            "home_score": int(m_sc.group(1)), "away_score": int(m_sc.group(2)),
            "odds": [float(o) for o in odds[:nway]],
        })
    return rows


def collect(sess: requests.Session, seasons=None) -> dict:
    """리그 × 시즌으로 결과 페이지를 훑는다.

    현재 시즌은 슬러그 그대로, 과거 시즌은 `-YYYY` 를 붙인다.
    """
    RAW.mkdir(parents=True, exist_ok=True)
    seasons = seasons or SEASONS
    out = {}
    cur = pd.Timestamp.today().year
    for league, (slug, nway) in SOURCES.items():
        rows = []
        for yr in seasons:
            url = f"{BASE}{slug}{'' if yr == cur else f'-{yr}'}/results/"
            try:
                r = sess.get(url, timeout=25)
                got = parse_results(r.text, nway=nway, season=yr) \
                    if r.status_code == 200 else []
            except Exception as e:                   # noqa: BLE001
                print(f"    [{league} {yr}] 오류 {type(e).__name__}")
                got = []
            rows += got
            time.sleep(GAP)
        # 중복 제거 (날짜+팀)
        seen, uniq = set(), []
        for x in rows:
            k = (x.get("date"), x["home_en"], x["away_en"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(x)
        out[league] = uniq
        ov = [sum(1 / o for o in x["odds"]) for x in uniq
              if x["odds"] and all(o > 1 for o in x["odds"])]
        pay = 100 / np.mean(ov) if ov else float("nan")
        print(f"  {league:8} {len(uniq):5d}경기 ({nway}-way) · 해외 환급률 {pay:.2f}%")
        document_name = {"K리그1": "overseas_kleague1"}.get(
            league, f"overseas_{league.lower()}")
        persist_document(document_name, uniq,
                         RAW / f"{league}.json", indent=None)
    return out


def compare(data: dict) -> None:
    """프로토 배당과 해외 배당을 같은 경기에서 맞대본다."""
    from matches import GAMES, _DATE_RE, _away, _home
    # ⚠️ 축구는 프로토도 3-way(승무패)다. 2-way 승패만 보면 축구가 통째로 빠진다.
    proto = pd.read_csv(GAMES)
    proto = proto[(~proto["is_void"].astype(bool))
                  & (((proto["market_family"] == "승패") & (proto["n_way"] == 2))
                     | ((proto["market_family"] == "승무패") & (proto["n_way"] == 3)))
                  & (proto["result"].isin(["홈승", "홈패", "무승부"]))]
    parts = proto["odds"].str.split(",", expand=True)
    proto = proto.assign(p_home=pd.to_numeric(parts[0], errors="coerce"),
                         p_draw=pd.to_numeric(parts[1], errors="coerce"),
                         p_away=pd.to_numeric(parts[2], errors="coerce"))
    # 2-way 는 두 번째 칸이 원정이다
    two = proto["n_way"] == 2
    proto.loc[two, "p_away"] = pd.to_numeric(parts[1], errors="coerce")[two]
    proto.loc[two, "p_draw"] = np.nan
    hs, aw = proto["home"].map(_home), proto["away"].map(_away)
    proto = proto.assign(home_team=[t for t, _ in hs], home_score=[s for _, s in hs],
                         away_score=[s for s, _ in aw], away_team=[t for _, t in aw])
    md = proto["date_text"].astype(str).str.extract(_DATE_RE)
    proto = proto.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                         _dd=pd.to_numeric(md[1], errors="coerce"))
    proto = proto.dropna(subset=["_mm", "_dd", "p_home", "p_away",
                                 "home_score", "away_score"])
    proto = proto[proto["n_way"].eq(2) | proto["p_draw"].notna()]
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
        nway = SOURCES.get(league, (None, 2))[1]
        for x in rows:
            if len(x["odds"]) < nway or any(o <= 1 for o in x["odds"]):
                continue
            if not x.get("date"):
                continue
            key = (pd.Timestamp(x["date"]).date(), x["home_score"], x["away_score"])
            cands = idx.get(key, [])
            if len(cands) != 1:
                continue          # 날짜+스코어로 유일하지 않으면 버린다
            c = cands[0]
            if int(c.n_way) != nway:
                continue                  # 선택지 수가 다르면 비교 불가
            o_ov = x["odds"][:nway]
            ov_o = sum(1 / o for o in o_ov)
            ov_p = (1 / c.p_home + 1 / c.p_away
                    + (1 / c.p_draw if nway == 3 else 0.0))
            allrows.append({
                "league": league, "date": c.date,
                "proto_home": c.p_home, "proto_away": c.p_away,
                "proto_draw": (c.p_draw if nway == 3 else np.nan),
                "os_home": o_ov[0], "os_away": o_ov[-1],
                "os_draw": (o_ov[1] if nway == 3 else np.nan),
                "p_proto": (1 / c.p_home) / ov_p,     # devig(multiplicative)
                "p_os": (1 / o_ov[0]) / ov_o,
                "result": c.result,
                "pay_proto": 100 / ov_p, "pay_os": 100 / ov_o,
                "won": 1.0 if c.result == "홈승" else 0.0,
                "n_way": nway,
            })
            matched += 1
        print(f"  {league:8} 해외 {len(rows):4d}경기 → 프로토 결합 {matched:4d}")

    if not allrows:
        print("\n결합된 경기가 없습니다. 스코어 매칭이 유일하지 않았습니다.")
        return
    df = pd.DataFrame(allrows)
    df["edge"] = df["p_os"] - df["p_proto"]        # 해외가 더 높게 보는 정도

    # ⚠️ 3-way 를 2-way 로직으로 계산하면 안 된다.
    #    (1 − p_홈) 은 원정 확률이 아니라 '무승부 + 원정'이고,
    #    적중 판정에서 원정 베팅이 무승부에도 맞은 것으로 처리된다.
    #    실제로 그렇게 계산했더니 ROI +64% 라는 불가능한 값이 나왔다.
    #    → 선택지를 하나씩 펼쳐서 각각 devig 확률·배당·적중을 맞춘다.
    legs = []
    for r in df.itertuples():
        if r.n_way == 2:
            ov_o = 1 / r.os_home + 1 / r.os_away
            opts = [("홈", r.proto_home, (1 / r.os_home) / ov_o, r.result == "홈승"),
                    ("원정", r.proto_away, (1 / r.os_away) / ov_o, r.result == "홈패")]
        else:
            ov_o = 1 / r.os_home + 1 / r.os_draw + 1 / r.os_away
            opts = [("홈", r.proto_home, (1 / r.os_home) / ov_o, r.result == "홈승"),
                    ("무", r.proto_draw, (1 / r.os_draw) / ov_o, r.result == "무승부"),
                    ("원정", r.proto_away, (1 / r.os_away) / ov_o, r.result == "홈패")]
        for name, po, p_os, hit in opts:
            if not po or po <= 1:
                continue
            legs.append({"league": r.league, "n_way": r.n_way, "sel": name,
                         "odds": po, "p_os": p_os, "ev": p_os * po - 1,
                         "won": bool(hit)})
    L = pd.DataFrame(legs)

    print(f"\n총 결합 {len(df):,}경기 · 선택지 {len(L):,}개")
    print(f"  프로토 평균 환급률 {df['pay_proto'].mean():.2f}%")
    print(f"  해외   평균 환급률 {df['pay_os'].mean():.2f}%")
    print(f"\n괴리(해외확률 − 프로토확률) 분포")
    for k, v in df["edge"].abs().quantile([0.5, 0.75, 0.9, 0.95, 0.99]).items():
        print(f"    {int(k*100)}분위 {v:.4f} ({v*100:.2f}%p)")

    print(f"\n⭐ 해외 확률로 계산한 프로토 베팅 EV (선택지 단위)")
    print(f"  EV > 0 인 선택지: {(L['ev'] > 0).sum():,} / {len(L):,} "
          f"({(L['ev'] > 0).mean():.1%})")
    rng = np.random.default_rng(42)
    for th in (0.0, 0.02, 0.05, 0.10):
        sel = L[L["ev"] > th]
        if len(sel) < 20:
            continue
        prof = np.where(sel["won"], sel["odds"] - 1, -1.0)
        idx = rng.integers(0, len(prof), size=(4000, len(prof)))
        d = prof[idx].mean(axis=1)
        lo, hi = np.quantile(d, [0.025, 0.975])
        print(f"    EV>{th:>4.0%}: {len(sel):4d}건 · 적중 {sel['won'].mean():5.1%} · "
              f"ROI {prof.mean():+7.2%}  95%CI [{lo:+.2%}, {hi:+.2%}]")
    print("\n  기준선: 프로토에 아무거나 걸면 약 −12%")
    print("\n  ※ 표본이 작으면 ROI 는 참고치일 뿐이다. 핵심은 괴리 분포다.")


def main() -> int:
    sess = _session()
    seasons = [int(a) for a in sys.argv[1:] if a.isdigit()] or None
    print(f"해외 배당 수집 (BetExplorer) · 시즌 {seasons or SEASONS}")
    data = collect(sess, seasons)
    compare(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
