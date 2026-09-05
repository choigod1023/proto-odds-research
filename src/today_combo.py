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
   실제 조합배당을 목표의 95~115% 안에 묶고, 같은 배당칸 안에서는 검증된
   최종 적중확률을 우선한다. 검증 보정값이 없으면 동일 시점 Shin 시장확률로
   복귀하며, 과거 배당구간 손익은 설명용 진단값으로만 남긴다.

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

from datetime import datetime, timedelta
import copy
import itertools
import json
import math
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from evolutionary_policy import live_snapshot, load_artifact
from ai_decision import (can_apply_decision_probability,
                         validate_decision_snapshot)
from devig import MARKET_PROBABILITY_METHOD, market_probabilities
from bets import SEL_NAMES
from runtime_db import (export_site_artifacts,
                        load_artifact as load_runtime_artifact, persist_artifact)
from recommendation_policy import (
    MAX_AUTO_RECOMMENDATION_ODDS,
    PREFERRED_RECOMMENDATION_ODDS,
    automatic_selection_exclusion_reason,
    recommendation_priority,
    recommendation_exclusion_reason,
)

ROOT = Path(__file__).resolve().parent.parent
TODAY = ROOT / "docs" / "data" / "today.json"
GRADES = ROOT / "docs" / "data" / "loss_grades.json"
COMBO = ROOT / "docs" / "data" / "combo.json"
OUT = ROOT / "docs" / "data" / "today_combo.json"
LIVE_ODDS = ROOT / "docs" / "data" / "live_odds.json"
EVOLUTION_ARTIFACT = ROOT / "findings" / "evolutionary_selector.json"
PICKS_V2 = ROOT / "docs" / "data" / "picks_v2.json"

# combo.py 가 쓰는 것과 같은 경계
BINS = [(1.0, 1.3), (1.3, 1.5), (1.5, 1.8), (1.8, 2.2), (2.2, 3.0), (3.0, 5.0), (5.0, 999)]
LABELS = ["1.0-1.3", "1.3-1.5", "1.5-1.8", "1.8-2.2", "2.2-3.0", "3.0-5.0", "5.0+"]
BANNED = {"2.2-3.0", "3.0-5.0", "5.0+"}
TARGETS = [3, 5, 8, 12]
SAFE_TARGET_BINS = {
    3: ["1.5-1.8", "1.5-1.8"],
    5: ["1.5-1.8", "1.5-1.8", "1.5-1.8"],
    8: ["1.8-2.2", "1.8-2.2", "1.8-2.2"],
    12: ["1.5-1.8", "1.8-2.2", "1.8-2.2", "1.8-2.2"],
}
DAILY_CHALLENGE_MIN_ROI = -0.205
DAILY_CHALLENGE_MIN_HIT = {3: 0.27}
DAILY_CHALLENGE_MAX_TARGET = 3
DAILY_CHALLENGE_ROI_TOLERANCE = 0.03
DAILY_CHALLENGE_BUDGET_RATIO = 0.10
CORRELATION_STRESS_RHO = -0.05
KST = ZoneInfo("Asia/Seoul")
DATE_TIME = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")


def bin_of(o: float) -> str | None:
    # 과거 구간은 오른쪽 닫힘이지만 운영 우선선 1.50은 상위 구간에 포함한다.
    if math.isclose(float(o), PREFERRED_RECOMMENDATION_ODDS, abs_tol=1e-9):
        return "1.5-1.8"
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


def _leg_probability_contract(candidate: dict) -> tuple[
        float | None, float | None, bool, str]:
    """검증된 점추정과 추천용 하한을 분리하고 나머지는 시장으로 복귀한다."""
    market = probability_of(candidate.get("market_prob"))
    final = probability_of(candidate.get("predicted_hit_prob"))
    applied = candidate.get("decision_pipeline_applied") is True
    validated = applied and candidate.get("has_validated_edge") is True
    estimate = final if final is not None and validated else market
    interval_lower = probability_of(candidate.get("probability_lower_bound"))
    has_interval = bool(
        validated
        and candidate.get("validated_uncertainty_available") is True
        and candidate.get("uncertainty_source") == "validated_residual_interval"
        and interval_lower is not None
        and estimate is not None
        and interval_lower <= estimate
    )
    if has_interval:
        return estimate, interval_lower, True, "validated_residual_interval"
    market_lower = min(market, estimate) if market is not None and estimate is not None else market
    return estimate, market_lower, False, "shin_market_fallback"


def calibrated_leg_probability(candidate: dict) -> tuple[float | None, float | None]:
    """검증된 잔차 계수가 없으면 후보의 동일 시점 시장확률로 정확히 복귀한다.

    예전 식은 넓은 배당구간 평균 ROI를 개별 후보 배당으로 나눈 뒤, 실제 승수도 아닌
    그 파생값에 Wilson 하한을 적용했다. 후보별 적중확률이 아니므로 선택에 쓰지 않는다.
    """
    estimate, lower, _, _ = _leg_probability_contract(candidate)
    return estimate, lower


