"""오늘의 최적 조합 — 실제 발매 중인 배당으로 목표 배당별 조합을 짠다.

무엇을 최적화하나
-----------------
**이기는 조합은 없다.** 12개 검증이 그렇게 끝났다. 여기서 고르는 기준은 하나다 —
**같은 목표 배당을 만들 때 가장 덜 잃는 구성.**

두 단계로 고른다.

1. **어느 배당대를 몇 개 쓸 것인가** — `combo.py` 가 실측으로 푼 문제다.
   다리를 하나 더 붙일 때마다 마진이 한 번 더 물려 약 −6%p 씩 깎이므로,
   목표 배당은 다리 수가 아니라 다리당 배당으로 맞춘다.

2. **그 배당대 안에서 어느 경기를 고를 것인가** — 여기가 이 파일이다.
   실제 조합배당을 목표의 95~115% 안에 묶고, 선택 경기별 공통 Shin 시장확률의
   곱이 가장 큰 서로 다른 경기 조합을 찾는다. 동률이면 환급률과 목표 근접도를 쓴다.
   이 확률은 자체 모델 우위가 아니라 시장 기준이다. 따라서 적중·이변 위험을
   정직하게 비교할 수는 있지만, 시장보다 더 잘 맞는다는 뜻은 아니다.

규정 (https://www.sportstoto.co.kr/proto_rules.php · 2022-03 19회차 한경기구매 도입)
  · **한경기구매(단폴)**: '한경기' 로 지정된 경기만. 단위투표금액 1,000원
  · **조합구매**: 2~10경기. 단위투표금액 100원
  · 같은 경기의 다른 마켓끼리는 한 장에 못 담는다 → 모든 다리는 서로 다른 경기
  · 회차당 1인 10만원 · 투표권당 적중금 상한 1억원

⚠️ 어떤 경기가 '한경기구매' 로 지정됐는지는 우리 데이터에 없다. 단폴 칸은
   "지정돼 있다면 이게 최선" 이라는 뜻이고, 실제 가능 여부는 베트맨에서 확인해야 한다.

사용:
    python3 src/today_combo.py
    python3 src/today_combo.py --selftest
"""
from __future__ import annotations

from datetime import datetime
import itertools
import json
import math
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from recommendation_policy import recommendation_exclusion_reason

ROOT = Path(__file__).resolve().parent.parent
TODAY = ROOT / "docs" / "data" / "today.json"
GRADES = ROOT / "docs" / "data" / "loss_grades.json"
COMBO = ROOT / "docs" / "data" / "combo.json"
OUT = ROOT / "docs" / "data" / "today_combo.json"

# combo.py 가 쓰는 것과 같은 경계
BINS = [(1.0, 1.3), (1.3, 1.5), (1.5, 1.8), (1.8, 2.2), (2.2, 3.0), (3.0, 5.0), (5.0, 999)]
LABELS = ["1.0-1.3", "1.3-1.5", "1.5-1.8", "1.8-2.2", "2.2-3.0", "3.0-5.0", "5.0+"]
BANNED = {"5.0+"}          # −33.5%. 어떤 목표에서도 쓰지 않는다.
TARGETS = [1.4, 2, 3, 5, 8, 12]
KST = ZoneInfo("Asia/Seoul")
DATE_TIME = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")


def bin_of(o: float) -> str | None:
    for (lo, hi), lab in zip(BINS, LABELS):
        if lo < o <= hi:
            return lab
    return None


def probability_of(value: object) -> float | None:
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None
    return probability if 0.0 < probability < 1.0 else None


def leg_quality(candidate: dict) -> tuple:
    probability = probability_of(candidate.get("market_prob"))
    return (
        -(probability or 0.0),
        candidate["overround"],
        candidate["kickoff_at"],
        -candidate["odds"],
    )


def kickoff_at(date_text: object, year: int) -> datetime | None:
    """프로토 `MM.DD(요일) HH:MM`을 KST 시각으로 바꾼다."""
    match = DATE_TIME.search(str(date_text or ""))
    if not match:
        return None
    month, day, hour, minute = map(int, match.groups())
    try:
        return datetime(year, month, day, hour, minute, tzinfo=KST)
    except ValueError:
        return None


