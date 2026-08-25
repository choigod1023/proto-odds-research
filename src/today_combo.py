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

from recommendation_policy import (
    MAX_AUTO_RECOMMENDATION_ODDS,
    automatic_selection_exclusion_reason,
    recommendation_exclusion_reason,
)

ROOT = Path(__file__).resolve().parent.parent
TODAY = ROOT / "docs" / "data" / "today.json"
GRADES = ROOT / "docs" / "data" / "loss_grades.json"
COMBO = ROOT / "docs" / "data" / "combo.json"
OUT = ROOT / "docs" / "data" / "today_combo.json"

# combo.py 가 쓰는 것과 같은 경계
BINS = [(1.0, 1.3), (1.3, 1.5), (1.5, 1.8), (1.8, 2.2), (2.2, 3.0), (3.0, 5.0), (5.0, 999)]
LABELS = ["1.0-1.3", "1.3-1.5", "1.5-1.8", "1.8-2.2", "2.2-3.0", "3.0-5.0", "5.0+"]
BANNED = {"2.2-3.0", "3.0-5.0", "5.0+"}
TARGETS = [1.4, 2, 3, 5, 8, 12]
SAFE_TARGET_BINS = {
    1.4: ["1.0-1.3", "1.0-1.3"],
    2: ["1.0-1.3", "1.5-1.8"],
    3: ["1.3-1.5", "1.8-2.2"],
    5: ["1.5-1.8", "1.5-1.8", "1.5-1.8"],
    8: ["1.8-2.2", "1.8-2.2", "1.8-2.2"],
    12: ["1.5-1.8", "1.8-2.2", "1.8-2.2", "1.8-2.2"],
}
DAILY_CHALLENGE_BUDGET_RATIO = 0.10
PRIMARY_TARGET_MIN = 2.0
PRIMARY_TARGET_MAX = 3.0
PRIMARY_MIN_ACTUAL_ODDS = 1.75
FRAGILE_MARKETS = {"승①패", "전반승무패", "전반언더오버", "전반핸디캡"}
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


def calibrated_leg_probability(candidate: dict) -> tuple[float | None, float | None]:
    """실측 ROI를 후보 배당의 적중확률로 역산하고 95% 단측 Wilson 하한을 낸다.

    p = (1 + ROI) / odds 는 이 저장소의 devig_pick.py가 검증에 쓰는 동일한 정의다.
    자체 모델은 시장보다 나빴으므로 사용하지 않는다. 실측 표본이 없으면 시장확률로
    되돌아가되, 그 값을 수익 우위라고 부르지 않는다.
    """
    market = probability_of(candidate.get("market_prob"))
    try:
        odds = float(candidate.get("odds"))
        roi = float(candidate.get("hist_roi"))
        n = int(candidate.get("hist_n") or 0)
    except (TypeError, ValueError):
        return market, market
    if odds <= 1.0 or not -0.99 < roi < 5.0:
        return market, market
    estimate = min(0.999, max(0.001, (1.0 + roi) / odds))
    if n < 30:
        return estimate, estimate
    z = 1.645  # 95% 단측 하한
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = (estimate + z2 / (2.0 * n)) / denominator
    margin = z * math.sqrt(estimate * (1.0 - estimate) / n + z2 / (4.0 * n * n)) / denominator
    return estimate, max(0.001, center - margin)


