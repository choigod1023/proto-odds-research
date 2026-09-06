import { eligibleFinalSelections, recommendationPriority } from "./recommendation-policy.js";

// Risk limits, not fitted probabilities or a claim of profitable performance.
export const POLICY_VERSION = "daily-value-v1";
export const MIN_HIT = 0.50;
export const MIN_RETURN = -0.15;
export const BASE_PER_LEAGUE = 3;

const number = (value) => {
  if (typeof value !== "number" && (typeof value !== "string" ||
      !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim()))) return null;
  return Number.isFinite(Number(value)) ? Number(value) : null;
};
const probability = (value) => {
  const p = number(value);
  return p != null && p > 0 && p < 1 ? p : null;
};

/** Consume only normalized, current-revision probability metadata from the pipeline. */
export function dailyValueMetrics(candidate) {
  const invalid = {
    policy_version: POLICY_VERSION, probability: null, comparison_probability: null,
    break_even_probability: null, expected_return: null, comparison_return: null,
    validated_probability: false, validated_interval: false, qualifies: false,
  };
  const odds = number(candidate?.odds ?? candidate?.["배당"]);
  const market = probability(candidate?.market_prob ?? candidate?.["시장확률"]);
  if (odds == null || odds <= 1 || market == null) return invalid;
  const final = probability(candidate?.predicted_hit_prob);
  const validated = candidate?.decision_pipeline_applied === true &&
    candidate?.has_validated_edge === true && final != null;
  const estimate = validated ? final : market;
  const interval = candidate?.probability_interval;
  const lower = probability(candidate?.probability_lower_bound);
  const lo = Array.isArray(interval) && interval.length === 2 ? probability(interval[0]) : null;
  const hi = Array.isArray(interval) && interval.length === 2 ? probability(interval[1]) : null;
  const hasInterval = validated && candidate?.validated_uncertainty_available === true &&
    candidate?.uncertainty_source === "validated_residual_interval" &&
    lower != null && lo != null && hi != null && lo <= estimate && estimate <= hi &&
    // today_combo serializes the stated lower to four decimal places.
    Math.abs(lower - lo) <= 5.0001e-5;
  const comparison = hasInterval ? lo : Math.min(market, estimate);
  const expectedReturn = estimate * odds - 1;
  const comparisonReturn = comparison * odds - 1;
  return {
    policy_version: POLICY_VERSION, probability: estimate, comparison_probability: comparison,
    break_even_probability: 1 / odds, expected_return: expectedReturn,
    comparison_return: comparisonReturn, validated_probability: validated,
    validated_interval: hasInterval,
    qualifies: estimate >= MIN_HIT && comparisonReturn >= MIN_RETURN - 1e-12,
  };
}

const clean = (value) => String(value ?? "").trim();
const selectionKey = (row) => [row?.round, row?.game_no ?? row?.["게임번호"],
  row?.market, row?.market_label ?? row?.label ?? "", row?.sel ?? row?.["선택"]]
  .map(clean).join("|");
const lexical = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const dateParts = (row) => {
  let kickoffText = clean(row?.kickoff_at);
  // Legacy naive timestamps mean KST, regardless of the browser's time zone.
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(kickoffText)) {
    kickoffText = `${kickoffText.replace(" ", "T")}+09:00`;
  }
  const kickoff = Date.parse(kickoffText);
  const date = clean(row?.date).match(/^(\d{2})\.(\d{2})/);
  const iso = Number.isFinite(kickoff)
    ? new Date(kickoff + 9 * 3600000).toISOString().slice(0, 10)
    : null;
  return { year: iso?.slice(0, 4) || clean(row?.year),
    monthDay: iso?.slice(5) || (date ? `${date[1]}-${date[2]}` : null) };
};
const leagueKey = (row, knownYears) => {
  const { year, monthDay } = dateParts(row);
  const known = knownYears.get(monthDay);
  const inferredYear = known?.size === 1 ? [...known][0] : "undated";
  const day = monthDay ? `${year || inferredYear}-${monthDay}` : "undated";
  return `${day}|${clean(row?.league) || "리그 미분류"}`;
};

export function compareDailyValue(a, b) {
  const left = dailyValueMetrics(a);
  const right = dailyValueMetrics(b);
  return (right.comparison_return ?? -Infinity) - (left.comparison_return ?? -Infinity) ||
    (right.expected_return ?? -Infinity) - (left.expected_return ?? -Infinity) ||
    (right.probability ?? -Infinity) - (left.probability ?? -Infinity) ||
    lexical(clean(a?.kickoff_at || a?.date), clean(b?.kickoff_at || b?.date)) ||
    lexical(selectionKey(a), selectionKey(b));
}

/** Same policy is persisted with the DB artifact by src/daily_value.py. */
export function dailyValueDecisions(candidates = []) {
  // Old candidates may have a date but no year; don't give the same KST day
  // two league quotas just because another candidate has an ISO timestamp.
  const knownYears = new Map();
  candidates.forEach((row) => {
    const { year, monthDay } = dateParts(row);
    if (!year || !monthDay) return;
    if (!knownYears.has(monthDay)) knownYears.set(monthDay, new Set());
    knownYears.get(monthDay).add(year);
  });
  const eligible = new Set(eligibleFinalSelections(candidates));
  const rows = candidates.map((selection) => {
    const metrics = dailyValueMetrics(selection);
    const odds = number(selection?.odds ?? selection?.["배당"]);
    const reason = !eligible.has(selection) || odds == null || odds <= 1 ? "safety"
      : metrics.probability == null ? "invalid"
        : metrics.probability < MIN_HIT ? "hit_floor"
          : !metrics.qualifies ? "return_floor" : "rank";
    return { selection, ...metrics, recommended: false, league_rank: null, reason_code: reason };
  });
  const groups = new Map();
  rows.filter((row) => row.reason_code === "rank").forEach((row) => {
    const key = leagueKey(row.selection, knownYears);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  groups.forEach((group) => {
    const primary = group.filter((row) => recommendationPriority(row.selection) === 1);
    const pool = primary.length ? primary : group;
    if (primary.length) group.filter((row) => !primary.includes(row))
      .forEach((row) => { row.reason_code = "fallback"; });
    pool.sort((a, b) => compareDailyValue(a.selection, b.selection)).forEach((row, index) => {
      row.league_rank = index + 1;
      const extra = row.validated_interval && row.comparison_return > 0;
      row.recommended = index < BASE_PER_LEAGUE || extra;
      row.reason_code = index < BASE_PER_LEAGUE ? "base" : extra ? "validated_extra" : "rank";
    });
  });
  return rows;
}
