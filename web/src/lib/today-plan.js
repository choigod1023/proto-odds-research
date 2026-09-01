import { eligibleFinalSelections, hitProbabilityOf,
  recommendationPriority } from "./recommendation-policy.js";
import { refreshEvolutionarySelector } from "./evolutionary-selector.js";

const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const NEXT_MORNING_END_HOUR = 12;
const DATE_TIME = /(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})/;
export const MAX_TODAY_RECHECK_MS = 30 * 60 * 1000;
export const SAFE_TARGET_BINS = {
  3: ["1.5-1.8", "1.5-1.8"],
  5: ["1.5-1.8", "1.5-1.8", "1.5-1.8"],
  8: ["1.8-2.2", "1.8-2.2", "1.8-2.2"],
  12: ["1.5-1.8", "1.8-2.2", "1.8-2.2", "1.8-2.2"],
};

export const DAILY_CHALLENGE_MIN_ROI = -0.205;
export const DAILY_CHALLENGE_MIN_HIT = { 3: 0.27 };
export const DAILY_CHALLENGE_MAX_TARGET = 3;
export const DAILY_CHALLENGE_ROI_TOLERANCE = 0.03;
export const DAILY_CHALLENGE_BUDGET_RATIO = 0.10;

export function kickoffTime(candidate, year) {
  const iso = Date.parse(candidate?.kickoff_at || "");
  if (Number.isFinite(iso)) return iso;

  const match = String(candidate?.date || "").match(DATE_TIME);
  if (!match) return Number.NaN;
  const [, month, day, hour, minute] = match.map(Number);
  const sourceYear = Number(year) || new Date().getUTCFullYear();
  const gameYear = Number(candidate?.round) === 1 && month === 12
    ? sourceYear - 1 : sourceYear;
  return Date.UTC(gameYear, month - 1, day, hour, minute) - KST_OFFSET_MS;
}

const eventKey = (candidate, year) =>
  candidate?.event_key ||
  `${kickoffTime(candidate, year)}|${candidate?.home || ""}|${candidate?.away || candidate?.match || ""}`;

function byNextKickoff(a, b, year) {
  return (
    kickoffTime(a, year) - kickoffTime(b, year) ||
    Number(a.overround || 99) - Number(b.overround || 99) ||
    Number(b.odds || 0) - Number(a.odds || 0)
  );
}

function byLegQuality(a, b, year) {
  const priorityOrder = recommendationPriority(b) - recommendationPriority(a);
  const aCalibrated = calibratedLegProbability(a);
  const bCalibrated = calibratedLegProbability(b);
  const aOdds = Number(a?.odds || 0);
  const bOdds = Number(b?.odds || 0);
  const conservativeOrder = (bCalibrated.lower ?? 0) - (aCalibrated.lower ?? 0);
  const calibratedOrder = (bCalibrated.estimate ?? 0) - (aCalibrated.estimate ?? 0);
  const aProbability = hitProbabilityOf(a);
  const bProbability = hitProbabilityOf(b);
  const probabilityOrder =
    (Number.isFinite(bProbability) ? bProbability : 0) -
    (Number.isFinite(aProbability) ? aProbability : 0);
  const returnOrder = (bCalibrated.lower ?? 0) * bOdds -
    (aCalibrated.lower ?? 0) * aOdds;
  return priorityOrder || conservativeOrder || calibratedOrder || probabilityOrder || returnOrder ||
    Number(a.overround || 99) - Number(b.overround || 99) ||
    byNextKickoff(a, b, year);
}

function candidatePool(candidates, bin, year) {
  const byPrice = new Map();
  for (const candidate of candidates.filter((row) => row.bin === bin)) {
    const bucket = Math.round(Number(candidate.odds) / 0.05);
    if (!byPrice.has(bucket)) byPrice.set(bucket, []);
    byPrice.get(bucket).push(candidate);
  }
  return [...byPrice.values()]
    .flatMap((rows) => rows.sort((a, b) => byLegQuality(a, b, year)).slice(0, 3))
    .sort((a, b) => byLegQuality(a, b, year));
}

