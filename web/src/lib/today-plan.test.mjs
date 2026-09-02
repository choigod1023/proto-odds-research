import assert from "node:assert/strict";
import { availableToday, kickoffTime, nextTodayRefreshDelay, pickNextLegs,
  challengeOptions, planSignature, recommendationFromPlans,
  ticketIndexForRecommendation, ticketMetrics } from "./today-plan.js";

const leg = (event, kickoff, bin, odds, gameNo, marketProb = 0.5) => ({
  event_key: event,
  kickoff_at: kickoff,
  date: "08.19(수) 07:00",
  match: event,
  market: "승패",
  market_label: "",
  sel: "홈",
  bin,
  odds,
  overround: 1.12,
  game_no: gameNo,
  market_prob: marketProb,
  hist_roi: -0.1,
  hist_n: 10_000,
});

const past = leg("past", "2026-08-19T06:00:00+09:00", "1.5-1.8", 1.6, "1", 0.58);
const sameA = leg("same", "2026-08-19T07:00:00+09:00", "1.5-1.8", 1.6, "2", 0.55);
const sameB = leg("same", "2026-08-19T07:00:00+09:00", "1.8-2.2", 2.0, "3", 0.45);
const nextA = leg("next-a", "2026-08-19T07:05:00+09:00", "1.5-1.8", 1.65, "4", 0.60);
const nextB = leg("next-b", "2026-08-19T07:10:00+09:00", "1.8-2.2", 2.05, "5", 0.44);
const high = leg("high", "2026-08-19T07:20:00+09:00", "2.2-3.0", 2.4, "6", 0.40);
const reverse = { ...leg("reverse", "2026-08-19T07:30:00+09:00", "1.8-2.2", 1.95, "7", 0.46),
  is_market_favorite: false };
const lastMinute = leg("last-minute", "2026-08-19T23:59:00+09:00", "1.5-1.8", 1.7, "8", 0.56);
const tomorrow = leg("tomorrow", "2026-08-20T00:00:00+09:00", "1.8-2.2", 2.0, "9", 0.48);
const tomorrowMorning = leg("tomorrow-morning", "2026-08-20T09:00:00+09:00", "1.5-1.8", 1.7, "10", 0.56);
const tomorrowNoon = leg("tomorrow-noon", "2026-08-20T12:00:00+09:00", "1.5-1.8", 1.7, "11", 0.56);

const picked = pickNextLegs([sameA, sameB, nextA, nextB], ["1.5-1.8", "1.8-2.2"], 2026);
assert.equal(picked.length, 2);
assert.notEqual(picked[0].event_key, picked[1].event_key, "같은 실제 경기의 마켓을 묶으면 안 된다");

const today = {
  year: 2026,
  candidates: [past, sameA, sameB, nextA, nextB, high, reverse],
  evolutionary_selector: {
    status: "shadow_only",
    profiles: {
      balanced: {
        historical_status: "promising_but_unproven",
        rule: {
          genome: { confidence: 1 },
          constraints: { odds_min: 1.4, odds_max: 1.85, target_odds: 1.58 },
        },
      },
    },
  },
  plans: [{
    target: 3.2,
    ok: true,
    bins: ["1.5-1.8", "1.8-2.2"],
    expected_roi: -0.2,
    hit_est: 0.3,
    picks: [past, sameB],
  }],
};

const before = availableToday(today, Date.parse("2026-08-19T06:59:00+09:00"));
assert.equal(before.candidates.some((candidate) => candidate.event_key === "past"), false);
assert.equal(before.candidates.some((candidate) => candidate.event_key === "high"), false);
assert.equal(before.candidates.some((candidate) => candidate.event_key === "reverse"), false);
assert.equal(before.next.event_key, "same");
assert.deepEqual(before.plans, []);
assert.equal(before.solo, null);
assert.equal(before.recommendation.action, "disabled");
assert.equal(before.evolutionary_selector.profiles.balanced.selected.event_key, "next-a");