def _correlation_stress(probabilities: list[float]) -> tuple[float, float, float]:
    """독립 곱에 pairwise rho=-0.05를 가한 민감도와 엄격한 Fréchet 하한."""
    independent = math.prod(probabilities)
    if len(probabilities) < 2:
        return independent, independent, 0.0
    adjustment = 0.0
    for left in range(len(probabilities)):
        for right in range(left + 1, len(probabilities)):
            covariance = CORRELATION_STRESS_RHO * math.sqrt(
                probabilities[left] * (1.0 - probabilities[left])
                * probabilities[right] * (1.0 - probabilities[right])
            )
            other = math.prod(
                probability for index, probability in enumerate(probabilities)
                if index not in {left, right}
            )
            adjustment += covariance * other
    frechet = max(0.0, sum(probabilities) - (len(probabilities) - 1.0))
    stressed = min(independent, max(frechet, independent + adjustment))
    return stressed, frechet, independent - stressed


def historical_leg_score(candidate: dict) -> tuple[float, int]:
    """자체 실측표의 손실률을 진단 지표로 계산한다.

    이 값은 후보별 적중확률이 아니다. 표본이 충분한 동일 배당·선택 구간에서 실제로
    덜 잃었던 정도만 비교하며, 확률 표시는 계속 동일 시점 Shin 확률을 사용한다.
    """
    try:
        roi = float(candidate.get("hist_roi"))
        n = int(candidate.get("hist_n") or 0)
    except (TypeError, ValueError):
        return -1.0, 0
    if not math.isfinite(roi) or n < 30 or not -0.99 < roi < 5.0:
        return -1.0, 0
    # 작은 표본이 우연히 좋아 보이는 것을 막기 위해 1,000경기까지 전체 기준(-12%)으로 수축한다.
    weight = min(1.0, n / 1000.0)
    return weight * roi + (1.0 - weight) * -0.12, n


def leg_quality(candidate: dict) -> tuple:
    calibrated, conservative = calibrated_leg_probability(candidate)
    odds = float(candidate.get("odds") or 0.0)
    return (
        -(conservative or 0.0),
        -(calibrated or 0.0),
        -((conservative or 0.0) * odds),
        candidate["overround"],
        candidate["kickoff_at"],
        -odds,
    )


def kickoff_at(date_text: object, year: int, round_no: object = None) -> datetime | None:
    """프로토 `MM.DD(요일) HH:MM`을 KST 시각으로 바꾼다."""
    match = DATE_TIME.search(str(date_text or ""))
    if not match:
        return None
    month, day, hour, minute = map(int, match.groups())
    game_year = int(year) - (int(round_no or 0) == 1 and month == 12)
    try:
        return datetime(game_year, month, day, hour, minute, tzinfo=KST)
    except ValueError:
        return None


def _live_prices() -> tuple[dict, str | None]:
    """현재 배당 스냅샷을 운영 DB(또는 개발 fixture)에서 읽는다."""
    payload = load_runtime_artifact("live_odds", LIVE_ODDS) or {}
    return payload.get("odds") or {}, payload.get("generated_at")


def _candidate_source() -> dict:
    """Build candidate markets from the live feed, not the slow today publisher.

    Prices alone cannot add a newly opened round or update a handicap line.
    Read market metadata and its complete price vector from the same snapshot.
    Older collectors without market metadata can use picks_v2 as a fallback.
    """
    live = load_runtime_artifact("live_odds", LIVE_ODDS) or {}
    picks = (load_runtime_artifact("picks_v2", PICKS_V2) or {}) if not isinstance(live.get("markets"), dict) else {}
    stamp = live.get("generated_at") if isinstance(live.get("markets"), dict) else picks.get("generated_at")
    try:
        year = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(KST).year
    except (ValueError, TypeError):
        year = datetime.now(KST).year
    rounds: dict[str, list[dict]] = {}

    def add(round_no: object, row: dict) -> None:
        if row.get("result") not in (None, "", "-", "경기전"):
            return
        try:
            prices = [float(value) for value in row.get("odds") or []]
            names = SEL_NAMES.get((row.get("market"), len(prices)))
            if not names or any(not math.isfinite(p) or p <= 1 for p in prices):
                return
            probabilities = market_probabilities(prices)
        except (TypeError, ValueError, ArithmeticError):
            return
        overround = sum(1 / price for price in prices)
        rounds.setdefault(str(round_no), []).append({
            **row, "market_label": row.get("label") or "",
            "overround": overround, "payout": 100 / overround,
            "selections": [{"name": name, "odds": price, "prob": probability}
                           for name, price, probability in zip(names, prices, probabilities)],
        })

    if isinstance(live.get("markets"), dict):
        for round_no, markets in live["markets"].items():
            for game_no, row in markets.items():
                add(round_no, {**row, "game_no": str(game_no)})
        source = "live_odds"
    else:
        for game in picks.get("live") or []:
            if game.get("status") not in ("경기전", "배당대기"):
                continue
            groups: dict[str, list[dict]] = {}
            for option in game.get("options") or []:
                groups.setdefault(str(option.get("게임번호") or ""), []).append(option)
            for game_no, options in groups.items():
                first = options[0]
                names = SEL_NAMES.get((first.get("market"), len(options)))
                by_name = {option.get("선택"): option for option in options}
                if not game_no or not names or any(name not in by_name for name in names):
                    continue
                add(game.get("round"), {
                    "game_no": game_no, "date": game.get("date"),
                    "home": game.get("home"), "away": game.get("away"),
                    "league": game.get("league"), "sport": game.get("sport"),
                    "market": first.get("market"), "label": first.get("label"),
                    "odds": [by_name[name].get("배당") for name in names],
                })
        source = "picks_v2"
    return {"year": year, "generated_at": stamp, "candidate_source": source,
            "rounds": [{"round": int(round_no), "games": games}
                       for round_no, games in rounds.items()]}


