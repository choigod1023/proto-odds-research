import { eligibleAutoSelections } from "./recommendation-policy.js";

const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const DATE_TIME = /(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})/;
export const MAX_TODAY_RECHECK_MS = 30 * 60 * 1000;
export const SAFE_TARGET_BINS = {
  1.4: ["1.0-1.3", "1.0-1.3"],
  2: ["1.0-1.3", "1.5-1.8"],
  3: ["1.3-1.5", "1.8-2.2"],
  5: ["1.5-1.8", "1.5-1.8", "1.5-1.8"],
  8: ["1.8-2.2", "1.8-2.2", "1.8-2.2"],
  12: ["1.5-1.8", "1.8-2.2", "1.8-2.2", "1.8-2.2"],
};

export function kickoffTime(candidate, year) {
  const iso = Date.parse(candidate?.kickoff_at || "");
  if (Number.isFinite(iso)) return iso;

  const match = String(candidate?.date || "").match(DATE_TIME);
  if (!match) return Number.NaN;
  const [, month, day, hour, minute] = match.map(Number);
  const sourceYear = Number(year) || new Date().getUTCFullYear();
  return Date.UTC(sourceYear, month - 1, day, hour, minute) - KST_OFFSET_MS;
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
  const aCalibrated = calibratedLegProbability(a);
  const bCalibrated = calibratedLegProbability(b);
  const aOdds = Number(a?.odds || 0);
  const bOdds = Number(b?.odds || 0);
  const conservativeOrder = (bCalibrated.lower ?? 0) * bOdds -
    (aCalibrated.lower ?? 0) * aOdds;
  const calibratedOrder = (bCalibrated.estimate ?? 0) * bOdds -
    (aCalibrated.estimate ?? 0) * aOdds;
  const aProbability = Number(a?.market_prob);
  const bProbability = Number(b?.market_prob);
  const probabilityOrder =
    (Number.isFinite(bProbability) ? bProbability : 0) -
    (Number.isFinite(aProbability) ? aProbability : 0);
  return conservativeOrder || calibratedOrder || probabilityOrder ||
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
  const market = validProbability(candidate?.market_prob);
  const odds = Number(candidate?.odds);
  const roi = Number(candidate?.hist_roi);
  const n = Number(candidate?.hist_n);
  if (!(odds > 1) || !Number.isFinite(roi) || roi <= -0.99 || roi >= 5) {
    return { estimate: market, lower: market, n: null };
  }
  const estimate = Math.min(0.999, Math.max(0.001, (1 + roi) / odds));
  if (!Number.isFinite(n) || n < 30) return { estimate, lower: estimate, n: null };
  const z = 1.645;
  const z2 = z * z;
  const denominator = 1 + z2 / n;
  const center = (estimate + z2 / (2 * n)) / denominator;
  const margin = z * Math.sqrt(estimate * (1 - estimate) / n + z2 / (4 * n * n)) / denominator;
  return { estimate, lower: Math.max(0.001, center - margin), n };
}

export function pickNextLegs(candidates, bins, year, target = null) {
  const eligible = eligibleAutoSelections(candidates);
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
        metrics.conservative_expected_roi ?? Number.NEGATIVE_INFINITY,
        metrics.calibrated_hit_est ?? 0,
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
      const probability = Number(candidate.market_prob);
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
  const calibrated = picks.map(calibratedLegProbability);
  const calibratedHit = calibrated.every(({ estimate }) => estimate != null)
    ? calibrated.reduce((total, { estimate }) => total * estimate, 1) : null;
  const conservativeHit = calibrated.every(({ lower }) => lower != null)
    ? calibrated.reduce((total, { lower }) => total * lower, 1) : null;
  const samples = calibrated.map(({ n }) => n).filter((n) => Number.isFinite(n) && n > 0);
  const result = {
    actual_odds: Number(actualOdds.toFixed(2)),
    probability_basis: "배당구간 실측 ROI 보정확률 · 95% Wilson 단측 하한",
  };
  if (marketHit != null) {
    result.hit_est = Number(marketHit.toFixed(5));
    result.upset_risk = Number((1 - marketHit).toFixed(5));
    result.expected_roi = Number((marketHit * actualOdds - 1).toFixed(4));
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

export function recommendationFromPlans(plans) {
  const available = (plans || []).filter((plan) => plan?.ok);
  if (!available.length) return { action: "none", target: null, index: -1,
    why: "오늘 23:59 KST까지 구성 가능한 조합이 없다" };
  const positive = available.filter((plan) => Number(plan.conservative_expected_roi) > 0);
  const pool = positive.length ? positive : available;
  const best = [...pool].sort((a, b) => {
    if (positive.length) {
      const growth = kellyGrowth(b) - kellyGrowth(a);
      if (growth) return growth;
    }
    return Number(b.conservative_expected_roi ?? -99) - Number(a.conservative_expected_roi ?? -99) ||
      Number(b.calibrated_hit_est ?? 0) - Number(a.calibrated_hit_est ?? 0);
  })[0];
  const index = available.findIndex((plan) => plan.target === best.target);
  return positive.length
    ? { action: "buy", target: best.target, index,
      why: "95% 보수 하한에서도 기대수익이 양수다" }
    : { action: "pass", target: best.target, index,
      why: "최선 조합도 95% 보수 기대수익이 0 이하라 자동 투입하지 않는다" };
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

function futureTodayCandidates(today, now) {
  if (!today) return [];
  const source = today.candidates?.length ? today.candidates : legacyCandidates(today);
  return eligibleAutoSelections(source).filter((candidate) => {
    const kickoff = kickoffTime(candidate, today.year);
    return Number.isFinite(kickoff) && kickoff > now && kstDay(kickoff) === kstDay(now);
  });
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
  const nextKickoff = futureTodayCandidates(today, now)
    .map((candidate) => kickoffTime(candidate, today.year))
    .sort((a, b) => a - b)[0];
  const wakeAt = Number.isFinite(nextKickoff) ? nextKickoff : nextKstMidnight(now);
  // 시작 시각과 같은 밀리초에 경계 판정이 흔들리지 않도록 1초 뒤에 갱신한다.
  return Math.min(waitLimit, Math.max(1000, wakeAt - now + 1000));
}

export function availableToday(today, now = Date.now()) {
  if (!today) return { plans: [], solo: null, candidates: [], next: null };
  const candidates = futureTodayCandidates(today, now)
    .sort((a, b) => byNextKickoff(a, b, today.year));

  const plans = (today.plans || []).map((plan) => {
    const bins = SAFE_TARGET_BINS[Number(plan.target)] ||
      (plan.bins?.length ? plan.bins : (plan.picks || []).map((pick) => pick.bin));
    const picks = pickNextLegs(candidates, bins, today.year, plan.target);
    if (!picks) {
      return { ...plan, ok: false,
        why: "시장 최유력·2.20 미만인 시작 전 경기만으로 조합할 수 없다" };
    }
    return {
      ...plan,
      ...ticketMetrics(picks),
      ok: true,
      bins,
      picks,
    };
  });

  const solo = candidates
    .filter((candidate) => candidate.bin === "1.0-1.3")
    .sort((a, b) => byLegQuality(a, b, today.year))[0] || null;
  const measuredSolo = solo ? { ...solo, ...ticketMetrics([solo]) } : null;

  return {
    ...today,
    plans,
    solo: measuredSolo,
    recommendation: recommendationFromPlans(plans),
    candidates,
    next: candidates[0] || null,
  };
}