const after = availableToday(today, Date.parse("2026-08-19T07:01:00+09:00"));
assert.equal(after.candidates.some((candidate) => candidate.event_key === "same"), false);
assert.deepEqual(after.plans, []);
assert.equal(after.solo, null);
assert.equal(after.evolutionary_selector.profiles.balanced.selected.event_key, "next-a");

const exactKickoff = availableToday(today, Date.parse("2026-08-19T07:00:00+09:00"));
assert.equal(exactKickoff.candidates.some((candidate) => candidate.event_key === "same"), false,
  "KST 경기 시작 시각이 되면 즉시 후보에서 제외해야 한다");

const thirtyMinutesBefore = Date.parse("2026-08-19T06:30:00+09:00");
assert.equal(nextTodayRefreshDelay(today, thirtyMinutesBefore), 30 * 60 * 1000,
  "다음 시작이 멀어도 최대 30분 뒤에는 다시 확인해야 한다");
const tenSecondsBefore = Date.parse("2026-08-19T06:59:50+09:00");
assert.equal(nextTodayRefreshDelay(today, tenSecondsBefore), 11 * 1000,
  "다음 KST 경기 시작 직후 재추천하도록 예약해야 한다");

const cutoffToday = { year: 2026, candidates: [lastMinute, tomorrow, tomorrowMorning, tomorrowNoon], plans: [] };
const beforeLastMinute = Date.parse("2026-08-19T23:58:50+09:00");
const cutoffResult = availableToday(cutoffToday, beforeLastMinute);
assert.deepEqual(cutoffResult.candidates.map((candidate) => candidate.event_key), ["last-minute"],
  "오늘 후보가 남아 있으면 다음 날 오전 경기를 섞지 않아야 한다");
assert.equal(nextTodayRefreshDelay(cutoffToday, beforeLastMinute), 11 * 1000);
const afterLastStart = Date.parse("2026-08-19T23:59:01+09:00");
const morningFallback = availableToday(cutoffToday, afterLastStart);
assert.equal(morningFallback.window, "next_morning");
assert.deepEqual(morningFallback.candidates.map((candidate) => candidate.event_key),
  ["tomorrow", "tomorrow-morning"],
  "오늘 후보가 소진되면 다음 날 00:00~11:59 경기만 사용해야 한다");
assert.equal(nextTodayRefreshDelay(cutoffToday, Date.parse("2026-08-19T23:59:50+09:00")), 11 * 1000,
  "다음 날 첫 경기 시작 직후 다시 확인해야 한다");

const metrics = ticketMetrics([nextA, nextB]);
assert.equal(metrics.actual_odds, 3.38);
assert.equal(metrics.hit_est, 0.264);
assert.equal(metrics.upset_risk, 0.736);
assert.equal(metrics.expected_roi, -0.107);
assert.equal(metrics.calibrated_expected_roi, metrics.expected_roi);
assert.equal(metrics.independent_lower_hit_est, 0.264);
assert.equal(metrics.correlation_stress_hit_est, 0.25184);
assert.equal(metrics.correlation_sensitivity, 0.01216);
assert.equal(metrics.frechet_lower_hit_bound, 0.04);
assert.equal(metrics.conservative_hit_est, metrics.correlation_stress_hit_est);
assert.equal(metrics.conservative_expected_roi, -0.1481);
assert.equal(metrics.independence_is_certainty, false);
assert.equal(metrics.calibration_min_n, null);
assert.match(metrics.probability_basis, /Shin/);