function betterScore(score, previous) {
  if (!previous) return true;
  for (let index = 0; index < score.length; index += 1) {
    if (score[index] !== previous[index]) return score[index] > previous[index];
  }
  return false;
}

const validProbability = (value) => {
  const probability = Number(value);
  return Number.isFinite(probability) && probability > 0 && probability < 1
    ? probability : null;
};

export function calibratedLegProbability(candidate) {
  const finalProbability = validProbability(hitProbabilityOf(candidate));
  return { estimate: finalProbability, lower: finalProbability, n: null };
}

export function pickNextLegs(candidates, bins, year, target = null) {
  const eligible = eligibleFinalSelections(candidates);
  const pools = bins.map((bin) => candidatePool(eligible, bin, year));
  if (pools.some((pool) => !pool.length)) return null;
  const lower = target ? Number(target) * 0.95 : 0;
  const upper = target ? Number(target) * 1.15 : Number.POSITIVE_INFINITY;
  let best = null;

  function search(index, picks, used, actualOdds, hitEstimate, payout) {
    if (actualOdds > upper) return;
    if (index === pools.length) {
      if (actualOdds < lower) return;
      const closeness = target ? -Math.abs(Math.log(actualOdds / Number(target))) : 0;
      const metrics = ticketMetrics(picks);
      const score = [
        metrics.independent_hit_est ?? 0,
        metrics.market_reference_roi ?? Number.NEGATIVE_INFINITY,
        hitEstimate,
        payout,
        closeness,
      ];
      if (betterScore(score, best?.score)) best = { score, picks: [...picks] };
      return;
    }
    for (const candidate of pools[index]) {
      const key = eventKey(candidate, year);
      if (used.has(key)) continue;
      const probability = hitProbabilityOf(candidate);
      const nextHit = Number.isFinite(probability) && probability > 0 && probability < 1
        ? hitEstimate * probability : 0;
      used.add(key);
      picks.push(candidate);
      search(index + 1, picks, used, actualOdds * Number(candidate.odds), nextHit,
        payout / Number(candidate.overround));
      picks.pop();
      used.delete(key);
    }
  }

  search(0, [], new Set(), 1, 1, 1);
  return best?.picks || null;
}

export function ticketMetrics(picks) {
  const actualOdds = picks.reduce((total, pick) => total * Number(pick.odds), 1);
  const probabilities = picks.map((pick) => validProbability(pick.market_prob));
  const marketHit = probabilities.every((probability) => probability != null)
    ? probabilities.reduce((total, probability) => total * probability, 1) : null;
  const finalProbabilities = picks.map((pick) => validProbability(hitProbabilityOf(pick)));
  const finalHit = finalProbabilities.every((probability) => probability != null)
    ? finalProbabilities.reduce((total, probability) => total * probability, 1) : null;
  const calibrated = picks.map(calibratedLegProbability);
  const calibratedHit = calibrated.every(({ estimate }) => estimate != null)
    ? calibrated.reduce((total, { estimate }) => total * estimate, 1) : null;
  const conservativeHit = calibrated.every(({ lower }) => lower != null)
    ? calibrated.reduce((total, { lower }) => total * lower, 1) : null;
  const samples = calibrated.map(({ n }) => n).filter((n) => Number.isFinite(n) && n > 0);
  const result = {
    actual_odds: Number(actualOdds.toFixed(2)),
    probability_basis: picks.every((pick) => pick?.has_validated_edge === true)
      ? "서로 다른 경기의 검증 보정 최종확률 독립 가정"
      : "서로 다른 경기의 Shin 시장확률 복귀값 독립 가정",
    independence_assumption: true,
  };
  if (marketHit != null) {
    result.market_reference_hit_est = Number(marketHit.toFixed(5));
    result.market_reference_roi = Number((marketHit * actualOdds - 1).toFixed(4));
  }
  if (finalHit != null) {
    result.independent_hit_est = Number(finalHit.toFixed(5));
    result.hit_est = Number(finalHit.toFixed(5));
    result.upset_risk = Number((1 - finalHit).toFixed(5));
    result.expected_roi = Number((finalHit * actualOdds - 1).toFixed(4));
  }
  if (calibratedHit != null) {
    result.calibrated_hit_est = Number(calibratedHit.toFixed(5));
    result.calibrated_expected_roi = Number((calibratedHit * actualOdds - 1).toFixed(4));
  }
  if (conservativeHit != null) {
    result.conservative_hit_est = Number(conservativeHit.toFixed(5));
    result.conservative_expected_roi = Number((conservativeHit * actualOdds - 1).toFixed(4));
  }
  result.calibration_min_n = samples.length ? Math.min(...samples) : null;
  result.has_validated_edge = picks.length > 0 &&
    picks.every((pick) => pick?.has_validated_edge === true);
  result.probability_source = result.has_validated_edge
    ? "validated_final_probability" : "shin_market_fallback";
  return result;
}