def legs_today(now: datetime | None = None) -> list[dict]:
    """지금 구매할 수 있는 시작 전 선택지만 다리 후보로 편다.

    ⚠️ 프로토는 회차를 겹쳐서 발매한다. **같은 경기(game_no)가 두 회차에 서로 다른
       배당으로 걸린다.** 같은 결과에 더 받는 쪽이 순수하게 유리하므로 높은 배당만 남긴다.

       실측(2026-07-29): 두 회차에 겹친 마켓 60개 중 중앙값은 차이가 없고,
       평균으로는 오버라운드가 1.56%p 개선된다. 유리한 회차는 88:49 / 89:47 로
       균형이라 '한 회차가 낡은 것' 이 아니라 진짜 라인 변동이다.
       **차익거래(환급률 100% 초과)는 0개** — 양쪽을 다 사서 확정 수익을 낼 수는 없다.
    """
    d = json.loads(TODAY.read_text(encoding="utf-8"))
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    source_year = int(d.get("year") or now.year)
    out = []
    for rnd in d.get("rounds", []):
        for g in rnd.get("games", []):
            # 검증되지 않은 마켓은 가격 정보로는 보여도 단폴·다폴 구매 후보가 아니다.
            if recommendation_exclusion_reason(g.get("market")):
                continue
            kickoff = kickoff_at(g.get("date"), source_year)
            # 시작한 경기는 더 이상 살 수 없다. 결과 수집이 늦어도 시각으로 즉시 제외한다.
            if kickoff is None or kickoff <= now:
                continue
            over = g.get("overround")
            if not over or not (1.0 < over <= 1.40):
                continue
            for s in g.get("selections", []):
                market_prob = probability_of(s.get("prob"))
                o = s.get("odds")
                b = bin_of(o) if o else None
                if not b or b in BANNED:
                    continue
                out.append({
                    "event_key": f"{kickoff.isoformat()}|{g.get('home')}|{g.get('away')}",
                    "kickoff_at": kickoff.isoformat(),
                    "round": rnd.get("round"), "game_no": g.get("game_no"),
                    "date": g.get("date"), "league": g.get("league"),
                    "match": f"{g.get('home')} vs {g.get('away')}",
                    "home": g.get("home"), "away": g.get("away"),
                    "market": g.get("market"), "market_label": g.get("market_label", ""),
                    "booking": g.get("booking_class"), "sel": s.get("name"),
                    "odds": o, "bin": b, "overround": round(over, 4),
                    "payout": g.get("payout"), "hist_roi": s.get("hist_roi"),
                    "market_prob": (round(market_prob, 4) if market_prob else None),
                    "failure_prob": (round(1.0 - market_prob, 4)
                                     if market_prob else None),
                })

    # 같은 실제 경기·마켓·선택이 여러 회차에 있으면 **배당이 높은 회차**만 남긴다.
    best: dict = {}
    for x in out:
        k = (x["event_key"], x["market"], str(x["market_label"]), x["sel"])
        cur = best.get(k)
        if cur is None or x["odds"] > cur["odds"]:
            if cur is not None:
                x = {**x, "beats": {"round": cur["round"], "odds": cur["odds"]}}
            best[k] = x
        elif x["odds"] < cur["odds"]:
            best[k] = {**cur, "beats": {"round": x["round"], "odds": x["odds"]}}
    return sorted(
        best.values(),
        key=lambda x: (x["kickoff_at"], x["overround"], -x["odds"]),
    )


def candidate_pool(cands: list[dict], wanted_bin: str) -> list[dict]:
    """배당 구간 전체를 남기되 같은 0.05 구간에서는 확률 상위 3개만 쓴다."""
    by_price: dict[int, list[dict]] = {}
    for candidate in (c for c in cands if c["bin"] == wanted_bin):
        bucket = int(round(float(candidate["odds"]) / 0.05))
        by_price.setdefault(bucket, []).append(candidate)
    pool = []
    for rows in by_price.values():
        pool.extend(sorted(rows, key=leg_quality)[:3])
    return sorted(pool, key=leg_quality)


