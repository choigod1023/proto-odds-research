"""전 마켓 통합 픽 생성 — 경기마다 '가장 나은 하나'를 고른다.

기존 generate_picks.py 의 한계
------------------------------
승패(2-way)만 봤다. 그건 프로토 물량의 24%다.
그리고 승패 확률만으론 **"근소 우위"를 표현할 방법이 없다.**

이 버전은 스코어 분포에서 **전 마켓을 계산하고 하나만 추천**한다.

    P(홈=i, 원정=j)
      → 승패 · 승무패 · 언더오버(라인별) · 핸디캡(라인별) · 승①패
      → 각 선택지의 기대 손익 = 모델확률 × 배당 − 1
      → 그중 최선 하나

추천 점수 — 기대 손익만 보지 않는다
-----------------------------------
`마켓선택.md` 실측:
  · 박빙(45~55%)은 어느 마켓이든 −13% 이하 → **판단이 안 서면 추천하지 않는다**
  · 강한 판단에서 승무패(3-way)는 −19.8% → **감점**
  · 물량 몰리는 라인(핸디 −1.0, 언더오버 2.5)이 가장 촘촘 → **감점**
  · 모델 괴리가 0.3 넘으면 기회가 아니라 **모델 고장 신호** → **제외**

용어
----
사이트에 나가는 값은 전부 쉬운 말로 바꾼다.
    EV        → 예상 손익
    devig     → (표시하지 않음)
    이상치 의심 → 계산 신뢰 낮음
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import SEL_NAMES                                          # noqa: E402
from commentary import josa, make_preview, make_short               # noqa: E402
import commentary_llm                                               # noqa: E402
from devig import market_probabilities                              # noqa: E402
from recommendation_policy import automatic_selection_exclusion_reason  # noqa: E402
from player_info import game_index, match_game                       # noqa: E402
from team_form import (build_forms, h2h_text, load_history,         # noqa: E402
                       set_rest_days)
from score_dist import (joint, p_handicap, p_margin_band, p_odd,    # noqa: E402
                        p_one_run, p_over, p_win)
from snapshot import UNPLAYED, _fetch, find_live_rounds             # noqa: E402
from wisetoto import CACHE, _session                                # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
# ⚠️ GitHub Pages 가 서빙하는 건 `docs/` 다. `web/` 에 쓰면 만들어도 사이트에
#    안 나온다 — 전 마켓 픽이 여태 화면에 없던 이유가 이것이었다.
OUT = ROOT / "docs" / "data"

WINDOW = 20
_LINE = re.compile(r"([-+]?\d+\.?\d*)")

# 실측 기반 감점 (findings/마켓선택.md)
CROWDED = {("핸디캡", -1.0), ("언더오버", 2.5)}    # 물량 몰려 촘촘한 라인

# ⚠️ 괴리 상한 — 이 프로젝트에서 가장 중요한 숫자다.
#
# 정산 114경기로 실측한 결과:
#     괴리 ≤0.02  n=76   수익률  −3.53%
#     괴리 ≤0.05  n=106  수익률 −35.02%
#     제한 없음    n=114  수익률 −23.19%
#
# **모델이 시장과 다르다고 말하는 순간 그 판단이 틀렸다.**
# market_scan.py 에서 모델이 전 마켓에서 프로토에 진 것의 직접적 귀결이다.
# EV 최대화로 고르면 '모델이 가장 크게 틀린 경기'를 고르게 된다 — 역선택이다.
#
# 그래서 상한을 0.02 로 조인다. 이건 사실상 **시장에 동의할 때만 본다**는 뜻이고,
# 모델이 시장을 이기기 전까지는 이게 정직한 운영이다.
MAX_SANE_GAP = 0.02


def clean(x: str) -> str:
    """팀명 옆에 붙은 숫자를 벗긴다.

    🔴 **소수점을 빠뜨리면 안 된다.** 원래 `-?\\d+` 였는데 핸디캡 행의
       "세이부 5.5"·"한신 -0.5" 를 못 벗겨서 팀명이 그대로 남았다.
       경기 키가 `리그|홈|원정|날짜` 라서 **핸디캡 마켓이 별개 경기로 쪼개졌고**,
       사이트 484경기 중 **162건(33%)이 유령**이었다.
       (`matches.py` 맨 위의 "두산 12" 함정과 같은 부류다 — 팀명에 숫자가 붙는데
        정규화를 부분만 하면 조용히 행이 갈린다.)
    """
    s = re.sub(r"^\s*-?\d+(?:\.\d+)?\s+", "", str(x).strip())
    return re.sub(r"\s+-?\d+(?:\.\d+)?\s*$", "", s).strip()


# 점수를 믿을 수 있는 마켓 — 아래 참조
SCORE_OK = ("승패", "승무패", "승①패", "승⑤패")


def score_of(home: str, away: str, family: str) -> list | None:
    """`home`/`away` 문자열에 박혀 있는 **실제 점수**를 꺼낸다.

    아카이브는 정산된 경기의 팀명 옆에 점수를 붙여 준다 —
        home "야쿠르트 5" · away "3 히로카프"  →  5:3

    🔴 **핸디캡 행에서 꺼내면 안 된다.** 거기 홈 숫자는 핸디를 더한 **보정 점수**다.
       같은 경기의 실측:
           승패      "주니치 3"   / "2 요코베이"   → 3:2  (진짜)
           핸디캡+2.5 "주니치 5.5" / "2 요코베이"   → 5.5:2 (3+2.5, 가짜)
       언더오버·홀짝 행에는 숫자가 아예 없다.
       그래서 `SCORE_OK` 마켓에서만 꺼낸다.
    """
    if family not in SCORE_OK:
        return None
    h = re.search(r"\s+(\d+)\s*$", str(home))
    a = re.match(r"^\s*(\d+)\s+", str(away))
    if not (h and a):
        return None
    return [int(h.group(1)), int(a.group(1))]


def shot_form() -> dict:
    """팀별 최근 슈팅 폼 — **배당 시점에 아는 과거 정보**다.

    ⚠️ 우위로는 못 쓴다. 시장 확률 위에 얹으면 오히려 나빠진다
       (검증 114경기: 시장 0.20362 → +유효슈팅차 0.24545).
       다만 **과정지표가 결과지표보다는 낫다**는 건 재현됐다
       (유효슈팅 0.24545 < 득실차 0.25423). 경기 내용으로는 쓸 값이다.
    """
    f = ROOT / "data" / "raw" / "detail" / "kleague_shots_2023_2026.json"
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}
    from collections import deque as _dq
    sf: dict = defaultdict(lambda: _dq(maxlen=10))
    sa: dict = defaultdict(lambda: _dq(maxlen=10))
    gf: dict = defaultdict(lambda: _dq(maxlen=10))
    for g in sorted(raw.values(), key=lambda x: x["date"]):
        d = g.get("data") or {}
        H, A = d.get("home") or {}, d.get("away") or {}
        if H.get("sog") is None or A.get("sog") is None:
            continue
        for team, me, op in ((g["home"], H, A), (g["away"], A, H)):
            sf[team].append(me["sog"]); sa[team].append(op["sog"]); gf[team].append(me["goals"])
    out = {}
    for t, v in sf.items():
        if len(v) < 5:
            continue
        s_for, s_ag = float(np.mean(v)), float(np.mean(sa[t]))
        g_for = float(np.mean(gf[t]))
        out[str(t)] = {
            "sog": round(s_for, 1), "sog_a": round(s_ag, 1),
            # 결정력 — 유효슈팅 대비 실제 득점. 낮으면 '만들고도 못 넣는다'
            "conv": round(g_for / s_for, 3) if s_for else None,
        }
    return out


# 리그 등급 — 국내 축구만. 그 해 가장 많이 뛴 리그가 그 팀의 등급이다.
TIER = {"K리그1": 1, "K리그2": 2}
# 이종등급 대결에서 상위등급이 실제로 앞서는 폭.
# 실측 42경기: +0.500골 · 95%CI [+0.167, +0.857] · p(≤0)=0.002 · 3년 모두 양수.
# (동일등급 홈 이점 +0.125골과 비교하면 4배다)
TIER_EDGE = 0.50


def team_tiers() -> dict:
    """(연도, 팀) → 등급. 없으면 0(모름)."""
    from matches import load_matches
    from collections import Counter
    m = load_matches()
    m = m[m["sport"] == "sc"]
    cnt: Counter = Counter()
    for r in m.itertuples():
        cnt[(r.year, r.home_team, r.league)] += 1
        cnt[(r.year, r.away_team, r.league)] += 1
    best: dict = {}
    for (y, t, lg), n in cnt.items():
        k = (int(y), str(t))
        if k not in best or n > best[k][1]:
            best[k] = (lg, n)
    return {k: TIER.get(v[0], 0) for k, v in best.items()}


def lineup_profiles() -> dict:
    """K리그 팀별 로테이션 성향 — 해설에 쓸 '이 팀은 어떤 팀인가'.

    ⚠️ 우위로는 못 쓴다. 리그 안에서 교체 인원과 결과는 무관했다
       (승률 0–1명 35.2% · 2–3명 37.1% · 4–5명 34.5% · 6명+ 35.9%, 단조성 없음).
       그래도 **경기 내용**으로는 쓸 수 있다 — 어떤 팀은 선발을 계속 갈고
       어떤 팀은 한 XI 로 간다. 라인업이 킥오프 1시간 전에 나온다는 건
       '이번 경기 예측에 늦다' 는 뜻이지 '과거를 못 본다' 는 뜻이 아니다.
    """
    f = ROOT / "data" / "processed" / "lineup_soccer.csv"
    if not f.exists():
        return {}
    try:
        d = pd.read_csv(f)
    except Exception:
        return {}
    d = d.dropna(subset=["churn"])
    out = {}

    def keys(name: str) -> set:
        """프로토와 네이버가 팀 이름을 다르게 쓴다(김천상무↔김천, 광주FC↔광주).
        접미사를 떼어 둘 다 등록한다."""
        n = str(name)
        ks = {n}
        for suf in ("FC", "상무", "유나", "아이", "시티", "삼성", "현대", "SK"):
            if n.endswith(suf) and len(n) > len(suf):
                ks.add(n[: -len(suf)])
        return ks

    for team, g in d.groupby("team"):
        if len(g) < 20:            # 표본이 적으면 성향이라 부를 수 없다
            continue
        rec = {
            "churn": round(float(g["churn"].mean()), 1),
            "reserve": round(float(g["n_reserve"].mean()), 1),
            "form_change": round(float(g["formation_changed"].mean()), 2),
            "formation": g["formation"].value_counts().index[0],
            "n": int(len(g)),
        }
        for k in keys(team):
            out.setdefault(k, rec)
    return out


def starters() -> dict:
    """날짜·팀 별칭으로 정규화한 경기별 선수 정보 인덱스.

    ⚠️ 왜 붙이나: 선발은 **경기마다 바뀌고 시장이 늦게 반영할 수 있는** 몇 안 되는
       정보다(피처 심사 3조건 통과). 실측으로는 시장을 못 이겼지만
       (Brier +0.006), **사람이 경기를 이해하는 데는 필요한 정보다.**
       숫자가 시장을 못 이긴다는 것과 화면에 안 보여줘도 된다는 건 다른 얘기다.

    팀 조합만 키로 쓰면 MLB 3연전의 어제 선발을 오늘 경기에 붙일 수 있다. 날짜를
    반드시 포함하고, team_map 의 검증된 프로토↔자료원 별칭을 통과시킨다.
    """
    return game_index()


def team_lambdas() -> dict:
    """리그·팀별 + **팀별(리그 무관)** 최근 득실 → λ 재료.

    ⚠️ 왜 팀별 풀링까지 만드나 — 컵대회 때문이다.
       λ 키를 (리그, 팀)으로만 두면 컵대회는 경기 수가 적어 8경기 문턱을 영영 못 넘는다.
       FC서울은 K리그1 에 20경기가 있는데도 (한국FA컵, FC서울)로는 4경기뿐이다.
       그 결과 한국FA컵 64건이 **전부** 버려지고 있었다.

       팀 단위로 풀링하면 예측 가능한 컵 경기가 **64건 → 1,032건(16배)** 이 된다.

    ⚠️ 단, 컵은 모델이 더 못 맞힌다 — 로테이션과 이종등급 대결 때문이다.
       실측(승무패 Brier, 모델−시장): 리그 +0.028 vs **컵 +0.043**, 4년 내내 일관.
       그래서 값은 내되 `lam_src="풀링"` 으로 표시하고 신뢰도를 낮게 본다.
       (컵 압축 자체를 보정하려 했으나 기울기 차이 −0.173 의 95%CI 가
        [−0.389, +0.055] 로 0 을 포함하고 2026 년엔 부호가 반대라 넣지 않았다.)
    """
    from matches import load_matches
    m = load_matches().sort_values("date")
    gf: dict = defaultdict(lambda: deque(maxlen=W_LONG))
    ga: dict = defaultdict(lambda: deque(maxlen=W_LONG))
    gfT: dict = defaultdict(lambda: deque(maxlen=W_LONG))   # 팀 단위(리그 무관)
    gaT: dict = defaultdict(lambda: deque(maxlen=W_LONG))
    for r in m.itertuples():
        y = int(r.year)
        gf[(r.league, r.home_team)].append((y, r.home_score))
        ga[(r.league, r.home_team)].append((y, r.away_score))
        gf[(r.league, r.away_team)].append((y, r.away_score))
        ga[(r.league, r.away_team)].append((y, r.home_score))
        gfT[r.home_team].append((y, r.home_score)); gaT[r.home_team].append((y, r.away_score))
        gfT[r.away_team].append((y, r.away_score)); gaT[r.away_team].append((y, r.home_score))
    return {"gf": gf, "ga": ga, "gfT": gfT, "gaT": gaT,
            "season": int(m["year"].max())}


W_LONG = 40                 # 가중 평균에 쓰는 최대 경기 수
SEASON_BOOST = 2.0          # 이번 시즌 경기에 주는 가중


def _wmean(rec, season: int) -> float:
    """이번 시즌 경기에 2배 가중한 평균.

    ⚠️ 왜 균등이 아닌가 — 실측으로 정했다. 축구 승무패 8,026건(2025~) 검증:
         균등20(구)      Brier 0.62113
         지수감쇠 10      0.61973
         **시즌가중 2배   0.61947**  ← 채택
         지수+시즌 2배    0.62069   (섞으면 오히려 나빠진다)
       그리고 **네 해 모두** 균등20보다 나았다(+3.1 / +1.3 / +1.6 / +1.8, ×1000).
       시장과의 격차를 약 5% 좁힌다 — 여전히 못 이기지만 방향은 확실하다.
    """
    r = list(rec)[-W_LONG:]
    w = np.array([SEASON_BOOST if y == season else 1.0 for y, _ in r])
    v = np.array([x for _, x in r], dtype=float)
    return float((v * w).sum() / w.sum())


HOME_MULT = {"bs": 1.03, "sc": 1.12, "bk": 1.02, "vl": 1.05}


def lambdas_for(st: dict, league: str, home: str, away: str, sport: str):
    """(λ홈, λ원정, 출처). 리그 키가 얇으면 **팀 단위 풀링**으로 떨어진다."""
    kh, ka = (league, home), (league, away)
    if len(st["gf"][kh]) >= 8 and len(st["gf"][ka]) >= 8:
        H = (st["gf"][kh], st["ga"][kh]); A = (st["gf"][ka], st["ga"][ka]); src = "리그"
    elif len(st["gfT"][home]) >= 8 and len(st["gfT"][away]) >= 8:
        # 컵대회 등 — 그 팀이 다른 리그에서 쌓은 기록을 끌어온다
        H = (st["gfT"][home], st["gaT"][home]); A = (st["gfT"][away], st["gaT"][away])
        src = "풀링"
    else:
        return None
    yr = st["season"]
    hm = HOME_MULT.get(sport, 1.05)
    lh = (_wmean(H[0], yr) + _wmean(A[1], yr)) / 2 * hm
    la = (_wmean(A[0], yr) + _wmean(H[1], yr)) / 2
    return float(lh), float(la), src


# 선택지 이름은 bets.SEL_NAMES 가 정본이다 (사본을 만들지 말 것).

def market_probs(M, fam: str, nw: int, line: float | None):
    if fam == "승패" and nw == 2:
        h, _, a = p_win(M)
        s = h + a
        return [h / s, a / s] if s > 0 else None
    if fam == "승무패" and nw == 3:
        return list(p_win(M))
    if fam == "언더오버" and nw == 2 and line is not None:
        po = p_over(M, line)
        return [1 - po, po]
    if fam == "핸디캡" and line is not None:
        w, d, l = p_handicap(M, line)
        if nw == 2:
            s = w + l
            return [w / s, l / s] if s > 0 else None
        return [w, d, l]
    if fam == "승①패" and nw == 3:
        return list(p_one_run(M))
    if fam == "승⑤패" and nw == 3:
        return list(p_margin_band(M, 5))
    if fam == "홀짝" and nw == 2:
        po = p_odd(M)
        return [po, 1 - po]              # [홀, 짝]
    return None


def _selftest() -> int:
    """실제 데이터에 나오는 모든 (마켓, 선택지수) 조합을 사이트가 그릴 수 있나.

    이게 없으면 새 마켓이 생겼을 때 **사이트에 'sel0/sel1' 이 그대로 나간다.**
    실제로 승⑤패·홀짝이 그랬다 — 분석 코드만 고치고 생성기를 안 고쳤다.
    """
    import numpy as _np
    df = pd.read_csv(ROOT / "data/processed/games.csv")
    # 취소 경기는 배당이 1.00/1.00 이고 사이트에 올라가지 않는다.
    # (승①패인데 n_way=2 인 29건이 전부 이것이었다 — 진짜 구멍이 아니다)
    if "is_void" in df.columns:
        df = df[~df["is_void"].astype(str).str.lower().isin(("true", "1"))]
    df = df[df["result"].astype(str) != "취소"]
    combos = (df.groupby(["market_family", "n_way"]).size()
                .sort_values(ascending=False))
    M = joint(4.5, 4.2, "bb")
    fails = []
    # 배당이 없는 행(n_way=0)과 미분류는 애초에 사이트에 안 올라간다
    SKIP = {"미분류"}
    # 전반 마켓은 **일부러** 모델 확률이 없다. 풀게임 분포로 값을 매기면 가짜 우위가 나온다.
    UNPRICED_OK = "전반"
    print("사이트 렌더 가능성 검사")
    for (fam, nw), n in combos.items():
        nw = int(nw)
        if nw == 0 or fam in SKIP:
            print(f"  ⏭ {fam:<6} {nw}-way {n:>7,}건 (사이트 대상 아님)")
            continue
        if fam.startswith(UNPRICED_OK):
            names = SEL_NAMES.get((fam, nw))
            ok = names is not None and len(names) == nw
            print(f"  {'⏭' if ok else '🔴'} {fam:<8} {nw}-way {n:>7,}건 "
                  f"(전반 — 모델 가격 없음이 정상"
                  + ("" if ok else ", **이름이 없어 sel0 노출**") + ")")
            if not ok:
                fails.append((fam, nw, n, ["전반인데 이름없음"]))
            continue
        names = SEL_NAMES.get((fam, nw))
        line = 1.5 if fam in ("언더오버", "핸디캡") else None
        pm = market_probs(M, fam, nw, line)
        bad = []
        if names is None:
            bad.append("이름없음→sel0/sel1 노출")
        elif len(names) != nw:
            bad.append(f"이름 {len(names)}개 ≠ {nw}")
        if pm is None:
            bad.append("모델확률 없음→비교 불가")
        elif len(pm) != nw or abs(sum(pm) - 1) > 1e-6:
            bad.append(f"확률 len={len(pm)} 합={sum(pm):.4f}")
        mark = "✅" if not bad else "🔴"
        print(f"  {mark} {fam:<6} {nw}-way {n:>7,}건"
              + (f"  ← {', '.join(bad)}" if bad else f"  {'/'.join(names)}"))
        if bad:
            fails.append((fam, nw, n, bad))
    # ---- 팀명 정규화 — 유령 경기 회귀 검사
    # 🔴 `clean()` 이 소수점을 못 벗겨서 핸디캡 마켓이 별개 경기로 갈렸고,
    #    사이트 484경기 중 162건(33%)이 유령이었다. 다시 나면 여기서 잡는다.
    print("\n팀명 정규화 검사")
    for raw, want in (("야쿠르트 5", "야쿠르트"), ("3 히로카프", "히로카프"),
                      ("세이부 5.5", "세이부"), ("한신 -0.5", "한신"),
                      ("-0.5 한신", "한신"), ("2.5 두산", "두산"), ("LG", "LG")):
        got = clean(raw)
        if got != want:
            fails.append(("clean", 0, 0, [f"{raw!r}→{got!r} (기대 {want!r})"]))
            print(f"  🔴 clean({raw!r}) = {got!r} ≠ {want!r}")
    # ⚠️ "끝에 숫자가 있으면 실패" 로 짜면 **샬케04·마인츠05** 가 걸린다 —
    #    팀명 자체에 숫자가 붙는다. 잡아야 하는 건 **띄어쓰기로 분리된 숫자 토큰**이다.
    _NUMTOK = re.compile(r"(^\s*-?\d+(?:\.\d+)?\s+)|(\s+-?\d+(?:\.\d+)?\s*$)")
    left = [x for c in ("home", "away") for x in df[c].astype(str).map(clean)
            if _NUMTOK.search(x)]
    if left:
        fails.append(("clean", 0, len(left), ["벗긴 뒤에도 숫자 토큰이 남았다"]))
        print(f"  🔴 정규화 후에도 숫자 토큰이 남은 행 {len(left):,}건 — 예: {left[:3]}")
    else:
        print(f"  ✅ 정수·소수·앞뒤 모두 벗긴다 (유령 경기 없음, {len(df)*2:,}행 검사)")
        print("     ※ 샬케04·마인츠05 처럼 팀명에 붙은 숫자는 남기는 게 맞다")

    # ---- 모지바케 — 수집 시 charset 추측이 빗나가면 여기서 잡힌다
    # 🔴 11개 회차 3,429행이 통째로 깨진 채 저장돼 있었다. `result` 까지 깨져서
    #    ('нҷҲмҠ№'=홈승) 그 행들이 모든 분석에서 조용히 빠졌다.
    _MOJI = re.compile(r"[Ѐ-ӿĀ-ſ]")
    dirty = {c: sum(1 for v in df[c].tolist() if isinstance(v, str) and _MOJI.search(v))
             for c in ("home", "away", "result", "league")}
    tot = sum(dirty.values())
    if tot:
        fails.append(("모지바케", 0, tot, [f"{k}={v}" for k, v in dirty.items() if v]))
        print(f"  🔴 모지바케 {tot:,}건 — {dirty}")
        print("     → wisetoto.repair_mojibake 가 못 되돌린 계열이 있다")
    else:
        print("  ✅ 모지바케 없음 (팀명·결과·리그에 키릴/라틴확장 0건)")

    if fails:
        tot = sum(f[2] for f in fails)
        print(f"\n🔴 {len(fails)}개 항목 {tot:,}건이 사이트에서 깨진다")
        return 1
    print("\n✅ 모든 마켓이 이름·확률 둘 다 있고, 팀명도 깨끗하다")
    return 0


def _form_dict(f) -> dict | None:
    if f is None:
        return None
    return {"w": f.w, "l": f.l, "d": f.d, "last10": f.last10_str,
            "streak": f"{f.streak_n}{f.streak_kind}" if f.streak_n >= 2 else "",
            "home": f"{f.home_w}-{f.home_l}", "away": f"{f.away_w}-{f.away_l}",
            "avg_scored": round(f.avg_scored, 1) if f.avg_scored else None,
            "avg_conceded": round(f.avg_conceded, 1) if f.avg_conceded else None,
            "rest_days": f.rest_days, "trend": f.trend}


def _as_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _market_context(options: list[dict]) -> dict:
    """해설이 실제 발매선과 교차 마켓의 충돌을 읽도록 필요한 값만 뽑는다.

    이 문맥은 새로운 예측 점수가 아니다. 승패 시장의 확신이 득점 모델·핸디캡·
    마진 밴드와 동시에 어긋나는지 설명하기 위한 진단값이다.
    """
    context: dict = {}

    totals = [o for o in options if o.get("market") == "언더오버"]
    if totals:
        first = totals[0]
        line = _as_float(first.get("line"))
        if line is None:
            m = _LINE.search(str(first.get("label") or ""))
            line = _as_float(m.group(1)) if m else None
        if line is not None:
            context["total"] = {
                "line": line,
                "label": first.get("label") or f"U/O {line:g}",
            }

    handicap = [o for o in options if o.get("market") == "핸디캡"]
    if handicap:
        home = next((o for o in handicap if o.get("선택") == "핸디홈"), None)
        away = next((o for o in handicap if o.get("선택") == "핸디원정"), None)
        if home and away:
            context["handicap"] = {
                "label": home.get("label") or away.get("label") or "실제 라인",
                "home_market": _as_float(home.get("시장확률")),
                "home_model": _as_float(home.get("모델확률")),
                "away_market": _as_float(away.get("시장확률")),
                "away_model": _as_float(away.get("모델확률")),
            }

    close = next((o for o in options
                  if o.get("market") in ("승①패", "승⑤패")
                  and o.get("선택") in ("1점차", "5점차")), None)
    if close:
        context["margin_band"] = {
            "label": close.get("선택"),
            "close_market": _as_float(close.get("시장확률")),
            "close_model": _as_float(close.get("모델확률")),
        }

    return context


_PRE = ("FC",)
_SUF = ("FC", "HD", "SK", "하나", "상무", "유나", "아이", "시티", "삼성", "현대", "스틸")


def _norm_team(n: str) -> str:
    """프로토와 네이버 표기 차이 — FC서울↔서울, 울산HD↔울산."""
    n = str(n).strip()
    for p in _PRE:
        if n.startswith(p) and len(n) > len(p):
            n = n[len(p):]
            break
    for s in _SUF:
        if n.endswith(s) and len(n) > len(s):
            n = n[: -len(s)]
            break
    return n


_STORY_FAIL: list = []


def _attach_story(g: dict, forms: dict, h2h: dict, st: dict,
                  by_team: dict | None = None, h2h_any: dict | None = None,
                  lineups: dict | None = None, shots: dict | None = None) -> None:
    """경기 하나에 최근폼·상대전적·선발·줄글 해설을 붙인다.

    수치만 있으면 '해석' 이지 '분석' 이 아니다. 모델이 시장을 못 이긴다는 것과
    경기 정보를 안 보여줘도 된다는 건 별개다.
    """
    lg, ht, at = g["league"], g["home"], g["away"]
    by_team = by_team or {}
    fh = forms.get((lg, ht)) or by_team.get(ht)
    fa = forms.get((lg, at)) or by_team.get(at)
    g["form_src"] = "리그" if forms.get((lg, ht)) else ("풀링" if fh else None)
    g["form_home"], g["form_away"] = _form_dict(fh), _form_dict(fa)
    g["h2h"] = h2h_text(h2h, lg, ht, at)
    if not g["h2h"] and h2h_any:
        alt = h2h_any.get((ht, at)) or h2h_any.get((at, ht))
        if alt:
            g["h2h"] = h2h_text(h2h, alt[0], ht, at)
    g["선발"] = match_game(st, lg, g.get("date", ""), ht, at)
    # 라인업 성향 — 예측이 아니라 팀 성질이다
    lp = lineups or {}
    def _lp(name: str):
        n = str(name)
        if n in lp:
            return lp[n]
        for suf in ("FC", "상무", "유나", "아이", "시티", "삼성", "현대", "SK"):
            if n.endswith(suf) and n[: -len(suf)] in lp:
                return lp[n[: -len(suf)]]
        return None

    g["라인업"] = {k: v for k, v in (("home", _lp(ht)), ("away", _lp(at))) if v}
    sh = shots or {}
    g["슈팅폼"] = {k: v for k, v in
                 (("home", sh.get(ht) or sh.get(_norm_team(ht))),
                  ("away", sh.get(at) or sh.get(_norm_team(at)))) if v}

    # make_preview 의 p_market 은 언제나 '홈' 확률이다. 옵션 정렬이 바뀌어 원정이
    # 먼저 와도 방향이 뒤집히지 않게 홈 행을 명시적으로 고른다.
    base_home = next((o for o in g["options"]
                      if o["market"] in ("승패", "승무패") and o["선택"] == "홈"), None)
    o_h = o_a = None
    for o in g["options"]:
        if o["market"] in ("승패", "승무패"):
            if o["선택"] == "홈":
                o_h = o["배당"]
            elif o["선택"] == "원정":
                o_a = o["배당"]
    p_home = g.get("홈승률")
    p_mkt = base_home["시장확률"] if base_home else None
    extra = []
    for side, name in (("home", ht), ("away", at)):
        v = g["라인업"].get(side)
        if not v:
            continue
        # ⚠️ '몇 명 바꿨나' 가 아니라 '주전이 아닌 선수를 몇 명 냈나' 로 쓴다.
        #    실측상 교체 인원 수는 결과와 무관했고(승률 34.5~37.1%),
        #    **비주전 투입**만 단조로 갈렸다(0–1명 45.7% → 6명+ 30.1%).
        if v["reserve"] >= 4.5:
            extra.append(f"{josa(name, '은', '는')} 주전이 아닌 선수를 평균 {v['reserve']}명 선발로 내는 팀이다")
        elif v["reserve"] <= 3.2:
            extra.append(f"{josa(name, '은', '는')} 주전 XI 를 거의 그대로 쓴다(비주전 평균 {v['reserve']}명)")
        if v["form_change"] <= 0.10:
            extra.append(f"{josa(name, '은', '는')} {v['formation']} 를 사실상 고정으로 쓴다")
        elif v["form_change"] >= 0.40:
            extra.append(f"{josa(name, '은', '는')} 포메이션을 경기마다 바꾼다(변경률 "
                         f"{v['form_change']*100:.0f}%)")
    for side, name in (("home", ht), ("away", at)):
        v = g["슈팅폼"].get(side)
        if not v:
            continue
        diff = v["sog"] - v["sog_a"]
        if abs(diff) >= 1.5:
            extra.append(f"{josa(name, '은', '는')} 최근 10경기 유효슈팅 {v['sog']}대 {v['sog_a']}로 "
                         f"내용에서 {'앞선다' if diff > 0 else '밀린다'}")
        if v["conv"] is not None and v["conv"] <= 0.22:
            extra.append(f"{josa(name, '은', '는')} 유효슈팅 대비 득점이 {v['conv']:.2f}로 낮아 "
                         f"만들고도 못 넣는 경기가 있다")
    g["라인업메모"] = ". ".join(extra) + ("." if extra else "")

    try:
        market_context = _market_context(g["options"])
        g["시장문맥"] = market_context
        g["해설"] = make_preview(ht, at, lg, fh, fa, h2h,
                                 p_home if p_home is not None else 0.5,
                                 p_mkt if p_mkt is not None else 0.5,
                                 o_h or 0, o_a or 0, g.get("payout") or 88.0,
                                 0.0, 0.0, sport=g["sport"],
                                 market_context=market_context)
        if g.get("라인업메모"):
            g["해설"] = (g["해설"] or "") + " " + g["라인업메모"]
        # 템플릿 문장을 LLM 이 말투만 다듬는다. 사실은 건드리지 않는다.
        # 키가 없거나·실패하거나·검사에 걸리면 템플릿 원문이 그대로 남는다.
        g["해설"] = commentary_llm.polish(g["해설"])
    except Exception as e:
        # ⚠️ 조용히 삼키면 안 된다. 실제로 make_preview 가 NameError 를 던지는데
        #    해설 0건이 그대로 배포됐다. 화면엔 그냥 '해설 없음' 으로 보인다.
        g["해설"] = None
        _STORY_FAIL.append(f"{g['league']} {g['home']}vs{g['away']}: {type(e).__name__} {e}")


# ⚠️ 퇴화 확률 = 모델이 그 마켓을 못 매긴 것이다.
#    실측 2026-07-29: 배구 언더오버 라인이 140.5~185.5(총 **득점**)인데
#    score_dist 는 배구를 **세트**로 모델링한다. 그래서 p_over 가 그대로 0 이 되고
#    "언더 100% · 예상손익 +76%" 라는 가짜 우위가 화면에 찍혔다.
#    (연구 수치에도 샜다 — 배구 언더오버 모델 Brier 0.489 vs 시장 0.250)
#    유한 배당이 걸린 선택지에 확률 0/1 은 존재할 수 없다. 값을 버린다.
_EPS = 1e-6


def _sane(pm):
    if pm is None:
        return None
    if any((p <= _EPS or p >= 1 - _EPS) for p in pm):
        return None
    return pm


def main() -> int:
    st = team_lambdas()
    sess = _session()
    season = datetime.now().year
    # 최근폼·상대전적·줄글 해설 — 예전엔 generate_picks 가 '승패 2-way' 에만 붙였다.
    # 그래서 언더오버·핸디캡·컵대회 경기는 해설이 **아예 만들어지지 않았다.**
    # ⚠️ season 을 안 넘기면 4년치가 누적된다 (LG 300승 212패 · 시즌 맞대결 32승 24패).
    #    "최근 폼" 이라는 말이 무의미해진다.
    hist = load_history()
    FORMS, H2H = build_forms(hist, season=season)
    STARTERS = starters()
    LINEUPS = lineup_profiles()
    TIERS = team_tiers()
    SHOTFORM = shot_form()
    # ⚠️ build_forms 는 (리그, 팀) 키다 → 컵대회는 폼이 비어 있고, 그러면 해설이
    #    "이번 시즌 기록이 충분히 쌓이지 않았다" 를 양 팀에 대해 두 번 말한 뒤
    #    "53% 우세" 라고 단정하는 자기모순 문장이 된다. 실제로 그랬다.
    #    λ 때와 같은 처리 — 그 팀이 **가장 많이 뛴 리그의 폼**을 끌어온다.
    #    (부산아이는 한국FA컵 3경기지만 K리그2 에 20경기가 있다)
    FORM_BY_TEAM: dict = {}
    for (lg_, tm_), fm_ in FORMS.items():
        n_ = fm_.w + fm_.l + fm_.d
        cur = FORM_BY_TEAM.get(tm_)
        if cur is None or n_ > cur[0]:
            FORM_BY_TEAM[tm_] = (n_, fm_)
    FORM_BY_TEAM = {k: v[1] for k, v in FORM_BY_TEAM.items()}
    # 상대전적도 같은 이유로 리그를 가리지 않고 찾는다
    H2H_ANY: dict = {}
    for k_, v_ in H2H.items():
        if len(k_) == 3:
            H2H_ANY.setdefault((k_[1], k_[2]), (k_[0], v_))
    have = sorted(int(p.stem.replace(".html", ""))
                  for p in (CACHE / str(season)).glob("*.html.gz")) \
        if (CACHE / str(season)).exists() else []
    live = find_live_rounds(sess, season, (max(have) - 3) if have else 1)
    recent = [r for r in have[-3:] if r not in live]
    rounds = sorted(set(live) | set(recent))
    print(f"대상 회차: 발매중 {live} + 최근 {recent}")

    games: dict = {}
    for rnd in rounds:
        for r in (_fetch(sess, season, rnd) or []):
            # ⚠️ 배당이 아직 안 나온 회차를 통째로 버리고 있었다.
            #    프로토는 **경기 목록을 먼저 열고 배당을 나중에 붙인다.**
            #    실측 2026-07-29: 회차 90 의 697행이 전부 odds=[] · n_way=0 이라
            #    '앞으로 있을 경기' 가 사이트에 하나도 안 보였다.
            #    배당이 없어도 대진·최근폼·해설은 보여줄 수 있다.
            if r.is_void:
                continue
            if not r.odds or not r.overround:
                if r.result in UNPLAYED:
                    ht0, at0 = clean(r.home), clean(r.away)
                    lam0 = lambdas_for(st, r.league, ht0, at0, r.sport)
                    k0 = f"{r.league}|{ht0}|{at0}|{r.date_text}"
                    if k0 not in games:
                        games[k0] = {
                            "round": rnd, "date": r.date_text, "league": r.league,
                            "sport": r.sport, "home": ht0, "away": at0,
                            "lam_home": (round(lam0[0], 2) if lam0 else None),
                            "lam_away": (round(lam0[1], 2) if lam0 else None),
                            "lam_src": (lam0[2] if lam0 else None),
                            "no_model": lam0 is None,
                            "no_odds": True,          # 배당 미발표
                            "status": "배당대기", "options": []}
                continue
            if not (1.0 <= r.overround <= 1.40):
                continue
            ht, at = clean(r.home), clean(r.away)
            lam = lambdas_for(st, r.league, ht, at, r.sport)
            # ⚠️ 풀링 λ 는 리그 등급 차이를 못 본다. K리그2 팀의 득점은 약한 상대
            #    기준이라 K리그1 팀과 같은 값처럼 보인다. 그래서 부산아이(2부) vs
            #    FC서울(1부) 에서 모델이 53% (시장 20%) 를 냈다.
            #    실측한 등급 우위(+0.50골)를 양쪽에 절반씩 나눠 얹는다.
            if lam and r.sport == "sc":
                th = TIERS.get((season, _norm_team(ht)), 0) or TIERS.get((season, ht), 0)
                ta = TIERS.get((season, _norm_team(at)), 0) or TIERS.get((season, at), 0)
                if th and ta and th != ta:
                    up_home = th < ta          # 숫자가 작을수록 상위 등급
                    d = TIER_EDGE / 2
                    lh = max(0.15, lam[0] + (d if up_home else -d))
                    la = max(0.15, lam[1] + (-d if up_home else d))
                    lam = (lh, la, lam[2] + "+등급보정")
            # ⚠️ 여기서 continue 하면 **컵대회가 통째로 사라진다.**
            #    λ 키가 (리그, 팀) 이라 한국FA컵·UCL 처럼 경기 수가 적은 대회는
            #    8경기 문턱을 영영 못 넘는다. FC서울은 K리그1 에 20경기가 있는데도
            #    (한국FA컵, FC서울) 로는 4경기뿐이라 탈락했다.
            #    실측 2026-07-29: 363건 중 138건(38%)이 이렇게 조용히 버려졌고
            #    한국FA컵 64건은 **전부** 빠졌다.
            #    → 모델 확률만 비우고 경기 자체는 내보낸다. 배당·등급은 모델이 필요 없다.
            nw = r.n_way
            line = None
            if r.market_family in ("언더오버", "핸디캡"):
                m0 = _LINE.search(str(r.market_label))
                if not m0:
                    continue
                line = float(m0.group(1))
            pm = None
            if lam:
                M = joint(lam[0], lam[1], r.sport)
                pm = _sane(market_probs(M, r.market_family, nw, line))
            if pm is not None and len(pm) != len(r.odds):
                pm = None
            if pm is None:
                pm = [None] * len(r.odds)      # 모델 없음 — 배당·등급만 보여준다

            names = SEL_NAMES.get((r.market_family, nw), tuple(f"sel{i}" for i in range(nw)))
            settled = r.result not in UNPLAYED and r.result != ""

            # ⭐ 경기 단위로 묶는다 — 같은 경기가 여러 상품으로 중복 발매되므로
            gkey = f"{r.league}|{ht}|{at}|{r.date_text}"
            g = games.setdefault(gkey, {
                "round": rnd, "date": r.date_text, "league": r.league,
                "sport": r.sport, "home": ht, "away": at,
                "lam_home": (round(lam[0], 2) if lam else None),
                "lam_away": (round(lam[1], 2) if lam else None),
                "lam_src": (lam[2] if lam else None),
                "no_model": lam is None,
                "status": "정산" if settled else "경기전",
                "score": None, "결과": None,
                "options": []})
            g.pop("no_odds", None)
            if settled:
                g["status"] = "정산"
                # 점수·승패는 경기당 한 번만 잡으면 된다. 여러 마켓 행 중
                # 점수를 믿을 수 있는 행(SCORE_OK)이 나올 때 채운다.
                if g.get("score") is None:
                    sc = score_of(r.home, r.away, r.market_family)
                    if sc:
                        g["score"] = sc
                if g.get("결과") is None and r.market_family in ("승패", "승무패"):
                    g["결과"] = r.result
            elif g["status"] == "배당대기":
                g["status"] = "경기전"

            # ⚠️ 예전엔 (1/o)/ov, 즉 multiplicative devig 였다 — 마진이 모든
            #    선택지에 같은 비율로 얹혀 있다는 가정이다. 실측은 그렇지 않다:
            #    마진이 배당을 따라 단조 증가한다(1.0-1.3 에서 8.7% → 5.0+ 에서 35.9%,
            #    236,637 선택지). 균등이 아니므로 multiplicative 는 **역배 확률을
            #    부풀린다** — 배당 6.6 에서 실측 9.71% 를 13.18% 로, 36% 과대평가했다.
            #
            #    실제 발매 배당 1,129건에 4종을 걸어 실측 참확률((1+ROI)/배당)과
            #    대조한 결과(devig_pick.py):
            #        multiplicative  오차 0.619%p · 역배편향 +1.006%p
            #        additive        0.716%p · −0.670%p
            #        power           1.208%p · −0.972%p   (과교정)
            #        shin            0.613%p · −0.304%p   ← 채택
            #    shin 은 시장에 내부정보 보유자가 비율 z 만큼 있다고 보는 모형이라
            #    역배에 마진이 더 얹히는 현상을 구조적으로 설명한다.
            #
            #    ⚠️ 그래도 5.0+ 는 실측 −35.90% 로 어떤 devig 으로도 다 설명되지 않는다.
            #       그 구간은 등급 D 로 픽에서 이미 걸러진다(lessBadPick 은 확률이
            #       아니라 실측 ROI 로 고른다). 여기서 고치는 건 **표시값의 정직함**이다.
            p_market = market_probabilities(list(r.odds))
            for i, (p, o) in enumerate(zip(pm, r.odds)):
                p_mkt = p_market[i]
                gap = (None if p is None else abs(p - p_mkt))
                g["options"].append({
                    "market": r.market_family, "n_way": nw,
                    "label": r.market_label or "", "line": line,
                    "선택": names[i] if i < len(names) else str(i),
                    "배당": round(o, 2),
                    "모델확률": (None if p is None else round(p, 4)),
                    "시장확률": round(p_mkt, 4),
                    "예상손익": (None if p is None else round(p * o - 1, 4)),
                    "괴리": (None if gap is None else round(gap, 4)),
                    "게임번호": r.game_no,
                    "적중": (None if not settled else _hit(nw, r.result, i)),
                })

    # ---- 경기별 최선 하나 고르기
    out = []
    for g in games.values():
        if g.get("no_odds") and not g["options"]:
            if not g.get("no_model"):
                h0, _, a0 = p_win(joint(g["lam_home"], g["lam_away"], g["sport"]))
                g["홈승률"] = round(h0 / (h0 + a0), 4) if h0 + a0 > 0 else None
            else:
                g["홈승률"] = None
            g["판단"] = "배당 미발표"
            g["추천"] = None
            g["선택지수"] = 0
            _attach_story(g, FORMS, H2H, STARTERS, FORM_BY_TEAM, H2H_ANY, LINEUPS, SHOTFORM)
            out.append(g)
            continue
        # 모델이 없는 경기(컵대회 등)는 배당·등급만 보여준다. 추천은 하지 않는다.
        if g.get("no_model"):
            g["홈승률"] = None
            g["판단"] = "모델 없음 — 배당만"
            for o in g["options"]:
                o["제외"] = "리그 표본이 부족해 모델을 못 세운다 (컵대회 등)"
            g["추천"] = None                 # 추천은 안 하되 **목록에는 남긴다**
            g["선택지수"] = len(g["options"])
            _attach_story(g, FORMS, H2H, STARTERS, FORM_BY_TEAM, H2H_ANY, LINEUPS, SHOTFORM)
            out.append(g)
            continue

        h, _, a = p_win(joint(g["lam_home"], g["lam_away"], g["sport"]))
        p_home = h / (h + a) if h + a > 0 else 0.5
        g["홈승률"] = round(p_home, 4)
        g["판단"] = ("박빙" if 0.45 <= p_home <= 0.55
                     else ("홈 근소" if p_home < 0.60 else "홈 우세")
                     if p_home > 0.55
                     else ("원정 근소" if p_home > 0.40 else "원정 우세"))

        # 같은 마켓 안에서 시장확률이 가장 높은 선택지만 자동 추천 자격이 있다.
        # 3-way에서는 50% 미만이어도 셋 중 1위면 favorite이므로 확률 0.5로 자르지 않는다.
        favorite_by_market: dict[tuple, float] = {}
        for option in g["options"]:
            key = (option["market"], option["label"], option["line"], option["게임번호"])
            favorite_by_market[key] = max(
                favorite_by_market.get(key, 0.0), float(option["시장확률"]))

        best, best_score = None, -9e9
        for o in g["options"]:
            key = (o["market"], o["label"], o["line"], o["게임번호"])
            policy_reason = automatic_selection_exclusion_reason(
                o["market"], o["배당"], o["시장확률"], favorite_by_market[key])
            if policy_reason:
                # 상세에는 남기되 검증 안 된 역배를 자동 추천으로 포장하지 않는다.
                o["제외"] = policy_reason
                continue
            # 모델 확률이 없는 선택지(전반 마켓 등)는 비교 대상이 아니다
            if o["괴리"] is None or o["예상손익"] is None:
                o["제외"] = "모델이 값을 매기지 않는 마켓 (전반전 등)"
                continue
            if o["괴리"] > MAX_SANE_GAP:
                # 모델이 시장과 크게 다르다 = 모델이 틀렸을 확률이 높다
                o["제외"] = "모델·시장 차이가 커서 신뢰 낮음"
                continue
            score = o["예상손익"]
            # 실측 기반 감점
            if 0.45 <= p_home <= 0.55:
                score -= 0.05                      # 박빙은 전 마켓 열위
            if o["market"] == "승무패" and not (0.45 <= p_home <= 0.55):
                score -= 0.04                      # 강한 판단에서 3-way 재앙
            if (o["market"], o["line"]) in CROWDED:
                score -= 0.02                      # 물량 몰린 라인
            o["추천점수"] = round(score, 4)
            if score > best_score:
                best, best_score = o, score
        g["추천"] = best
        g["선택지수"] = len(g["options"])
        _attach_story(g, FORMS, H2H, STARTERS, FORM_BY_TEAM, H2H_ANY, LINEUPS, SHOTFORM)
        out.append(g)

    # 시간순 정렬
    if _STORY_FAIL:
        print(f"🔴 해설 생성 실패 {len(_STORY_FAIL)}건 — 예: {_STORY_FAIL[0]}")
    n_story = sum(1 for g in out if g.get("해설"))
    print(f"해설 {n_story}/{len(out)}건")
    commentary_llm.flush()      # 캐시 저장 + 이번 주기 호출/적중 요약

    out.sort(key=lambda g: (g["date"], g["home"]))
    live_g = [g for g in out if g["status"] in ("경기전", "배당대기")]
    past_g = [g for g in out if g["status"] == "정산"]

    tally = None
    done = [g["추천"] for g in past_g if g.get("추천") and g["추천"].get("적중") is not None]
    if done:
        wins = sum(1 for o in done if o["적중"])
        roi = float(np.mean([(o["배당"] - 1) if o["적중"] else -1.0 for o in done]))
        tally = {"n": len(done), "wins": wins,
                 "hit_rate": round(wins / len(done), 4), "roi": round(roi, 4)}

    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rounds": rounds, "live": live_g, "past": past_g, "tally": tally,
        "note": ("전 마켓(승패·언더오버·핸디캡·승①패)을 스코어 분포에서 계산해 "
                 "경기마다 하나만 골라 보여줍니다."),
        "warning": ("⚠️ 아직 베팅에 쓸 수 없습니다. 모델이 시장보다 부정확해서, "
                    "모델과 시장의 판단이 다를수록 모델이 틀렸을 확률이 높습니다. "
                    "그래서 '시장과 거의 같게 본 경기'만 남겨 두었습니다."),
        "gap_cap": MAX_SANE_GAP,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "picks_v2.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(f"\n경기 {len(out)} (예정 {len(live_g)} / 정산 {len(past_g)})")
    if tally:
        print(f"정산 추천 성적: {tally['wins']}/{tally['n']} "
              f"({tally['hit_rate']:.1%}) · 수익률 {tally['roi']:+.2%}")
    print(f"저장: {OUT / 'picks_v2.json'}")
    for g in live_g[:5]:
        b = g["추천"]
        if b:
            print(f"  {g['date']} {g['league']} {g['home']}vs{g['away']} "
                  f"[{g['판단']}] → {b['market']} {b['label']} {b['선택']} "
                  f"@{b['배당']} 예상손익 {b['예상손익']:+.1%}")
    return 0


def _hit(nw: int, result: str, i: int) -> bool:
    W = {(2, "홈승"): 0, (2, "홈패"): 1, (2, "언더"): 0, (2, "오버"): 1,
         (2, "핸디승"): 0, (2, "핸디패"): 1,
         (3, "홈승"): 0, (3, "무승부"): 1, (3, "홈패"): 2,
         (3, "핸디승"): 0, (3, "핸디무"): 1, (3, "핸디패"): 2, (3, "①"): 1}
    return W.get((nw, result)) == i


if __name__ == "__main__":
    # ⚠️ --selftest 는 main() **앞에서** 분기해야 한다. 뒤에 두면 영영 안 돈다.
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