function kellyGrowth(plan) {
  const probability = validProbability(plan?.conservative_hit_est);
  const odds = Number(plan?.actual_odds);
  if (probability == null || !(odds > 1) || probability * odds <= 1) {
    return Number.NEGATIVE_INFINITY;
  }
  const full = Math.min(1, Math.max(0, (probability * odds - 1) / (odds - 1)));
  const fraction = full * 0.5;
  return probability * Math.log1p(fraction * (odds - 1)) +
    (1 - probability) * Math.log1p(-fraction);
}

function metricNumber(plan, key, fallback) {
  const raw = plan?.[key];
  if (raw == null || raw === "") return fallback;
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

function referenceMetric(plan, current, legacy, fallback) {
  return plan?.[current] != null
    ? metricNumber(plan, current, fallback)
    : metricNumber(plan, legacy, fallback);
}

function selectionLossMetric(plan) {
  return plan?.historical_expected_roi != null
    ? metricNumber(plan, "historical_expected_roi", -99)
    : referenceMetric(plan, "market_reference_roi", "conservative_expected_roi", -99);
}

export function recommendationFromPlans(plans) {
  const available = (plans || []).filter((plan) => plan?.ok);
  if (!available.length) return { action: "none", target: null, index: -1,
    why: "현재 선택 가능한 경기로 구성할 조합이 없다" };
  const positive = available.filter((plan) => plan.has_validated_edge === true &&
    referenceMetric(plan, "market_reference_roi", "conservative_expected_roi", -99) > 0);
  const challenge = available.filter((plan) =>
    metricNumber(plan, "target", 99) <= DAILY_CHALLENGE_MAX_TARGET &&
    selectionLossMetric(plan) >= DAILY_CHALLENGE_MIN_ROI &&
    referenceMetric(plan, "independent_hit_est", "calibrated_hit_est", 0) >=
      (DAILY_CHALLENGE_MIN_HIT[metricNumber(plan, "target", 99)] ?? Number.POSITIVE_INFINITY));
  const byRiskAdjustedQuality = (a, b) =>
    selectionLossMetric(b) - selectionLossMetric(a) ||
    referenceMetric(b, "independent_hit_est", "calibrated_hit_est", 0) -
      referenceMetric(a, "independent_hit_est", "calibrated_hit_est", 0);

  let action;
  let best;
  let why;
  if (positive.length) {
    action = "buy";
    best = [...positive].sort((a, b) =>
      kellyGrowth(b) - kellyGrowth(a) || byRiskAdjustedQuality(a, b))[0];
    why = "사전 검증된 독립 확률모델의 기대수익이 양수다";
  } else if (challenge.length) {
    action = "challenge";
    const bestChallengeRoi = Math.max(...challenge.map((plan) =>
      selectionLossMetric(plan)));
    const balanced = challenge.filter((plan) =>
      selectionLossMetric(plan) >= bestChallengeRoi - DAILY_CHALLENGE_ROI_TOLERANCE);
    best = [...balanced].sort((a, b) =>
      metricNumber(b, "target", 0) - metricNumber(a, "target", 0) || byRiskAdjustedQuality(a, b))[0];
    why = "자체 과거 실측 손실 −20.5% 이내와 독립 가정 적중 27% 문턱을 충족한다";
  } else {
    action = "pass";
    best = [...available].sort(byRiskAdjustedQuality)[0];
    why = "구매 기준에도 미달했다";
  }
  const index = available.findIndex((plan) => plan.target === best.target);
  return {
    action,
    target: best.target,
    index,
    budget_ratio: action === "challenge" ? DAILY_CHALLENGE_BUDGET_RATIO : null,
    why,
  };
}

/** 헤더의 자동 판정과 처음 펼쳐지는 티켓이 같은 조합을 가리키게 한다. */
export function ticketIndexForRecommendation(recommendation, plans, solo = null) {
  const available = (plans || []).filter((plan) => plan?.ok);
  if (recommendation?.action === "solo" && solo) return -1;
  const stated = Number(recommendation?.index);
  if (Number.isInteger(stated) && stated >= 0 && stated < available.length) return stated;
  const byTarget = available.findIndex((plan) =>
    Number(plan.target) === Number(recommendation?.target));
  if (byTarget >= 0) return byTarget;
  if (!available.length && solo) return -1;
  return 0;
}

/**
 * 자동 패스와 별개로, 사용자가 고른 도전 강도와 투입 금액을 조합한다.
 * 금액이 커져도 저배당으로 자동 변경하지 않으며, 모든 금액 카드가 같은
 * 목표 배당 조합을 가리킨다. +EV 신호로 승격하지는 않는다.
 */
export function challengeOptions(plans, budget, desiredTarget = 3) {
  const dayBudget = Math.max(1000, Math.min(100000, Number(budget) || 10000));
  const requestedTarget = Number.isFinite(Number(desiredTarget)) && Number(desiredTarget) > 1
    ? Number(desiredTarget) : 3;
  const stakes = [...new Set([0.1, 0.3, 0.5, 1].map((ratio) =>
    Math.min(dayBudget, Math.max(1000, Math.floor(dayBudget * ratio / 1000) * 1000))))];
  const available = (plans || []).map((plan, index) => ({ plan, index })).filter(({ plan }) => {
    const odds = Number(plan?.actual_odds);
    return plan?.ok && Number.isFinite(odds) && odds > 1;
  });
  if (!available.length) return [];

  const probabilityOfPlan = (plan) => {
    const independent = Number(plan?.independent_hit_est);
    if (plan?.independent_hit_est != null && Number.isFinite(independent) &&
      independent > 0 && independent < 1) return independent;
    const calibrated = Number(plan?.calibrated_hit_est);
    if (plan?.calibrated_hit_est != null && Number.isFinite(calibrated) &&
      calibrated > 0 && calibrated < 1) return calibrated;
    const market = Number(plan?.hit_est);
    return Number.isFinite(market) && market > 0 && market < 1 ? market : 0;
  };
  const conservativeRoiOf = (plan) => {
    const reference = Number(plan?.market_reference_roi);
    if (plan?.market_reference_roi != null && Number.isFinite(reference)) return reference;
    const conservative = Number(plan?.conservative_expected_roi);
    if (plan?.conservative_expected_roi != null && Number.isFinite(conservative))
      return conservative;
    const calibrated = Number(plan?.calibrated_expected_roi);
    return Number.isFinite(calibrated) ? calibrated : -1;
  };

  const enriched = available.map(({ plan, index }) => ({
    plan,
    index,
    target: Number(plan.target),
    hit: probabilityOfPlan(plan),
    roi: conservativeRoiOf(plan),
  })).filter((row) => Number.isFinite(row.target));
  if (!enriched.length) return [];

  const atOrAbove = enriched.filter((row) => row.target >= requestedTarget);
  const best = [...(atOrAbove.length ? atOrAbove : enriched)].sort((a, b) =>
    atOrAbove.length
      ? a.target - b.target || b.hit - a.hit || b.roi - a.roi
      : b.target - a.target || b.hit - a.hit || b.roi - a.roi)[0];

  return stakes.map((stake) => {
    const net = stake * (Number(best.plan.actual_odds) - 1);
    return {
      stake,
      budget_share: stake / dayBudget,
      requested_target: requestedTarget,
      plan_index: best.index,
      target: best.plan.target,
      actual_odds: Number(best.plan.actual_odds),
      independent_hit_est: best.hit,
      market_reference_roi: best.roi,
      calibrated_hit_est: best.hit,
      conservative_expected_roi: best.roi,
      net_profit: Math.round(net),
      conservative_loss: Math.round(Math.max(0, -best.roi * stake)),
    };
  });
}

function legacyCandidates(today) {
  const all = [today?.solo, ...(today?.plans || []).flatMap((plan) => plan.picks || [])]
    .filter(Boolean);
  const seen = new Set();
  return all.filter((candidate) => {
    const key = `${eventKey(candidate, today?.year)}|${candidate.market}|${candidate.market_label}|${candidate.sel}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const kstDay = (time) => Math.floor((time + KST_OFFSET_MS) / DAY_MS);
const nextKstMidnight = (now) => (kstDay(now) + 1) * DAY_MS - KST_OFFSET_MS;

function futureCandidatesInWindow(today, now) {
  if (!today) return { candidates: [], window: "today" };
  const source = today.candidates?.length ? today.candidates : legacyCandidates(today);
  const future = eligibleFinalSelections(source).filter((candidate) => {
    const kickoff = kickoffTime(candidate, today.year);
    return Number.isFinite(kickoff) && kickoff > now;
  });
  const currentDay = kstDay(now);
  const todayCandidates = future.filter((candidate) =>
    kstDay(kickoffTime(candidate, today.year)) === currentDay);
  if (todayCandidates.length) return { candidates: todayCandidates, window: "today" };
  const morningEnd = (currentDay + 1) * DAY_MS - KST_OFFSET_MS
    + NEXT_MORNING_END_HOUR * 60 * 60 * 1000;
  return {
    candidates: future.filter((candidate) => {
      const kickoff = kickoffTime(candidate, today.year);
      return kstDay(kickoff) === currentDay + 1 && kickoff < morningEnd;
    }),
    window: "next_morning",
  };
}

/**
 * 다음 경기 시작 직후 다시 판정하되, 후보가 멀리 있어도 30분마다 시계를 보정한다.
 * 브라우저 타이머는 절전 중 늦어질 수 있으므로 화면 복귀 이벤트에서도 별도로 호출한다.
 */
export function nextTodayRefreshDelay(
  today,
  now = Date.now(),
  maxWait = MAX_TODAY_RECHECK_MS,
) {
  const waitLimit = Number.isFinite(maxWait) && maxWait > 0
    ? maxWait : MAX_TODAY_RECHECK_MS;
  if (!today) return waitLimit;
  const nextKickoff = futureCandidatesInWindow(today, now).candidates
    .map((candidate) => kickoffTime(candidate, today.year))
    .sort((a, b) => a - b)[0];
  const wakeAt = Number.isFinite(nextKickoff) ? nextKickoff : nextKstMidnight(now);
  // 시작 시각과 같은 밀리초에 경계 판정이 흔들리지 않도록 1초 뒤에 갱신한다.
  return Math.min(waitLimit, Math.max(1000, wakeAt - now + 1000));
}

export function availableToday(today, now = Date.now()) {
  if (!today) return { plans: [], solo: null, candidates: [], next: null };
  const activeWindow = futureCandidatesInWindow(today, now);
  const candidates = activeWindow.candidates
    .sort((a, b) => byNextKickoff(a, b, today.year));

  const plans = (today.plans || []).map((plan) => {
    const bins = SAFE_TARGET_BINS[Number(plan.target)] ||
      (plan.bins?.length ? plan.bins : (plan.picks || []).map((pick) => pick.bin));
    const picks = pickNextLegs(candidates, bins, today.year, plan.target);
    if (!picks) {
      return { ...plan, ok: false,
        why: "1.50~2.20 미만 최종 적중 우선 경기만으로 조합할 수 없다" };
    }
    return {
      ...plan,
      ...ticketMetrics(picks),
      ok: true,
      bins,
      picks,
    };
  });

  const solo = [...candidates]
    .sort((a, b) => byLegQuality(a, b, today.year))[0] || null;
  const measuredSolo = solo ? { ...solo, ...ticketMetrics([solo]) } : null;

  return {
    ...today,
    plans,
    solo: measuredSolo,
    recommendation: recommendationFromPlans(plans, measuredSolo),
    candidates,
    window: activeWindow.window,
    next: candidates[0] || null,
    evolutionary_selector: refreshEvolutionarySelector(today.evolutionary_selector, candidates),
  };
}
