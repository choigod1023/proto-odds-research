"""오늘자 산출물 생성 — 발매 중인 회차에 실측 결과를 붙인다.

⚠️ 이것은 '승부 예측'이 아니다.
   Q0 결과 프로토 배당에서 +EV 구간은 발견되지 않았다(findings/Q0.md).
   따라서 지금 정직하게 제공할 수 있는 것은 **가격 분석과 회피 필터**다:

     · 이 회차의 환급률은 몇 등급인가        (Q1: 회차마다 86~89%로 다름)
     · 이 배당대의 과거 실측 수익률은 얼마인가 (Q0/Q5: 배당 높을수록 급락)
     · 이 상품은 같은 경기의 대안보다 유리한가 (2-way > 3-way > 3-way핸디캡)
     · 시장이 보는 확률은 얼마인가             (devig)

산출물: web/data/today.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import SEL_NAMES                                 # noqa: E402
from devig import MARKET_PROBABILITY_METHOD, market_probabilities  # noqa: E402
from snapshot import UNPLAYED, find_live_rounds, _fetch    # noqa: E402
from wisetoto import CACHE, _session                       # noqa: E402
from runtime_db import persist_artifact, read_frame, database_enabled, RuntimeDatabase  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BETS = ROOT / "data" / "processed" / "bets.csv"
OUT = ROOT / "docs" / "data" / "today.json"

BUCKETS = [(1.0, 1.5), (1.5, 1.8), (1.8, 2.2), (2.2, 3.0), (3.0, 5.0), (5.0, 999)]
THEORETICAL = {"2-way": 1 / 1.1364 - 1, "3-way": 1 / 1.1494 - 1,
               "3-way-핸디캡": 1 / 1.1629 - 1}

# 회차 환급률 등급 (Q1 실측: 86/87/88/89% 이산값에 몰림)
def payout_grade(payout: float) -> tuple[str, str]:
    if payout >= 88.5:
        return "A", "환급률 최상위 회차 (상위 10%)"
    if payout >= 87.9:
        return "B", "환급률 양호 (중앙값 수준)"
    if payout >= 86.9:
        return "C", "환급률 평균 이하"
    return "D", "환급률 최하위 회차 (하위 10%)"


def bucket_of(o: float) -> str:
    for lo, hi in BUCKETS:
        if lo <= o < hi:
            return f"{lo:.1f}–{hi:.1f}" if hi < 999 else "5.0+"
    return "5.0+"


def make_comment(r, sels: list[dict], grade: str, warns: list[str],
                 limit: int = 200) -> str:
    """경기별 분석 코멘트를 실측치로 자동 작성한다 (200자 이내).

    추측이나 전력 평가는 넣지 않는다. 우리가 실제로 측정한 것만 쓴다:
    시장 내재확률 · 회차 환급률 등급 · 해당 배당대의 과거 실측 수익률 · 구조적 열위.
    """
    top = max(sels, key=lambda s: s["prob"])
    parts = []

    # 1) 시장이 보는 확률
    parts.append(f"시장은 {top['name']} {top['prob']*100:.0f}%로 본다"
                 f"(배당 {top['odds']:.2f}).")

    # 2) 상품 구조와 환급률
    payout = 100 / r.overround
    if r.booking_class == "2-way":
        parts.append(f"2-way라 환급률 {payout:.1f}%로 구조상 가장 유리.")
    elif r.booking_class == "3-way":
        parts.append(f"3-way 환급률 {payout:.1f}%, 2-way보다 약 1%p 불리.")
    else:
        parts.append(f"3-way 핸디캡 환급률 {payout:.1f}%, 구조상 가장 불리.")

    # 3) 이 배당대의 과거 실측 수익률
    if top.get("hist_roi") is not None and top.get("hist_n"):
        parts.append(f"{top['bucket']} 구간 과거 실측 수익률 "
                     f"{top['hist_roi']*100:+.1f}%(n={top['hist_n']:,}).")

    # 4) 경고 또는 회차 등급
    if warns:
        parts.append(warns[0].split(" — ")[0] + " 주의.")
    else:
        parts.append(f"회차 환급률 {grade}등급.")

    # 5) 마지막 한마디 — 전부 마이너스라는 사실을 감추지 않는다
    parts.append("배당 기반 +EV 구간은 미발견.")

    out = " ".join(parts)
    if len(out) > limit:
        out = out[:limit - 1].rstrip() + "…"
    return out


def build_lookup() -> dict:
    """과거 553회차에서 (booking구조 × 배당구간)별 실측 ROI 표를 만든다."""
    b = read_frame("processed_bets", BETS)
    b["bucket"] = b["odds"].map(bucket_of)
    g = b.groupby(["booking_class", "bucket"]).agg(
        n=("profit", "size"), roi=("profit", "mean")).reset_index()
    return {f"{r.booking_class}|{r.bucket}": {"n": int(r.n), "roi": float(r.roi)}
            for r in g.itertuples()}


def main() -> int:
    if (RuntimeDatabase().dataset_metadata("processed_bets") is None
            if database_enabled() else not BETS.exists()):
        print("먼저 python src/build_dataset.py 를 실행하세요.")
        return 1
    lookup = build_lookup()
    print(f"과거 실측 조회표 {len(lookup)}개 구간")

    sess = _session()
    year = datetime.now().year
    have = sorted(int(p.stem.replace(".html", ""))
                  for p in (CACHE / str(year)).glob("*.html.gz")) \
        if (CACHE / str(year)).exists() else []
    if database_enabled():
        have = [int(name.rsplit(":", 1)[1]) for name in
                RuntimeDatabase().document_names(f"archive:{year}:")]
    hint = (max(have) - 3) if have else 1
    rounds = find_live_rounds(sess, year, hint)
    if not rounds:
        print("발매 중인 회차가 없습니다.")
        return 1

    out_rounds = []
    for rnd in rounds:
        rows = _fetch(sess, year, rnd)
        if not rows:
            continue
        live = [r for r in rows if r.result in UNPLAYED and not r.is_void
                and r.overround and 1.0 <= r.overround <= 1.40]
        if not live:
            continue

        ov_2way = [r.overround for r in live if r.booking_class == "2-way"]
        payout = 100 / float(np.mean(ov_2way)) if ov_2way else None
        grade, grade_note = payout_grade(payout) if payout else ("?", "")

        games = []
        for r in live:
            # 상세 픽과 오늘 조합이 같은 시장확률을 쓰도록 공통 devig를 적용한다.
            # 서로 다른 방법을 쓰면 같은 경기의 확률이 화면마다 달라진다.
            probs = market_probabilities(list(r.odds))
            sel_names = SEL_NAMES.get(
                (r.market_family, r.n_way), tuple(f"sel{i}" for i in range(r.n_way)))

            sels = []
            for i, o in enumerate(r.odds):
                key = f"{r.booking_class}|{bucket_of(o)}"
                hist = lookup.get(key, {})
                sels.append({
                    "name": sel_names[i] if i < len(sel_names) else f"sel{i}",
                    "odds": round(o, 2),
                    "prob": round(probs[i], 4),
                    "bucket": bucket_of(o),
                    "hist_roi": round(hist.get("roi", float("nan")), 4)
                    if hist else None,
                    "hist_n": hist.get("n"),
                })

            warns = []
            if r.booking_class == "3-way-핸디캡":
                warns.append("3-way 핸디캡 — 같은 경기 2-way 대비 약 2%p 불리")
            if max(r.odds) >= 5.0:
                warns.append("배당 5.0 이상 포함 — 과거 실측 ROI −33%")
            elif max(r.odds) >= 3.0:
                warns.append("배당 3.0 이상 포함 — 기준선 대비 열위")

            games.append({
                "game_no": r.game_no, "date": r.date_text, "sport": r.sport,
                "league": r.league, "home": r.home, "away": r.away,
                "market": r.market_family, "market_label": r.market_label,
                "booking_class": r.booking_class, "n_way": r.n_way,
                "overround": round(r.overround, 4),
                "payout": round(100 / r.overround, 2),
                "theoretical_roi": round(THEORETICAL.get(r.booking_class, -0.12), 4),
                "selections": sels, "warnings": warns,
                "comment": make_comment(r, sels, grade, warns),
            })

        games.sort(key=lambda g: (g["date"], g["game_no"]))
        out_rounds.append({
            "round": rnd, "n_games": len(games),
            "payout_2way": round(payout, 2) if payout else None,
            "grade": grade, "grade_note": grade_note,
            "games": games,
        })
        print(f"  {year}-{rnd}회차: {len(games)}경기 · 2-way 환급률 "
              f"{payout:.2f}% (등급 {grade})" if payout else f"  {year}-{rnd}회차")

    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "year": year,
        "probability_method": MARKET_PROBABILITY_METHOD,
        "rounds": out_rounds,
        "basis": {
            "history_rounds": 553,
            "history_bets": 353047,
            "note": "Q0 결과 프로토 배당에서 +EV 구간은 발견되지 않았다. "
                    "본 산출물은 승부 예측이 아니라 가격 분석·회피 필터다.",
        },
    }
    persist_artifact("today", doc, OUT)
    print(f"\n생성 완료: {OUT}")
    print(f"  회차 {len(out_rounds)}개 · 경기 {sum(r['n_games'] for r in out_rounds)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