def pick_legs(
    cands: list[dict],
    bins: list[str],
    target: float | None = None,
) -> list[dict] | None:
    """목표 배당 범위 안에서 시장 기준 적중률이 가장 높은 서로 다른 경기 조합."""
    pools = [candidate_pool(cands, wanted_bin) for wanted_bin in bins]
    if any(not pool for pool in pools):
        return None

    lower = target * 0.95 if target else 0.0
    upper = target * 1.15 if target else float("inf")
    best: tuple | None = None
    for legs in itertools.product(*pools):
        if len({candidate["event_key"] for candidate in legs}) != len(legs):
            continue
        odds = math.prod(float(candidate["odds"]) for candidate in legs)
        if not lower <= odds <= upper:
            continue
        probabilities = [probability_of(candidate.get("market_prob")) for candidate in legs]
        hit = math.prod(probabilities) if all(p is not None for p in probabilities) else 0.0
        payout = math.prod(1.0 / float(candidate["overround"]) for candidate in legs)
        closeness = -abs(math.log(odds / target)) if target else 0.0
        score = (hit, payout, closeness)
        if best is None or score > best[0]:
            best = (score, legs)
    return list(best[1]) if best else None


def ticket_metrics(legs: list[dict]) -> dict:
    odds = math.prod(float(candidate["odds"]) for candidate in legs)
    probabilities = [probability_of(candidate.get("market_prob")) for candidate in legs]
    market_hit = math.prod(probabilities) if all(p is not None for p in probabilities) else None
    return {
        "actual_odds": round(odds, 2),
        "hit_est": (round(market_hit, 5) if market_hit is not None else None),
        "upset_risk": (round(1.0 - market_hit, 5) if market_hit is not None else None),
        "expected_roi": (round(market_hit * odds - 1.0, 4)
                         if market_hit is not None else None),
    }




def build() -> dict:
    cands = legs_today()
    plans = json.loads(COMBO.read_text(encoding="utf-8"))["plans"]
    by_target = {p["target"]: p for p in plans}

    out_plans = []
    for t in TARGETS:
        p = by_target.get(t)
        if not p:
            continue
        legs = pick_legs(cands, p["best"]["bins"], target=t)
        if not legs:
            out_plans.append({"target": t, "ok": False,
                              "why": f"오늘 발매 중인 경기로는 {p['best']['bins']} 구성을 못 만든다"})
            continue
        metrics = ticket_metrics(legs)
        has_market_probability = metrics["hit_est"] is not None
        out_plans.append({
            "target": t, "ok": True, "legs": len(legs),
            "bins": p["best"]["bins"],
            "expected_roi": (metrics["expected_roi"] if has_market_probability
                             else p["best"]["roi"]),
            "hit_est": (metrics["hit_est"] if has_market_probability else p["best"]["hit"]),
            "upset_risk": metrics["upset_risk"],
            "actual_odds": metrics["actual_odds"],
            "probability_basis": ("선택 경기의 Shin 시장확률 곱" if has_market_probability
                                  else "과거 배당구간 평균"),
            "historical_bucket_hit_est": p["best"]["hit"],
            "historical_bucket_roi": p["best"]["roi"],
            "picks": legs,
        })

    # 단폴 — 지정 경기라면 가장 덜 잃는 한 장
    solo = None
    lo = [c for c in cands if c["bin"] == "1.0-1.3"]
    if lo:
        lo.sort(key=leg_quality)
        solo = lo[0]

    grades = json.loads(GRADES.read_text(encoding="utf-8"))
    today = json.loads(TODAY.read_text(encoding="utf-8"))
    return {
        "generated_at": today.get("generated_at"),
        "year": today.get("year"),
        "probability_method": today.get("probability_method", "legacy"),
        "basis": "배당대는 과거 손실구조에, 티켓 적중률은 선택 경기별 시장확률 곱에 쓴다. "
                 "독립 모델 우위가 검증되기 전까지 모델 괴리는 적중률에 섞지 않는다.",
        "n_candidates": len(cands),
        "n_better_round": sum(1 for c in cands if c.get("beats")),
        "next_kickoff_at": min((c["kickoff_at"] for c in cands), default=None),
        "solo": solo,
        "plans": out_plans,
        # 브라우저가 시간이 지난 직후 다음 경기로 즉시 다시 조합할 때 쓴다.
        "candidates": cands,
        "odds_bins": grades["odds_bins"],
        "note": "표시 적중률은 선택한 서로 다른 경기의 시장 기준 확률을 곱한 값이다. "
                "자체 모델 우위가 검증된 확률은 아니며, 기대값이 음수면 시장을 이긴 픽이 아니다. "
                "단폴은 '한경기' 로 지정된 경기만 구매할 수 있다.",
    }