def _reprice_game(game: dict, round_no: object, live: dict) -> tuple[dict, bool]:
    fresh = (live.get(str(round_no)) or {}).get(str(game.get("game_no")))
    selections = game.get("selections") or []
    if not isinstance(fresh, list) or len(fresh) != len(selections):
        return game, False
    try:
        odds = [float(value) for value in fresh]
        if any(value <= 1.0 for value in odds):
            return game, False
        probabilities = market_probabilities(odds)
    except (TypeError, ValueError, ArithmeticError):
        return game, False
    repriced = copy.deepcopy(game)
    for selection, price, probability in zip(repriced["selections"], odds, probabilities):
        selection["odds"] = round(price, 2)
        selection["prob"] = round(probability, 6)
    overround = sum(1.0 / price for price in odds)
    repriced["overround"] = round(overround, 6)
    repriced["payout"] = round(100.0 / overround, 2)
    return repriced, True


def legs_today(now: datetime | None = None, live_prices: dict | None = None,
               source: dict | None = None) -> list[dict]:
    """KST 오늘과, 오늘 후보 소진 시 쓸 다음 날 오전 선택지를 함께 준비한다.

    ⚠️ 프로토는 회차를 겹쳐서 발매한다. **같은 경기(game_no)가 두 회차에 서로 다른
       배당으로 걸린다.** 같은 결과에 더 받는 쪽이 순수하게 유리하므로 높은 배당만 남긴다.

       실측(2026-07-29): 두 회차에 겹친 마켓 60개 중 중앙값은 차이가 없고,
       평균으로는 오버라운드가 1.56%p 개선된다. 유리한 회차는 88:49 / 89:47 로
       균형이라 '한 회차가 낡은 것' 이 아니라 진짜 라인 변동이다.
       **차익거래(환급률 100% 초과)는 0개** — 양쪽을 다 사서 확정 수익을 낼 수는 없다.
    """
    d = _candidate_source() if source is None else source
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)
    source_year = int(d.get("year") or now.year)
    # Source metadata already includes its own prices. Do not overlay a second
    # snapshot with a changed line; explicit overrides remain for legacy tests.
    live_prices = {} if live_prices is None else live_prices
    out = []
    for rnd in d.get("rounds", []):
        for original in rnd.get("games", []):
            g, live_repriced = _reprice_game(original, rnd.get("round"), live_prices)
            # 검증되지 않은 마켓은 가격 정보로는 보여도 단폴·다폴 구매 후보가 아니다.
            if recommendation_exclusion_reason(g.get("market")):
                continue
            kickoff = kickoff_at(g.get("date"), source_year, rnd.get("round"))
            # 화면은 오늘 적격 후보가 있으면 오늘 것만 쓰고, 없을 때에만 다음 날
            # 00:00~11:59 후보로 전환한다. 자정 직후 수집을 기다리지 않도록 생성물에는
            # 두 구간을 미리 함께 담는다.
            tomorrow = now.date() + timedelta(days=1)
            in_window = kickoff is not None and (
                kickoff.date() == now.date()
                or (kickoff.date() == tomorrow and kickoff.hour < 12)
            )
            if kickoff is None or kickoff <= now or not in_window:
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
            ordered_probability = sorted((probability for _, probability in selections),
                                         reverse=True)
            market_gap = (ordered_probability[0] - ordered_probability[1]
                          if len(ordered_probability) > 1 else ordered_probability[0])
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
                    "date": g.get("date"), "sport": g.get("sport"),
                    "league": g.get("league"),
                    "match": f"{g.get('home')} vs {g.get('away')}",
                    "home": g.get("home"), "away": g.get("away"),
                    "market": g.get("market"), "market_label": g.get("market_label", ""),
                    "booking": g.get("booking_class"), "sel": s.get("name"),
                    "odds": o, "bin": b, "overround": round(over, 4),
                    "payout": g.get("payout"), "hist_roi": s.get("hist_roi"),
                    "hist_n": s.get("hist_n"),
                    "market_prob": round(market_prob, 4),
                    "predicted_hit_prob": round(market_prob, 4),
                    "probability_source": "shin_market_fallback",
                    "has_validated_edge": False,
                    "market_gap": round(market_gap, 4),
                    "n_way": len(selections),
                    "failure_prob": round(1.0 - market_prob, 4),
                    "is_market_favorite": True,
                    "recommendation_priority": (
                        "primary" if recommendation_priority(o) == 1 else "fallback"
                    ),
                    "price_source": "live_odds" if live_repriced or d.get("candidate_source") == "live_odds" else "published_snapshot",
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
    # 경기별 최종 한 장은 개편된 decision snapshot을 연결한 뒤 고른다. 여기서 먼저
    # 줄이면 원장이 선택한 다른 마켓을 후보군에서 잃어버린다.
    return sorted(
        deduped,
        key=lambda x: (x["kickoff_at"], x["overround"], -x["odds"]),
    )