const validatedMetrics = ticketMetrics([
  { ...nextA, predicted_hit_prob: 0.68, probability_lower_bound: 0.58,
    decision_pipeline_applied: true, has_validated_edge: true,
    policy_authorized: false, validated_uncertainty_available: true,
    uncertainty_source: "validated_residual_interval" },
  { ...nextB, predicted_hit_prob: 0.60, probability_lower_bound: 0.52,
    decision_pipeline_applied: true, has_validated_edge: true,
    policy_authorized: false, validated_uncertainty_available: true,
    uncertainty_source: "validated_residual_interval" },
]);
assert.equal(validatedMetrics.independent_hit_est, 0.408);
assert.equal(validatedMetrics.independent_lower_hit_est, 0.3016);
assert.equal(validatedMetrics.has_validated_edge, true);
assert.equal(validatedMetrics.validated_uncertainty_available, true);
assert.equal(validatedMetrics.probability_source, "validated_final_probability");
assert.equal(validatedMetrics.conservative_probability_source,
  "validated_interval_correlation_stress");

const policyMetrics = ticketMetrics([{
  ...nextA, predicted_hit_prob: 0.70, probability_lower_bound: 0.60,
  decision_pipeline_applied: true, has_validated_edge: false,
  policy_authorized: true, validated_uncertainty_available: false,
  uncertainty_source: "shin_market_fallback",
}]);
assert.equal(policyMetrics.independent_hit_est, 0.60,
  "정책 승인 스냅샷의 예전 최종확률은 시장확률로 fail-close해야 한다");
assert.equal(policyMetrics.conservative_hit_est, 0.60);
assert.equal(policyMetrics.has_validated_edge, false);
assert.equal(policyMetrics.has_policy_authorized_probability, false);
assert.equal(policyMetrics.has_policy_authorized_shadow, true);
assert.equal(policyMetrics.probability_source, "shin_market_fallback");
assert.equal(policyMetrics.conservative_probability_source,
  "shin_market_fallback_correlation_stress");

const pass = recommendationFromPlans([
  { ok: true, target: 3, conservative_expected_roi: -0.05, calibrated_hit_est: 0.269 },
  { ok: true, target: 5, conservative_expected_roi: -0.12, calibrated_hit_est: 0.70 },
]);
assert.equal(pass.action, "pass");
assert.equal(pass.target, 3);
const dailyChallenge = recommendationFromPlans([
  { ok: true, target: 3, actual_odds: 2.89, calibrated_hit_est: 0.282,
    conservative_hit_est: 0.277, correlation_stress_hit_est: 0.277,
    market_reference_roi: -0.50, correlation_stress_expected_roi: -0.1995,
    conservative_expected_roi: -0.1995 },
]);
assert.equal(dailyChallenge.action, "challenge");
assert.equal(dailyChallenge.target, 3);
assert.equal(dailyChallenge.budget_ratio, 0.1);
const historicalOnly = recommendationFromPlans([
  { ok: true, target: 3, calibrated_hit_est: 0.28,
    market_reference_roi: -0.25, historical_expected_roi: -0.18 },
]);
assert.equal(historicalOnly.action, "pass",
  "과거 손실표만 좋아도 최종 예상 적중 기준을 대신하지 않는다");
const tooRiskyForDailyChallenge = recommendationFromPlans([
  { ok: true, target: 3, calibrated_hit_est: 0.30,
    correlation_stress_hit_est: 0.28, market_reference_roi: -0.10,
    correlation_stress_expected_roi: -0.206, conservative_expected_roi: -0.206 },
]);
assert.equal(tooRiskyForDailyChallenge.action, "pass");
const malformedChallenge = recommendationFromPlans([
  { ok: true, target: 3, calibrated_hit_est: 0.30,
    correlation_stress_hit_est: 0.30, correlation_stress_expected_roi: null,
    conservative_expected_roi: null },
]);
assert.equal(malformedChallenge.action, "pass",
  "누락된 손실지표를 0으로 바꿔 소액 도전으로 승격하면 안 된다");
const buy = recommendationFromPlans([{ ok: true, target: 3, actual_odds: 3,
  conservative_hit_est: 0.35, conservative_expected_roi: 0.05,
  market_reference_roi: -0.15, has_validated_edge: true,
  validated_uncertainty_available: true }]);
