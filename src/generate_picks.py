"""오늘자 픽 산출 — 모델 확률 · 기대값 · 프리뷰 코멘트.

⚠️ 현재 모델 성능 (2025~2026 검증, src/picks.py)
      모델 Brier 0.2384  vs  시장 Brier 0.2270  → **시장이 더 정확하다**
      EV>0 픽의 실제 수익률 −15.07%  vs  아무거나 걸기 −12.00%
   즉 **이 모델의 픽을 그대로 따르면 무작위보다 더 잃는다.**

   그럼에도 픽을 산출하는 이유는, 개선의 출발점이 되는 **기록된 기준선**이 필요하기 때문이다.
   산출물에는 이 사실을 그대로 표기한다. 감추면 도구가 아니라 사기가 된다.

산출물: docs/data/picks.json
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commentary import make_preview, make_short          # noqa: E402
from atomic_publish import PublishGuardError, publish_nonempty_json  # noqa: E402
from elo_model import fit_logistic, load_results, prob_home, run_elo  # noqa: E402
from snapshot import UNPLAYED, _fetch, find_live_rounds  # noqa: E402
from team_form import build_forms, form_for_game, h2h_text  # noqa: E402
from wisetoto import CACHE, _session                     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

KST = timezone(timedelta(hours=9))
_KICK_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\([^)]*\)\s*(\d{1,2}):(\d{2})")


def _now_kst() -> datetime:
    return datetime.now(KST)


def _kickoff(date_text: str, season: int) -> datetime | None:
    """'07.28(화) 18:30' → 경기 시작 시각(KST).

    ⚠️ 회차 데이터에는 연도가 없다. 시즌 연도를 붙이되, 12월↔1월 경계에서
       한 해가 틀어지는 걸 막기 위해 지금과 반년 이상 벌어지면 보정한다.
    """
    m = _KICK_RE.match(str(date_text).strip())
    if not m:
        return None
    mm, dd, hh, mi = (int(x) for x in m.groups())
    try:
        ko = datetime(season, mm, dd, hh, mi, tzinfo=KST)
    except ValueError:                       # 2/30 같은 파싱 오류
        return None
    now = _now_kst()
    if ko - now > timedelta(days=180):
        ko = ko.replace(year=season - 1)
    elif now - ko > timedelta(days=180):
        ko = ko.replace(year=season + 1)
    return ko


def _is_recommendable_now(result: str | None, kickoff: datetime | None,
                          now: datetime | None = None) -> bool:
    """사전등록되지 않은 레거시 픽은 시작 전 경기만 추천한다."""
    if result in ("홈승", "홈패"):
        return False
    return kickoff is not None and kickoff > (now or _now_kst())


def _sanitize_existing_document(doc: dict, now: datetime | None = None) -> dict:
    """기존 레거시 산출물에서 시작 전 픽만 보존한다."""
    cutoff = now or _now_kst()
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=KST)
    kept = []
    for pick in doc.get("picks", []):
        try:
            kickoff = datetime.fromisoformat(str(pick.get("kickoff")))
        except (TypeError, ValueError):
            kickoff = None
        if not _is_recommendable_now(pick.get("result"), kickoff, cutoff):
            continue
        pick.update(status="경기전", result=None, hit=None, profit=None)
        kept.append(pick)
    doc["picks"] = kept
    doc["n_picks"] = len(kept)
    doc["rounds"] = sorted({int(pick["round"]) for pick in kept})
    doc["tally"] = None
    doc["tally_status"] = "prediction_ledger_required"
    return doc


def _sanitize_existing_output() -> int:
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    _sanitize_existing_document(doc)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"시작 전 레거시 픽만 보존: {OUT}")
    return 0


OUT = ROOT / "docs" / "data" / "picks.json"

# src/picks.py 백테스트에서 측정한 값 — 산출물에 그대로 싣는다
BACKTEST = {
    "train": "2023–2024", "test": "2025–2026",
    "model_brier": 0.2384, "market_brier": 0.2270,
    "pick_roi": -0.1507, "pick_ci": [-0.1847, -0.1148],
    "baseline_roi": -0.12, "n_picks": 4530,
    "verdict": "모델이 시장보다 부정확하며, 픽의 과거 수익률이 기준선보다 나쁘다. "
               "실전 사용 금지 — 개선 기준선으로만 쓴다.",
}


def clean_team(x: str) -> str:
    """'삼성 7' → '삼성', '7 키움' → '키움'. 경기 전이면 스코어가 없다."""
    s = str(x).strip()
    s = re.sub(r"^\s*-?\d+\s+", "", s)
    s = re.sub(r"\s+-?\d+\s*$", "", s)
    return s.strip()


def main() -> int:
    hist = load_results()
    hist, ratings = run_elo(hist)

    # 팀별 누적 경기 수 — 표본이 적으면 Elo가 신뢰할 수 없다.
    # (여자배구 네이션스리그처럼 몇 경기뿐인 팀에서 EV +57% 같은 헛값이 나온다)
    import collections
    games_seen = collections.Counter()
    for r in hist.itertuples():
        games_seen[(r.league, r.home_team)] += 1
        games_seen[(r.league, r.away_team)] += 1
    MIN_GAMES = 30
    a, b = fit_logistic(hist[hist["year"] <= 2024])
    season = datetime.now().year
    import pandas as _pd
    now_for_forms = _pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None)
    forms, h2h = build_forms(hist, season=season, as_of=now_for_forms)
    print(f"Elo 레이팅 {len(ratings)}팀 · 시즌 {season} 폼 {len(forms)}팀")

    sess = _session()
    have = sorted(int(p.stem.replace(".html", ""))
                  for p in (CACHE / str(season)).glob("*.html.gz")) \
        if (CACHE / str(season)).exists() else []
    live_rounds = find_live_rounds(sess, season, (max(have) - 3) if have else 1)
    # 이 산출물은 사전등록 원장이 아니다. 이미 끝난 경기를 현재 모델로 다시
    # 예측하면 결과를 본 뒤 만든 '가짜 과거 성적'이 되므로 발매 중 회차만 다룬다.
    rounds = sorted(set(live_rounds))
    print(f"대상 회차: 발매중 {live_rounds}")

    picks = []
    for rnd in rounds:
        rows = _fetch(sess, season, rnd) or []
        # 배당이 붙은 승패 2-way 전부. 실제 추천은 아직 시작하지 않은 경기만 낸다.
        # (배당은 회차 공개 시 한 번에 붙지 않고 순차적으로 붙는다 — 2026-07-26 관측)
        live = [r for r in rows
                if r.odds and not r.is_void
                and r.market_family == "승패" and r.n_way == 2
                and r.overround and 1.0 <= r.overround <= 1.40]
        if not live:
            continue
        payout = 100 / float(np.mean([r.overround for r in live]))

        for r in live:
            ko = _kickoff(r.date_text, season)
            if not _is_recommendable_now(r.result, ko):
                continue

            ht, at = clean_team(r.home), clean_team(r.away)
            kh, ka = (r.league, ht), (r.league, at)
            if kh not in ratings or ka not in ratings:
                continue          # 처음 보는 팀은 추정하지 않는다
            if min(games_seen[kh], games_seen[ka]) < MIN_GAMES:
                continue          # 표본 부족 — Elo가 신뢰할 수 없다
            diff = ratings[kh] + 45.0 - ratings[ka]
            p_home = float(prob_home(diff, a, b))
            o_h, o_a = r.odds[0], r.odds[1]
            ev_h = p_home * o_h - 1
            ev_a = (1 - p_home) * o_a - 1
            ov = 1 / o_h + 1 / o_a
            p_mkt = (1 / o_h) / ov

            side = ht if ev_h >= ev_a else at
            ev = max(ev_h, ev_a)
            game_time = (_pd.Timestamp(ko).tz_localize(None) if ko is not None else None)
            fh = form_for_game(forms.get(kh), game_time)
            fa = form_for_game(forms.get(ka), game_time)

            picks.append({
                "round": rnd, "game_no": r.game_no, "date": r.date_text,
                "sport": r.sport, "league": r.league,
                "home": ht, "away": at,
                "odds_home": round(o_h, 2), "odds_away": round(o_a, 2),
                "payout": round(100 / r.overround, 2),
                "p_model_home": round(p_home, 4),
                "p_market_home": round(p_mkt, 4),
                "edge_home": round(p_home - p_mkt, 4),
                "ev_home": round(ev_h, 4), "ev_away": round(ev_a, 4),
                "pick_side": side, "pick_ev": round(ev, 4),
                "pick_odds": round(o_h if side == ht else o_a, 2),
                "elo_home": round(ratings[kh], 1), "elo_away": round(ratings[ka], 1),
                "n_games_home": games_seen[kh], "n_games_away": games_seen[ka],
                "kickoff": ko.isoformat() if ko else None,
                "status": "경기전",
                "result": None,
                "hit": None, "profit": None,
                "h2h": h2h_text(h2h, r.league, ht, at),
                "form_home": _form_dict(fh), "form_away": _form_dict(fa),
                "preview": make_preview(ht, at, r.league, fh, fa, h2h,
                                        p_home, p_mkt, o_h, o_a, payout,
                                        ev_h, ev_a, sport=r.sport),
                "short": make_short(ht, at, fh, fa,
                                    p_home if side == ht else 1 - p_home,
                                    side, ev),
            })

    # 킥오프가 임박한 순. 과거 성적은 예측 시점에 고정한 append-only 원장에서만
    # 계산해야 하므로 이 즉석 산출물에는 tally를 만들지 않는다.
    picks.sort(key=lambda x: (x.get("kickoff") or "", -x["pick_ev"]))
    doc = {
        "tally": None,
        "tally_status": "prediction_ledger_required",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season": season, "rounds": rounds,
        "n_picks": len(picks), "backtest": BACKTEST, "picks": picks,
    }
    doc = repair_text_tree(doc)
    try:
        publish_nonempty_json(
            OUT, doc, rounds=rounds, records=picks, artifact_name="picks.json")
    except PublishGuardError as exc:
        print(f"\n산출물 갱신 보류: {exc}")
        return 1
    print(f"\n픽 {len(picks)}건 → {OUT}")
    for p in picks[:5]:
        print(f"  [{p['pick_ev']:+.1%}] {p['league']} {p['home']} vs {p['away']} "
              f"→ {p['pick_side']} @{p['pick_odds']}")
    return 0


def _form_dict(f) -> dict | None:
    if f is None:
        return None
    return {"w": f.w, "l": f.l, "d": f.d, "last10": f.last10_str,
            "streak": f"{f.streak_n}{f.streak_kind}" if f.streak_n >= 2 else "",
            "home": f"{f.home_w}-{f.home_l}", "away": f"{f.away_w}-{f.away_l}",
            "avg_scored": round(f.avg_scored, 1) if f.avg_scored else None,
            "avg_conceded": round(f.avg_conceded, 1) if f.avg_conceded else None,
            "rest_days": f.rest_days, "streak_days": f.streak_days,
            "close_games": f.close_games, "blowout_w": f.blowout_w,
            "shutout_l": f.shutout_l, "trend": f.trend,
            "margin_recent": round(f.margin_recent, 1) if f.margin_recent is not None else None,
            "margin_prev": round(f.margin_prev, 1) if f.margin_prev is not None else None}


if __name__ == "__main__":
    if "--sanitize-existing" in sys.argv:
        raise SystemExit(_sanitize_existing_output())
    raise SystemExit(main())