def leg_quality(candidate: dict) -> tuple:
    calibrated, conservative = calibrated_leg_probability(candidate)
    probability = probability_of(candidate.get("market_prob"))
    odds = float(candidate.get("odds") or 0.0)
    return (
        -((conservative or 0.0) * odds),
        -((calibrated or 0.0) * odds),
        -(probability or 0.0),
        candidate["overround"],
        candidate["kickoff_at"],
        -odds,
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
    """KST 오늘 23:59까지 구매할 수 있는 시작 전 선택지만 다리 후보로 편다.

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
    else:
        now = now.astimezone(KST)
    source_year = int(d.get("year") or now.year)
    out = []
    for rnd in d.get("rounds", []):
        for g in rnd.get("games", []):
            # 검증되지 않은 마켓은 가격 정보로는 보여도 단폴·다폴 구매 후보가 아니다.
            if recommendation_exclusion_reason(g.get("market")):
                continue
            kickoff = kickoff_at(g.get("date"), source_year)
            # 시작한 경기와 KST 자정 이후 경기는 '오늘 살 거면'에서 제외한다.
            # 23:59 시작은 오늘, 00:00 시작은 내일 픽이다.
            if kickoff is None or kickoff <= now or kickoff.date() != now.date():
                continue
            over = g.get("overround")
            if not over or not (1.0 < over <= 1.40):
                continue
            selections = [
                (selection, probability_of(selection.get("prob")))
                for selection in g.get("selections", [])
            ]
            selections = [(selection, probability) for selection, probability in selections
                          if probability is not None]
            if not selections:
                continue
            favorite_probability = max(probability for _, probability in selections)
            for s, market_prob in selections:
                o = s.get("odds")
                policy_reason = automatic_selection_exclusion_reason(
                    g.get("market"), o, market_prob, favorite_probability)
                if policy_reason:
                    continue
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
                    "hist_n": s.get("hist_n"),
                    "market_prob": round(market_prob, 4),
                    "failure_prob": round(1.0 - market_prob, 4),
                    "is_market_favorite": True,
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
    # 회차마다 정·역배 방향이 뒤집혔으면 양쪽이 각각 '그 회차의 최유력'으로 남을 수 있다.
    # 회차 중복을 정리한 뒤 실제 경기·마켓 단위로 한 번 더 최유력만 남긴다.
    deduped = list(best.values())
    favorite_by_market: dict[tuple, float] = {}
    for candidate in deduped:
        key = (candidate["event_key"], candidate["market"], str(candidate["market_label"]))
        favorite_by_market[key] = max(
            favorite_by_market.get(key, 0.0), float(candidate["market_prob"]))
    deduped = [candidate for candidate in deduped if float(candidate["market_prob"]) >=
               favorite_by_market[(candidate["event_key"], candidate["market"],
                                   str(candidate["market_label"]))] - 1e-9]
    return sorted(
        deduped,
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
    """목표 범위에서 보수 기대수익, 보정 적중률 순으로 가장 나은 서로 다른 경기 조합."""
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
        metrics = ticket_metrics(list(legs))
        payout = math.prod(1.0 / float(candidate["overround"]) for candidate in legs)
        closeness = -abs(math.log(odds / target)) if target else 0.0
        score = (metrics.get("conservative_expected_roi", -99.0),
                 metrics.get("calibrated_hit_est", 0.0),
                 metrics.get("hit_est", 0.0), payout, closeness)
        if best is None or score > best[0]:
            best = (score, legs)
    return list(best[1]) if best else None


def ticket_metrics(legs: list[dict]) -> dict:
    odds = math.prod(float(candidate["odds"]) for candidate in legs)
    probabilities = [probability_of(candidate.get("market_prob")) for candidate in legs]
    market_hit = math.prod(probabilities) if all(p is not None for p in probabilities) else None
    calibrated = [calibrated_leg_probability(candidate) for candidate in legs]
    calibrated_hit = (math.prod(p[0] for p in calibrated)
                      if all(p[0] is not None for p in calibrated) else None)
    conservative_hit = (math.prod(p[1] for p in calibrated)
                         if all(p[1] is not None for p in calibrated) else None)
    sample_sizes = []
    for candidate in legs:
        try:
            n = int(candidate.get("hist_n") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            sample_sizes.append(n)
    out = {
        "actual_odds": round(odds, 2),
        "hit_est": (round(market_hit, 5) if market_hit is not None else None),
        "upset_risk": (round(1.0 - market_hit, 5) if market_hit is not None else None),
        "expected_roi": (round(market_hit * odds - 1.0, 4)
                         if market_hit is not None else None),
        "calibrated_hit_est": (round(calibrated_hit, 5)
                               if calibrated_hit is not None else None),
        "calibrated_expected_roi": (round(calibrated_hit * odds - 1.0, 4)
                                     if calibrated_hit is not None else None),
        "conservative_hit_est": (round(conservative_hit, 5)
                                 if conservative_hit is not None else None),
        "conservative_expected_roi": (round(conservative_hit * odds - 1.0, 4)
                                       if conservative_hit is not None else None),
        "calibration_min_n": min(sample_sizes) if sample_sizes else None,
    }
    return out




def _kelly_growth(plan: dict) -> float:
    p = probability_of(plan.get("conservative_hit_est"))
    odds = float(plan.get("actual_odds") or 0.0)
    if p is None or odds <= 1.0 or p * odds <= 1.0:
        return float("-inf")
    full = min(1.0, max(0.0, (p * odds - 1.0) / (odds - 1.0)))
    fraction = full * 0.5
    return p * math.log1p(fraction * (odds - 1.0)) + (1.0 - p) * math.log1p(-fraction)


def _metric_number(plan: dict, key: str, default: float) -> float:
    try:
        value = float(plan.get(key))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _fragile_legs(plan: dict) -> int:
    """정배 승리와 다른 사건을 묻는 파생시장의 수를 센다.

    오늘 FC서울 승(적중)과 -2 핸디캡(실패), LG 승(적중)과 2점차 승(실패)이
    갈린 것처럼 파생시장은 승패 확신을 그대로 재사용할 수 없다.
    """
    count = 0
    for pick in plan.get("picks", []):
        market = str(pick.get("market") or "")
        if market in FRAGILE_MARKETS:
            count += 1
            continue
        # 축구 -2처럼 승리와 큰 점수차 승리를 혼동하기 쉬운 극단 핸디캡.
        if market == "핸디캡":
            match = re.search(r"[-+]?\d+(?:\.\d+)?", str(pick.get("market_label") or ""))
            if match and abs(float(match.group())) >= 1.5:
                count += 1
    return count


def _balanced_score(plan: dict) -> tuple:
    return (
        -_fragile_legs(plan),
        _metric_number(plan, "conservative_expected_roi", -99.0),
        _metric_number(plan, "calibrated_hit_est", 0.0),
        -int(plan.get("legs") or 99),
        -abs(_metric_number(plan, "actual_odds", 0.0) - 2.25),
    )


def daily_recommendation(plans: list[dict]) -> dict:
    available = [plan for plan in plans if plan.get("ok")]
    if not available:
        return {"action": "none", "recommended_target": None,
                "why": "오늘 23:59 KST까지 구성 가능한 조합이 없다"}
    positive = [plan for plan in available
                if _metric_number(plan, "conservative_expected_roi", -99.0) > 0.0]
    if positive:
        best = max(positive, key=lambda plan: (_kelly_growth(plan),
                   _metric_number(plan, "calibrated_hit_est", 0.0)))
        action = "buy"
        why = "실측 보정확률의 95% 보수 하한에서도 기대수익이 양수다"
    else:
        balanced = [plan for plan in available
                    if PRIMARY_TARGET_MIN <= _metric_number(plan, "target", 0.0)
                    <= PRIMARY_TARGET_MAX
                    and _metric_number(plan, "actual_odds", 0.0) >= PRIMARY_MIN_ACTUAL_ODDS]
        best = max(balanced or available, key=_balanced_score)
        action = "challenge"
        why = ("저배당 1.4배 대신 2~3배 구간에서 파생시장 수·보수 기대·보정 적중률을 "
               "함께 비교한 오늘의 균형 도전픽이다; 검증된 양의 기대수익 신호는 아니다")
    return {"action": action, "recommended_target": best["target"],
            "budget_ratio": (DAILY_CHALLENGE_BUDGET_RATIO
                             if action == "challenge" else None),
            "conservative_expected_roi": best.get("conservative_expected_roi"),
            "calibrated_hit_est": best.get("calibrated_hit_est"), "why": why}


def build() -> dict:
    cands = legs_today()
    combo = json.loads(COMBO.read_text(encoding="utf-8"))
    leg_history = {row["bin"]: row for row in combo["legs"]}

    out_plans = []
    for t in TARGETS:
        bins = SAFE_TARGET_BINS.get(t)
        if not bins:
            continue
        history = [leg_history.get(wanted_bin) for wanted_bin in bins]
        historical_hit = (round(math.prod(row["hit"] for row in history), 5)
                          if all(history) else None)
        historical_roi = (round(math.prod(row["mult"] for row in history) - 1.0, 4)
                          if all(history) else None)
        legs = pick_legs(cands, bins, target=t)
        if not legs:
            out_plans.append({"target": t, "ok": False,
                              "bins": bins,
                              "why": "시장 최유력·2.20 미만 선택만으로 목표 배당을 못 만든다"})
            continue
        metrics = ticket_metrics(legs)
        out_plans.append({
            "target": t, "ok": True, "legs": len(legs),
            "bins": bins,
            **metrics,
            "probability_basis": "배당구간 실측 ROI 보정확률 · 95% Wilson 단측 하한",
            "historical_bucket_hit_est": historical_hit,
            "historical_bucket_roi": historical_roi,
            "fragile_legs": _fragile_legs({"picks": legs}),
            "picks": legs,
        })

    # 단폴 — 지정 경기라면 가장 덜 잃는 한 장
    solo = None
    lo = [c for c in cands if c["bin"] == "1.0-1.3"]
    if lo:
        lo.sort(key=leg_quality)
        solo = {**lo[0], **ticket_metrics([lo[0]])}

    grades = json.loads(GRADES.read_text(encoding="utf-8"))
    today = json.loads(TODAY.read_text(encoding="utf-8"))
    return {
        "generated_at": today.get("generated_at"),
        "year": today.get("year"),
        "probability_method": today.get("probability_method", "legacy"),
        "basis": "각 시장에서 시장확률 1위이며 배당 2.20 미만인 선택만 쓴다. "
                 "목표 총배당은 역배 대신 안전 구간의 다리 수를 늘려 맞춘다.",
        "n_candidates": len(cands),
        "n_better_round": sum(1 for c in cands if c.get("beats")),
        "next_kickoff_at": min((c["kickoff_at"] for c in cands), default=None),
        "selection_policy": "기본 추천 2~3배 · 파생시장 감점 · 다리당 2.20 미만 · 홀짝 제외",
        "max_leg_odds_exclusive": MAX_AUTO_RECOMMENDATION_ODDS,
        "solo": solo,
        "plans": out_plans,
        # 브라우저가 시간이 지난 직후 다음 경기로 즉시 다시 조합할 때 쓴다.
        "recommendation": daily_recommendation(out_plans),
        "candidates": cands,
        "odds_bins": grades["odds_bins"],
        "note": "시장 우위 매수는 실측 ROI 보정확률의 95% 보수 하한이 양수일 때만 쓴다. "
                "그보다 낮으면 패스로 숨기거나 1.4배를 대표픽으로 내세우지 않고, "
                "2~3배 구간의 최선 후보를 위험이 표시된 소액 균형 도전픽으로 제안한다. "
                "자체 득점 모델은 시장보다 부정확해 자동 선택에 쓰지 않는다. "
                "검증되지 않은 역배는 관찰만 하고 자동 추천하지 않는다. "
                "다리를 늘리면 마진도 누적되므로 고배당 조합은 여전히 고위험이다. "
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
        if any(float(c["odds"]) >= MAX_AUTO_RECOMMENDATION_ODDS for c in p["picks"]):
            bad.append(f"목표 {p['target']}× : 2.20 이상 선택지가 섞였다")
        if any(not c.get("is_market_favorite") for c in p["picks"]):
            bad.append(f"목표 {p['target']}× : 시장 최유력 아닌 역배가 섞였다")
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

    print(f"다리 후보 {d['n_candidates']:,}개 (홀짝·역배·2.20+ 제외)\n")
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