assert.equal(buy.action, "buy");
const policyOnlyPositive = recommendationFromPlans([{ ok: true, target: 3, actual_odds: 3,
  conservative_hit_est: 0.38, conservative_expected_roi: 0.14,
  correlation_stress_hit_est: 0.26, market_reference_roi: -0.15,
  has_validated_edge: false, has_policy_authorized_probability: true }]);
assert.notEqual(policyOnlyPositive.action, "buy",
  "정책 승인 확률을 검증 우위로 세탁해 구매하면 안 된다");
const validatedWithoutInterval = recommendationFromPlans([{ ok: true, target: 3, actual_odds: 3,
  conservative_hit_est: 0.38, conservative_expected_roi: 0.14,
  correlation_stress_hit_est: 0.26, market_reference_roi: -0.15,
  has_validated_edge: true, validated_uncertainty_available: false }]);
assert.notEqual(validatedWithoutInterval.action, "buy",
  "검증된 불확실성 하한이 없으면 시장 복귀값으로 구매를 승격하면 안 된다");
const independenceOnlyChallenge = recommendationFromPlans([{ ok: true, target: 3,
  independent_hit_est: 0.30, correlation_stress_hit_est: 0.26,
  market_reference_roi: -0.18, correlation_stress_expected_roi: -0.22,
  conservative_expected_roi: -0.22 }]);
assert.equal(independenceOnlyChallenge.action, "pass",
  "독립 곱만 27%를 넘고 상관 스트레스가 못 넘으면 도전으로 올리지 않는다");
const passByStress = recommendationFromPlans([
  { ok: true, target: 5, market_reference_roi: -0.10,
    correlation_stress_expected_roi: -0.30, correlation_stress_hit_est: 0.18 },
  { ok: true, target: 8, market_reference_roi: -0.20,
    correlation_stress_expected_roi: -0.25, correlation_stress_hit_est: 0.20 },
]);
assert.equal(passByStress.action, "pass");
assert.equal(passByStress.target, 8,
  "패스 상태의 대표 조합도 서버와 같은 상관 스트레스 순서를 써야 한다");

const sameTargetPlans = [
  { ok: true, target: 3, actual_odds: 3, correlation_stress_hit_est: 0.31,
    correlation_stress_expected_roi: -0.205 },
  { ok: true, target: 3, actual_odds: 3, correlation_stress_hit_est: 0.28,
    correlation_stress_expected_roi: -0.17 },
];
const sameTargetRecommendation = recommendationFromPlans(sameTargetPlans);
assert.equal(sameTargetRecommendation.index, 1,
  "같은 target의 첫 plan이 아니라 실제로 판정 근거가 된 plan index를 반환해야 한다");
assert.equal(ticketIndexForRecommendation(sameTargetRecommendation, sameTargetPlans), 1);
const unavailableBeforeBest = [{ ok: false, target: 2 }, ...sameTargetPlans.slice(1)];
const shiftedRecommendation = recommendationFromPlans(unavailableBeforeBest);
assert.equal(shiftedRecommendation.index, 1,
  "ok plan만 필터링한 배열 index가 아니라 원본 plans index를 유지해야 한다");
assert.equal(ticketIndexForRecommendation(shiftedRecommendation, unavailableBeforeBest), 1);

const challengePlans = [
  { ok: true, target: 1.4, actual_odds: 1.4, correlation_stress_hit_est: 0.63,
    correlation_stress_expected_roi: -0.12, conservative_expected_roi: -0.12 },
  { ok: true, target: 2, actual_odds: 2, correlation_stress_hit_est: 0.425,
    correlation_stress_expected_roi: -0.15, conservative_expected_roi: -0.15 },
  { ok: true, target: 3, actual_odds: 3, correlation_stress_hit_est: 0.27333,
    correlation_stress_expected_roi: -0.18, conservative_expected_roi: -0.18,
    market_reference_roi: -0.12 },
  { ok: true, target: 5, actual_odds: 5, correlation_stress_hit_est: 0.15,
    correlation_stress_expected_roi: -0.25, conservative_expected_roi: -0.25 },
];
const challenges = challengeOptions(challengePlans, 10_000, 3);
assert.deepEqual(challenges.map((option) => option.stake), [1000, 3000, 5000, 10000]);
assert.deepEqual(challenges.map((option) => option.target), [3, 3, 3, 3],
  "투입 금액과 무관하게 사용자가 고른 도전 강도를 유지해야 한다");