def _selftest() -> int:
    d = build()
    bad = []
    print("오늘의 조합 자기검사")
    print(f"  다리 후보 {d['n_candidates']:,}개")
    for p in d["plans"]:
        if not p.get("ok"):
            print(f"  ⏭ 목표 {p['target']}× — {p['why']}")
            continue
        gs = [c["event_key"] for c in p["picks"]]
        if len(set(gs)) != len(gs):
            bad.append(f"목표 {p['target']}× : 같은 경기를 두 번 썼다 {gs} — 규정 위반")
        if any(c["bin"] in BANNED for c in p["picks"]):
            bad.append(f"목표 {p['target']}× : 금지 배당대가 섞였다")
        probabilities = [probability_of(c.get("market_prob")) for c in p["picks"]]
        if all(x is not None for x in probabilities):
            expected_hit = math.prod(probabilities)
            if abs(p["hit_est"] - expected_hit) > 1e-4:
                bad.append(f"목표 {p['target']}× : 선택 경기 확률 곱과 적중률이 다르다")
            expected_roi = expected_hit * math.prod(c["odds"] for c in p["picks"]) - 1.0
            if abs(p["expected_roi"] - expected_roi) > 1e-3:
                bad.append(f"목표 {p['target']}× : 실제 배당 기준 기대값과 다르다")
        exact_odds = math.prod(c["odds"] for c in p["picks"])
        if not p["target"] * 0.95 <= exact_odds <= p["target"] * 1.15:
            bad.append(f"목표 {p['target']}× : 실제 배당 {p['actual_odds']}×가 허용범위 밖")
        if p["expected_roi"] >= 0:
            bad.append(f"목표 {p['target']}× : 기대 ROI 가 양수다 — 그런 구성은 없다")
        print(f"  ✅ 목표 {p['target']}× — {p['legs']}폴 · 실배당 {p['actual_odds']}× · "
              f"서로 다른 경기 {len(set(gs))}개")
    now = datetime.now(KST)
    past = [c for c in d.get("candidates", []) if datetime.fromisoformat(c["kickoff_at"]) <= now]
    if past:
        bad.append(f"시작한 경기 {len(past)}개가 후보에 남았다")
    if d["solo"] and d["solo"]["bin"] in BANNED:
        bad.append("단폴이 금지 배당대다")
    if bad:
        print("\n🔴 " + "\n🔴 ".join(bad))
        return 1
    print("\n✅ 오늘의 조합 자기검사 통과")
    return 0


def main() -> int:
    d = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"다리 후보 {d['n_candidates']:,}개 (5.0+ 제외)\n")
    if d["solo"]:
        s = d["solo"]
        print(f"[단폴] {s['league']} {s['match']} · {s['market']} {s['sel']} @ {s['odds']} "
              f"(환급률 {s['payout']}%)  ← '한경기' 지정 경기만 가능")
    for p in d["plans"]:
        if not p.get("ok"):
            print(f"\n[목표 {p['target']}×] {p['why']}")
            continue
        print(f"\n[목표 {p['target']}×] {p['legs']}폴 · 실배당 {p['actual_odds']}× · "
              f"적중 {p['hit_est']*100:.1f}% · 기대 {p['expected_roi']*100:+.1f}%")
        for c in p["picks"]:
            print(f"   · {c['date']} {c['league']:<8} {c['match']:<22} "
                  f"{c['market']}{(' ' + c['market_label']) if c['market_label'] else ''} "
                  f"{c['sel']} @ {c['odds']}  (환급 {c['payout']}%)"
                  + (f"  ← {c['round']}회차. {c['beats']['round']}회차는 @{c['beats']['odds']}"
                     if c.get("beats") else ""))
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
