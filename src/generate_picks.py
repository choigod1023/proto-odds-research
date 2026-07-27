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

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commentary import make_preview, make_short          # noqa: E402
from elo_model import fit_logistic, load_results, prob_home, run_elo  # noqa: E402
from snapshot import UNPLAYED, _fetch, find_live_rounds  # noqa: E402
from team_form import build_forms, h2h_text, set_rest_days  # noqa: E402
from wisetoto import CACHE, _session                     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
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
    forms, h2h = build_forms(hist, season=season)
    # 오늘 기준 휴식일 계산 (경기 변수)
    import pandas as _pd
    set_rest_days(forms, _pd.Timestamp(datetime.now().date()))
    print(f"Elo 레이팅 {len(ratings)}팀 · 시즌 {season} 폼 {len(forms)}팀")

    sess = _session()
    have = sorted(int(p.stem.replace(".html", ""))
                  for p in (CACHE / str(season)).glob("*.html.gz")) \
        if (CACHE / str(season)).exists() else []
    live_rounds = find_live_rounds(sess, season, (max(have) - 3) if have else 1)
    # 발매 중인 회차 + 직전 회차들. 직전 회차는 이미 정산됐으므로
    # **모델 픽이 실제로 맞았는지** 확인할 수 있다(공개 검증).
    recent = [r for r in have[-3:] if r not in live_rounds]
    rounds = sorted(set(live_rounds) | set(recent))
    print(f"대상 회차: 발매중 {live_rounds} + 최근정산 {recent}")

    picks = []
    for rnd in rounds:
        rows = _fetch(sess, season, rnd) or []
        # 배당이 붙은 승패 2-way 전부. 정산된 경기는 적중 여부까지 낸다
        # (배당은 회차 공개 시 한 번에 붙지 않고 순차적으로 붙는다 — 2026-07-26 관측)
        live = [r for r in rows
                if r.odds and not r.is_void
                and r.market_family == "승패" and r.n_way == 2
                and r.overround and 1.0 <= r.overround <= 1.40]
        if not live:
            continue
        payout = 100 / float(np.mean([r.overround for r in live]))

        for r in live:
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
            fh, fa = forms.get(kh), forms.get(ka)

            # 정산 상태와 적중 여부
            settled = r.result in ("홈승", "홈패")
            hit = None
            profit = None
            if settled:
                winner = ht if r.result == "홈승" else at
                hit = (side == winner)
                profit = (r.odds[0 if side == ht else 1] - 1) if hit else -1.0

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
                "status": "정산" if settled else "경기전",
                "result": r.result if settled else None,
                "hit": hit, "profit": round(profit, 3) if profit is not None else None,
                "h2h": h2h_text(h2h, r.league, ht, at),
                "form_home": _form_dict(fh), "form_away": _form_dict(fa),
                "preview": make_preview(ht, at, r.league, fh, fa, h2h,
                                        p_home, p_mkt, o_h, o_a, payout,
                                        ev_h, ev_a, sport=r.sport),
                "short": make_short(ht, at, fh, fa,
                                    p_home if side == ht else 1 - p_home,
                                    side, ev),
            })

    picks.sort(key=lambda x: (x["status"] != "경기전", -x["pick_ev"]))
    done = [p for p in picks if p["hit"] is not None]
    tally = None
    if done:
        n = len(done)
        w = sum(1 for p in done if p["hit"])
        roi = sum(p["profit"] for p in done) / n
        tally = {"n": n, "wins": w, "hit_rate": round(w / n, 4),
                 "roi": round(roi, 4)}
        print(f"\n정산분 성적: {w}/{n} 적중({w/n:.1%}) · ROI {roi:+.2%}")
    doc = {
        "tally": tally,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season": season, "rounds": rounds,
        "n_picks": len(picks), "backtest": BACKTEST, "picks": picks,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
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
    raise SystemExit(main())