assert.deepEqual(challenges.map((option) => option.net_profit), [2000, 6000, 10000, 20000]);
assert.deepEqual(challenges.map((option) => option.conservative_loss), [180, 540, 900, 1800]);
assert.ok(challenges.every((option) => option.correlation_stress_expected_roi === -0.18));
assert.ok(challenges.every((option) => option.market_reference_roi === -0.12),
  "상관 스트레스 ROI를 시장 ROI라는 이름으로 다시 노출하면 안 된다");
assert.ok(challenges.every((option) => option.requested_target === 3));

const exactPlanChallenges = challengeOptions(sameTargetPlans, 10_000, 3);
assert.ok(exactPlanChallenges.every((option) => option.plan_index === 1),
  "수동 구매안도 자동 판정과 같은 ROI 우선 plan을 가리켜야 한다");
assert.ok(exactPlanChallenges.every((option) => option.market_reference_roi === null),
  "누락된 시장 기준 ROI를 Number(null)의 0으로 표시하면 안 된다");

const signedPlan = {
  target: 3,
  actual_odds: 3,
  input_revision_hash: "revision-a",
  picks: [{
    event_key: "event-a", round: 7, game_no: "10", market: "승패",
    market_label: "", selection_id: "sel-home", offer_id: "offer-a", sel: "홈",
    odds: 1.65, market_prob: 0.60, decision_id: "decision-a",
  }],
};
const originalSignature = planSignature(signedPlan);
assert.notEqual(planSignature({ ...signedPlan, input_revision_hash: "revision-b" }), originalSignature);
assert.notEqual(planSignature({ ...signedPlan, picks: [{
  ...signedPlan.picks[0], offer_id: "offer-b",
}] }), originalSignature);
assert.notEqual(planSignature({ ...signedPlan, picks: [{
  ...signedPlan.picks[0], selection_id: "sel-away", sel: "원정",
}] }), originalSignature,
"같은 경기라도 revision·offer·선택 방향이 바뀌면 저장 stake 서명이 달라야 한다");

const doubleChallenges = challengeOptions(challengePlans, 10_000, 2);
assert.deepEqual(doubleChallenges.map((option) => option.target), [2, 2, 2, 2]);
const fivefoldChallenges = challengeOptions(challengePlans, 10_000, 5);
assert.deepEqual(fivefoldChallenges.map((option) => option.target), [5, 5, 5, 5]);
assert.equal(fivefoldChallenges.at(-1).net_profit, 40_000,
  "만원 전액도 사용자가 선택한 5배 도전에 투입할 수 있어야 한다");
assert.deepEqual(challengeOptions(challengePlans, 10_000).map((option) => option.target),
  [3, 3, 3, 3], "기본 도전 강도는 3배여야 한다");

const fallbackChallenge = challengeOptions([
  { ok: true, target: 1.4, actual_odds: 1.2, market_reference_hit_est: 0.75,
    market_reference_roi: -0.1 },
], 10_000);
assert.deepEqual(fallbackChallenge, [],
  "상관 스트레스가 없는 구형 시장값을 스트레스 지표로 이름만 바꾸면 안 된다");
assert.deepEqual(challengeOptions([], 10_000), []);

assert.equal(kickoffTime({ date: "08.19(수) 07:00" }, 2026), Date.parse("2026-08-19T07:00:00+09:00"));
assert.equal(kickoffTime({ date: "12.31(목) 21:30", round: 1 }, 2026),
  Date.parse("2025-12-31T21:30:00+09:00"),
  "발매연도 1회차의 12월 31일은 이전 달력연도로 해석해야 한다");
console.log("today-plan schedule tests passed");