def select_event_candidates(candidates: list[dict]) -> list[dict]:
    """화면과 같이 우선 배당 구간 안에서 경기별 후보 하나를 남긴다."""
    def rank(candidate: dict) -> tuple:
        return (-recommendation_priority(candidate.get("odds")), *leg_quality(candidate))

    best_by_event: dict[str, dict] = {}
    for candidate in candidates:
        current = best_by_event.get(candidate["event_key"])
        if current is None or rank(candidate) < rank(current):
            best_by_event[candidate["event_key"]] = candidate
    return sorted(
        best_by_event.values(),
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
    """고정 배당칸·목표 범위에서 보수 적중확률이 높은 서로 다른 경기 조합."""
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
        # DB 이전 직전 계약: 목표 배당 범위 안에서는 생성기가 확정한 최종 예상
        # 적중확률을 먼저 최대화한다. 과거 구간 손실률은 진단용으로만 남긴다.
        score = (metrics.get("correlation_stress_hit_est", 0.0),
                 metrics.get("independent_hit_est", 0.0),
                 metrics.get("correlation_stress_expected_roi", -99.0),
                 metrics.get("hit_est", 0.0), payout, closeness)
        if best is None or score > best[0]:
            best = (score, legs)
    return list(best[1]) if best else None


def ticket_metrics(legs: list[dict]) -> dict:
    odds = math.prod(float(candidate["odds"]) for candidate in legs)
    probabilities = [probability_of(candidate.get("market_prob")) for candidate in legs]
    market_hit = math.prod(probabilities) if all(p is not None for p in probabilities) else None
    contracts = [_leg_probability_contract(candidate) for candidate in legs]
    calibrated_hit = (math.prod(contract[0] for contract in contracts)
                      if all(contract[0] is not None for contract in contracts) else None)
    independent_lower_hit = (math.prod(contract[1] for contract in contracts)
                             if all(contract[1] is not None for contract in contracts) else None)
    stressed_hit = frechet_hit = correlation_sensitivity = None
    if all(contract[1] is not None for contract in contracts):
        stressed_hit, frechet_hit, correlation_sensitivity = _correlation_stress(
            [contract[1] for contract in contracts]
        )
    has_validated_edge = bool(legs) and all(
        candidate.get("decision_pipeline_applied") is True
        and candidate.get("has_validated_edge") is True
        and probability_of(candidate.get("predicted_hit_prob")) is not None
        for candidate in legs
    )
    validated_uncertainty = has_validated_edge and all(contract[2] for contract in contracts)
    has_policy_authorized_shadow = bool(legs) and any(
        candidate.get("policy_authorized") is True
        and not (
            candidate.get("decision_pipeline_applied") is True
            and candidate.get("has_validated_edge") is True
        )
        for candidate in legs
    )
    out = {
        "actual_odds": round(odds, 2),
        "independent_hit_est": (round(calibrated_hit, 5)
                                 if calibrated_hit is not None else None),
        "market_reference_hit_est": (round(market_hit, 5)
                                      if market_hit is not None else None),
        "market_reference_roi": (round(market_hit * odds - 1.0, 4)
                                 if market_hit is not None else None),
        "independence_assumption": True,
        "independence_is_certainty": False,
        "correlation_stress_rho": CORRELATION_STRESS_RHO,
        "probability_basis": (
            "서로 다른 경기의 검증 보정 최종확률 독립 가정(확정값 아님)"
            if has_validated_edge
            else "정책 승인 신호는 진단 전용이며 Shin 시장확률로 복귀(확정값 아님)"
            if has_policy_authorized_shadow
            else "서로 다른 경기의 Shin 시장확률 독립 가정(확정값 아님)"
        ),
        "selection_basis": "final_hit_probability",
        "historical_expected_roi": round(
            math.prod(1.0 + historical_leg_score(candidate)[0] for candidate in legs) - 1.0,
            4,
        ),
        # 구형 산출물·브라우저 호환용 별칭. 현재는 위 최종 적중 추정치와 같다.
        "hit_est": (round(calibrated_hit, 5) if calibrated_hit is not None else None),
        "upset_risk": (round(1.0 - calibrated_hit, 5)
                        if calibrated_hit is not None else None),
        "expected_roi": (round(calibrated_hit * odds - 1.0, 4)
                         if calibrated_hit is not None else None),
        "calibrated_hit_est": (round(calibrated_hit, 5)
                               if calibrated_hit is not None else None),
        "calibrated_expected_roi": (round(calibrated_hit * odds - 1.0, 4)
                                     if calibrated_hit is not None else None),
        "independent_lower_hit_est": (round(independent_lower_hit, 5)
                                      if independent_lower_hit is not None else None),
        "correlation_stress_hit_est": (round(stressed_hit, 5)
                                       if stressed_hit is not None else None),
        "correlation_stress_expected_roi": (round(stressed_hit * odds - 1.0, 4)
                                             if stressed_hit is not None else None),
        "frechet_lower_hit_bound": (round(frechet_hit, 5)
                                    if frechet_hit is not None else None),
        "correlation_sensitivity": (round(correlation_sensitivity, 5)
                                    if correlation_sensitivity is not None else None),
        "conservative_hit_est": (round(stressed_hit, 5)
                                  if stressed_hit is not None else None),
        "conservative_expected_roi": (round(stressed_hit * odds - 1.0, 4)
                                        if stressed_hit is not None else None),
        "calibration_min_n": None,
        "has_validated_edge": has_validated_edge,
        "has_policy_authorized_probability": False,
        "has_policy_authorized_shadow": has_policy_authorized_shadow,
        "validated_uncertainty_available": validated_uncertainty,
        "probability_source": (
            "validated_final_probability"
            if has_validated_edge else "shin_market_fallback"
        ),
        "conservative_probability_source": (
            "validated_interval_correlation_stress"
            if validated_uncertainty else "shin_market_fallback_correlation_stress"
        ),
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


def _reference_metric(plan: dict, current: str, legacy: str, default: float) -> float:
    """새 의미가 명확한 필드를 우선하고 구형 저장물만 별칭으로 읽는다."""
    if plan.get(current) is not None:
        return _metric_number(plan, current, default)
    return _metric_number(plan, legacy, default)


def daily_recommendation(plans: list[dict]) -> dict:
    available = [plan for plan in plans if plan.get("ok")]
    if not available:
        return {"action": "none", "recommended_target": None,
                "why": "현재 선택 가능한 경기로 구성할 조합이 없다"}
    positive = [plan for plan in available
                if plan.get("has_validated_edge") is True
                and plan.get("validated_uncertainty_available") is True
                and _metric_number(plan, "conservative_expected_roi", -99.0) > 0.0]
    if positive:
        best = max(positive, key=lambda plan: (
            _kelly_growth(plan),
            _metric_number(plan, "correlation_stress_expected_roi", -99.0),
            _metric_number(plan, "correlation_stress_hit_est", 0.0),
        ))
        action = "buy"
        why = "사전 검증된 확률 하한에 상관 스트레스를 적용해도 기대수익이 양수다"
    else:
        challenge = [plan for plan in available
                     if _metric_number(plan, "target", 99.0) <= DAILY_CHALLENGE_MAX_TARGET
                     and _metric_number(plan, "correlation_stress_expected_roi",
                                        -99.0) >= DAILY_CHALLENGE_MIN_ROI
                     and _metric_number(plan, "correlation_stress_hit_est", 0.0) >=
                     DAILY_CHALLENGE_MIN_HIT.get(_metric_number(plan, "target", 99.0), float("inf"))]
        if challenge:
            best_challenge_roi = max(
                _metric_number(plan, "correlation_stress_expected_roi", -99.0)
                for plan in challenge)
            balanced = [plan for plan in challenge
                        if _metric_number(plan, "correlation_stress_expected_roi",
                                          -99.0) >=
                        best_challenge_roi - DAILY_CHALLENGE_ROI_TOLERANCE]
            best = max(balanced, key=lambda plan: (
                _metric_number(plan, "target", 0.0),
                _metric_number(plan, "correlation_stress_expected_roi", -99.0),
                _metric_number(plan, "correlation_stress_hit_est", 0.0)))
            action = "challenge"
            why = "현재 확률에 상관 스트레스를 적용한 3배 조합이 기대손실 −20.5% 이내와 적중 27% 문턱을 충족한다"
        else:
            best = max(available, key=lambda plan: (
                _metric_number(plan, "correlation_stress_expected_roi", -99.0),
                _metric_number(plan, "correlation_stress_hit_est", 0.0),
            ))
            action = "pass"
            why = "소액 도전 기준에도 미달했다"
    return {"action": action, "recommended_target": best["target"],
            "budget_ratio": (DAILY_CHALLENGE_BUDGET_RATIO if action == "challenge" else None),
            "market_reference_roi": _reference_metric(
                best, "market_reference_roi", "conservative_expected_roi", -99.0),
            "selection_historical_roi": _metric_number(
                best, "historical_expected_roi", -99.0),
            "independent_hit_est": _reference_metric(
                best, "independent_hit_est", "calibrated_hit_est", 0.0),
            "correlation_stress_hit_est": _metric_number(
                best, "correlation_stress_hit_est", 0.0),
            "correlation_stress_expected_roi": _metric_number(
                best, "correlation_stress_expected_roi", -99.0), "why": why}


def _game_context_index() -> dict[tuple[str, str, str, str], dict]:
    """전마켓 산출물의 LLM 해설·구조화 근거를 오늘 조합과 잇는다."""
    raw = load_runtime_artifact("picks_v2", PICKS_V2) or {}
    out = {}
    for game in [*(raw.get("live") or []), *(raw.get("past") or [])]:
        key = (str(game.get("date") or "")[:5], str(game.get("league") or ""),
               str(game.get("home") or ""), str(game.get("away") or ""))
        out[key] = game
    return out


def _approved_decision(snapshot: dict) -> bool:
    """원장 계약 검증과 운영 승인을 모두 통과한 최종 판정만 허용한다."""
    try:
        validate_decision_snapshot(snapshot)
    except (TypeError, ValueError):
        return False
    model = snapshot.get("model") or {}
    return can_apply_decision_probability(model)


def _matching_decision_option(candidate: dict, game: dict) -> dict | None:
    snapshot = game.get("decision_snapshot") or {}
    selection_id = snapshot.get("selection_id")
    snapshot_market = probability_of((snapshot.get("probability") or {}).get("market"))
    if not selection_id:
        return None
    for option in game.get("options") or []:
        if option.get("selection_id") != selection_id:
            continue
        if (str(option.get("market") or "") == str(candidate.get("market") or "")
                and str(option.get("label") or "") == str(candidate.get("market_label") or "")
                and str(option.get("선택") or "") == str(candidate.get("sel") or "")):
            try:
                same_price = math.isclose(
                    float(option.get("배당")), float(candidate.get("odds")), abs_tol=0.005
                )
                same_market_probability = math.isclose(
                    float(option.get("시장확률")), float(candidate.get("market_prob")),
                    abs_tol=0.0001,
                )
                same_snapshot_probability = snapshot_market is not None and math.isclose(
                    snapshot_market, float(candidate.get("market_prob")), abs_tol=0.0001
                )
            except (TypeError, ValueError):
                return None
            snapshot_offer = snapshot.get("offer_id")
            option_offer = option.get("offer_id")
            same_offer = bool(
                snapshot_offer and option_offer and snapshot_offer == option_offer
            )
            return option if (same_price and same_market_probability
                              and same_snapshot_probability and same_offer) else None
    return None


def _apply_decision_pipeline(candidate: dict, game: dict) -> None:
    """개편 원장의 동일 선택 최종확률·근거를 오늘 추천 후보에 연결한다."""
    snapshot = game.get("decision_snapshot") or {}
    option = _matching_decision_option(candidate, game)
    matched = option is not None
    approved = bool(option and _approved_decision(snapshot))
    probability = snapshot.get("probability") or {}
    model = snapshot.get("model") or {}
    final = probability_of(probability.get("final")) if approved else None
    market = probability_of(candidate.get("market_prob"))
    validated = bool(final is not None and can_apply_decision_probability(model))
    policy_authorized = bool(
        matched
        and model.get("status") == "operational"
        and model.get("promotion_gate") == "passed"
        and model.get("policy_authorized") is True
    )
    interval = probability.get("residual_interval") if validated else None
    interval_lower = (
        probability_of(interval[0])
        if isinstance(interval, (list, tuple)) and len(interval) == 2
        else None
    )
    if interval_lower is not None and final is not None and interval_lower > final:
        interval_lower = None
    fallback_lower = min(market, final) if market is not None and final is not None else market
    candidate.update({
        "decision_id": snapshot.get("decision_id") if matched else None,
        "decision_model": model.get("operating_version") if matched else None,
        "decision_pipeline_status": model.get("status") if matched else "market_fallback",
        "decision_pipeline_applied": bool(final is not None),
        "decision_promotion_gate": model.get("promotion_gate") if matched else None,
        "decision_artifact_hash": model.get("artifact_hash") if matched else None,
        "predicted_hit_prob": round(final if final is not None else market, 4)
        if final is not None or market is not None else None,
        "probability_source": (
            probability.get("basis") or "approved_decision_pipeline"
            if final is not None else "shin_market_fallback"
        ),
        "has_validated_edge": validated,
        "policy_authorized": policy_authorized,
        "probability_lower_bound": round(
            interval_lower if interval_lower is not None else fallback_lower, 4
        ) if interval_lower is not None or fallback_lower is not None else None,
        "probability_interval": list(interval) if interval_lower is not None else None,
        "uncertainty_source": (
            "validated_residual_interval"
            if interval_lower is not None else "shin_market_fallback"
        ),
        "validated_uncertainty_available": interval_lower is not None,
        "selection_basis": (
            "validated_decision_pipeline" if validated
            else "shin_market_fallback"
        ),
        "decision_evidence_ids": [
            row.get("id") for row in snapshot.get("evidence") or []
            if row.get("id")
        ] if matched else [],
    })
    if final is None:
        return
    candidate["reason"] = (
        "개편된 판정 원장에서 동일 선택·동일 배당 revision이 확인됐고, "
        f"{candidate['decision_model']} 최종확률을 추천 순위에 반영했다. "
        + ("통계 검증 하한도 보수 확률에 반영했다."
           if validated and interval_lower is not None
           else "통계 검증 하한은 없어 보수 확률은 시장값으로 복귀한다.")
    )


def _candidate_reason(candidate: dict) -> str:
    probability = probability_of(candidate.get("predicted_hit_prob"))
    probability_text = (
        f"{probability * 100:.1f}%" if probability is not None else "계산 불가"
    )
    reason = (
        f"같은 경기의 유효 후보 중 최종 적중확률 {probability_text}를 우선해 남긴 "
        f"{candidate['bin']} 배당 구간 선택이다. 검증된 AI 보정이 없으므로 이 확률은 "
        "동일 시점 Shin 시장확률이다. "
    )
    if candidate.get("hist_roi") is not None and candidate.get("hist_n"):
        reason += (f"배당구간 과거 실측 수익률 {float(candidate['hist_roi']) * 100:.1f}%"
                   f"(n={int(candidate['hist_n']):,})는 진단값이며 선택 순위를 바꾸지 않는다.")
    if candidate.get("beats"):
        reason += (
            f" 같은 경기의 {candidate['beats']['round']}회차 "
            f"{candidate['beats']['odds']:.2f}보다 {candidate['round']}회차 "
            f"{candidate['odds']:.2f}가 더 높다."
        )
    return reason


def _enrich_candidates(candidates: list[dict]) -> list[dict]:
    index = _game_context_index()
    for candidate in candidates:
        candidate["reason"] = _candidate_reason(candidate)
        key = (str(candidate.get("date") or "")[:5],
               str(candidate.get("league") or ""),
               str(candidate.get("home") or ""),
               str(candidate.get("away") or ""))
        game = index.get(key)
        if not game:
            continue
        _apply_decision_pipeline(candidate, game)
        commentary = str(game.get("근거해설") or game.get("해설") or "").strip()
        if commentary:
            candidate["context_summary"] = (
                commentary if len(commentary) <= 360
                else commentary[:359].rstrip() + "…"
            )
            candidate["commentary_method"] = (
                game.get("근거해설방식") or game.get("해설방식")
            )
        if game.get("경기근거"):
            candidate["evidence"] = game["경기근거"]
    return candidates


def retain_started_candidates(current: list[dict], previous: dict,
                              now: datetime) -> list[dict]:
    """오늘 시작 전 저장된 추천을 시작·종료 뒤에도 표시 원장에 보존한다."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)
    merged = {
        (row.get("event_key"), row.get("market"), str(row.get("market_label") or ""),
         row.get("sel")): row
        for row in current
    }
    previous_generated = str(previous.get("generated_at") or "")
    for original in previous.get("candidates") or []:
        try:
            kickoff = datetime.fromisoformat(str(original.get("kickoff_at") or ""))
            recommended_at = datetime.fromisoformat(
                str(original.get("recommended_at") or previous_generated)
            )
        except (TypeError, ValueError):
            continue
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=KST)
        if recommended_at.tzinfo is None:
            recommended_at = recommended_at.replace(tzinfo=KST)
        kickoff = kickoff.astimezone(KST)
        recommended_at = recommended_at.astimezone(KST)
        if not (kickoff.date() == now.date() and recommended_at < kickoff <= now):
            continue
        row = {
            **original,
            "recommended_at": recommended_at.isoformat(timespec="seconds"),
            "recommendation_state": "started_locked",
        }
        key = (row.get("event_key"), row.get("market"),
               str(row.get("market_label") or ""), row.get("sel"))
        merged.setdefault(key, row)
    return sorted(merged.values(), key=lambda row: (
        str(row.get("kickoff_at") or ""), str(row.get("league") or ""),
        str(row.get("match") or ""),
    ))


def build() -> dict:
    source = _candidate_source()
    live_generated_at = source.get("generated_at") if source["candidate_source"] == "live_odds" else None
    cands = select_event_candidates(
        _enrich_candidates(legs_today(source=source))
    )
    evolutionary = live_snapshot(cands, load_artifact(EVOLUTION_ARTIFACT))
    # 시작했다고 사전 추천 기록을 지우면 적중 결과를 추적할 수 없다. 직전 생성물이
    # 실제 킥오프 전에 저장한 오늘 후보만 잠그고, 새 조합 계산에는 섞지 않는다.
    previous = load_runtime_artifact("today_combo", OUT) or {}
    display_cands = retain_started_candidates(cands, previous, datetime.now(KST))

    grades = load_runtime_artifact("loss_grades", GRADES) or {}
    return {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source_generated_at": source.get("generated_at"),
        "candidate_source": source["candidate_source"],
        "live_odds_at": live_generated_at,
        "year": source.get("year"),
        "probability_method": MARKET_PROBABILITY_METHOD,
        "basis": "경기별 1.50~2.20 미만 유효 후보를 우선하고 그 안에서 최종 "
                 "예상 적중확률이 가장 높은 선택을 고른다. 해당 가격대가 없을 "
                 "때만 저배당 보조 후보를 허용하며, 검증된 AI 보정이 없으면 "
                 "동일 시점 Shin 시장확률로 복귀한다.",
        "n_candidates": len(display_cands),
        "n_primary_candidates": sum(
            1 for candidate in display_cands if candidate.get("recommendation_priority") == "primary"
        ),
        "n_fallback_candidates": sum(
            1 for candidate in display_cands if candidate.get("recommendation_priority") == "fallback"
        ),
        "n_better_round": sum(1 for c in cands if c.get("beats")),
        "next_kickoff_at": min((c["kickoff_at"] for c in cands), default=None),
        "selection_policy": "1.50~2.20 우선 · 없으면 저배당 보조 · 최종 적중확률 순 · 자동 조합 없음",
        "preferred_leg_odds_inclusive": PREFERRED_RECOMMENDATION_ODDS,
        "evolutionary_selector": evolutionary,
        "max_leg_odds_exclusive": MAX_AUTO_RECOMMENDATION_ODDS,
        "solo": None,
        "plans": [],
        "recommendation": {
            "action": "disabled", "recommended_target": None,
            "why": "자동 조합 추천 정책을 종료하고 경기별 추천만 운영한다",
        },
        "candidates": display_cands,
        "odds_bins": grades["odds_bins"],
        "note": "검증된 시장 잔차가 없어 추천확률은 Shin 시장확률로 복귀한다. "
                "자동 조합 추천과 목표배당 판정은 운영에서 제거하고 경기별 추천만 "
                "제공한다. 사용자가 저장한 베팅 기록의 결과 추적은 계속 유지한다. "
                "과거 배당구간 ROI를 개별 후보 적중확률로 바꾸지 않는다. "
                "자체 득점 모델은 시장보다 부정확해 자동 선택에 쓰지 않는다. "
                "비극단 가격·시장확률·shadow 모델 괴리 관문을 통과한 역배도 연구 "
                "진단으로만 남기며, 시간순 외부검증을 통과하기 전에는 시장 최유력 "
                "방향을 교체하지 않는다. "
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
            print(f"  [건너뜀] 목표 {p['target']}배 - {p['why']}")
            continue
        gs = [c["event_key"] for c in p["picks"]]
        if len(set(gs)) != len(gs):
            bad.append(f"목표 {p['target']}× : 같은 경기를 두 번 썼다 {gs} — 규정 위반")
        if any(c["bin"] in BANNED for c in p["picks"]):
            bad.append(f"목표 {p['target']}× : 금지 배당대가 섞였다")
        if any(float(c["odds"]) < PREFERRED_RECOMMENDATION_ODDS for c in p["picks"]):
            bad.append(f"목표 {p['target']}× : 1순위 조합에 1.50 미만 보조 선택지가 섞였다")
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
        print(f"  [통과] 목표 {p['target']}배 - {p['legs']}폴 · 실배당 {p['actual_odds']}배 · "
              f"서로 다른 경기 {len(set(gs))}개")
    now = datetime.now(KST)
    # 시작 전에 저장한 추천은 성적 추적을 위해 ``started_locked``로 남기는 것이
    # 정상이다. 새 조합 후보인 것처럼 남은 행만 오류로 본다.
    past = [
        candidate for candidate in d.get("candidates", [])
        if candidate.get("recommendation_state") != "started_locked"
        and datetime.fromisoformat(candidate["kickoff_at"]) <= now
    ]
    if past:
        bad.append(f"시작한 경기 {len(past)}개가 후보에 남았다")
    if d["solo"] and d["solo"]["bin"] in BANNED:
        bad.append("단폴이 금지 배당대다")
    for name, profile in (d.get("evolutionary_selector", {}).get("profiles") or {}).items():
        selected = profile.get("selected")
        if selected and datetime.fromisoformat(selected["kickoff_at"]) <= now:
            bad.append(f"자연선택 {name}에 시작한 경기가 남았다")
    if bad:
        print("\n[오류] " + "\n[오류] ".join(bad))
        return 1
    print("\n[통과] 오늘의 조합 자기검사")
    return 0


def main() -> int:
    d = build()
    persist_artifact("today_combo", d, OUT)
    # DB 정본 산출물을 정적 사이트가 읽는 docs/data/*.json 으로 내보낸다. 운영에서
    # 생성기는 DB 에만 쓰므로, 이 단계가 없으면 배포 사이트가 이관 시점 값에서 멈춘다.
    try:
        written = export_site_artifacts()
        if written:
            print(f"사이트 파일 갱신: {', '.join(written)}")
    except Exception as exc:                             # noqa: BLE001
        print(f"사이트 파일 내보내기 실패: {type(exc).__name__}: {exc}")

    print(f"다리 후보 {d['n_candidates']:,}개 "
          f"(1순위 {d['n_primary_candidates']:,} · 1.50 미만 보조 {d['n_fallback_candidates']:,})\n")
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
