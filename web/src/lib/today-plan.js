import { eligibleAutoSelections } from "./recommendation-policy.js";

const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
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
  const aProbability = Number(a?.market_prob);
  const bProbability = Number(b?.market_prob);
  const probabilityOrder =
    (Number.isFinite(bProbability) ? bProbability : 0) -
    (Number.isFinite(aProbability) ? aProbability : 0);
  return probabilityOrder ||
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
      const score = [hitEstimate, payout, closeness];
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
  const probabilities = picks.map((pick) => Number(pick.market_prob));
  const hasProbabilities = probabilities.every(
    (probability) => Number.isFinite(probability) && probability > 0 && probability < 1,
  );
  if (!hasProbabilities) return { actual_odds: Number(actualOdds.toFixed(2)) };
  const hitEstimate = probabilities.reduce((total, probability) => total * probability, 1);
  return {
    actual_odds: Number(actualOdds.toFixed(2)),
    hit_est: Number(hitEstimate.toFixed(5)),
    upset_risk: Number((1 - hitEstimate).toFixed(5)),
    expected_roi: Number((hitEstimate * actualOdds - 1).toFixed(4)),
    probability_basis: "선택 경기의 Shin 시장확률 곱",
  };
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
  const source = today.candidates?.length ? today.candidates : legacyCandidates(today);
  const nextKickoff = source
    .map((candidate) => kickoffTime(candidate, today.year))
    .filter((kickoff) => Number.isFinite(kickoff) && kickoff > now)
    .sort((a, b) => a - b)[0];
  if (!Number.isFinite(nextKickoff)) return waitLimit;
  // 시작 시각과 같은 밀리초에 경계 판정이 흔들리지 않도록 1초 뒤에 갱신한다.
  return Math.min(waitLimit, Math.max(1000, nextKickoff - now + 1000));
}

export function availableToday(today, now = Date.now()) {
  if (!today) return { plans: [], solo: null, candidates: [], next: null };
  const source = today.candidates?.length ? today.candidates : legacyCandidates(today);
  const candidates = eligibleAutoSelections(source)
    .filter((candidate) => kickoffTime(candidate, today.year) > now)
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

  return {
    ...today,
    plans,
    solo,
    candidates,
    next: candidates[0] || null,
  };
}
