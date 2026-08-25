import assert from "node:assert/strict";
import { availableToday, kickoffTime, nextTodayRefreshDelay, pickNextLegs, SAFE_TARGET_BINS,
  challengeOptions, recommendationFromPlans, ticketMetrics } from "./today-plan.js";

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

const picked = pickNextLegs([sameA, sameB, nextA, nextB], ["1.5-1.8", "1.8-2.2"], 2026);
assert.equal(picked.length, 2);
assert.notEqual(picked[0].event_key, picked[1].event_key, "같은 실제 경기의 마켓을 묶으면 안 된다");

const today = {
  year: 2026,
  candidates: [past, sameA, sameB, nextA, nextB, high, reverse],
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
assert.equal(before.plans[0].ok, true);
assert.notEqual(before.plans[0].picks[0].event_key, before.plans[0].picks[1].event_key);

const after = availableToday(today, Date.parse("2026-08-19T07:01:00+09:00"));
assert.equal(after.candidates.some((candidate) => candidate.event_key === "same"), false);
assert.deepEqual(after.plans[0].picks.map((candidate) => candidate.event_key), ["next-a", "next-b"]);
assert.equal(after.plans[0].actual_odds, 3.38);
assert.equal(after.plans[0].hit_est, 0.264);
assert.equal(after.plans[0].upset_risk, 0.736);
assert.equal(after.plans[0].expected_roi, -0.107);

const exactKickoff = availableToday(today, Date.parse("2026-08-19T07:00:00+09:00"));
assert.equal(exactKickoff.candidates.some((candidate) => candidate.event_key === "same"), false,
  "KST 경기 시작 시각이 되면 즉시 후보에서 제외해야 한다");

const thirtyMinutesBefore = Date.parse("2026-08-19T06:30:00+09:00");
assert.equal(nextTodayRefreshDelay(today, thirtyMinutesBefore), 30 * 60 * 1000,
  "다음 시작이 멀어도 최대 30분 뒤에는 다시 확인해야 한다");
const tenSecondsBefore = Date.parse("2026-08-19T06:59:50+09:00");
assert.equal(nextTodayRefreshDelay(today, tenSecondsBefore), 11 * 1000,
  "다음 KST 경기 시작 직후 재추천하도록 예약해야 한다");

const cutoffToday = { year: 2026, candidates: [lastMinute, tomorrow], plans: [] };
const beforeLastMinute = Date.parse("2026-08-19T23:58:50+09:00");
const cutoffResult = availableToday(cutoffToday, beforeLastMinute);
assert.deepEqual(cutoffResult.candidates.map((candidate) => candidate.event_key), ["last-minute"],
  "오늘 23:59 KST 경기는 포함하고 다음 날 00:00 경기는 제외해야 한다");
assert.equal(nextTodayRefreshDelay(cutoffToday, beforeLastMinute), 11 * 1000);
const afterLastStart = Date.parse("2026-08-19T23:59:01+09:00");
assert.equal(availableToday(cutoffToday, afterLastStart).candidates.length, 0);
assert.equal(nextTodayRefreshDelay(cutoffToday, Date.parse("2026-08-19T23:59:50+09:00")), 11 * 1000,
  "오늘 후보가 없으면 KST 자정 직후 다시 확인해야 한다");

const metrics = ticketMetrics([nextA, nextB]);
assert.equal(metrics.actual_odds, 3.38);
assert.equal(metrics.hit_est, 0.264);
assert.equal(metrics.upset_risk, 0.736);
assert.equal(metrics.expected_roi, -0.107);
assert.equal(metrics.calibrated_expected_roi, -0.19);
assert.ok(metrics.conservative_expected_roi < -0.19);
assert.ok(metrics.conservative_hit_est < metrics.calibrated_hit_est);
assert.equal(metrics.calibration_min_n, 10_000);
assert.match(metrics.probability_basis, /Wilson/);

const pass = recommendationFromPlans([
  { ok: true, target: 2, actual_odds: 2, conservative_expected_roi: -0.05, calibrated_hit_est: 0.52 },
  { ok: true, target: 5, conservative_expected_roi: -0.12, calibrated_hit_est: 0.70 },
]);
assert.equal(pass.action, "challenge");
assert.equal(pass.target, 2);
const dailyChallenge = recommendationFromPlans([
  { ok: true, target: 1.4, actual_odds: 1.39, calibrated_hit_est: 0.607,
    conservative_hit_est: 0.593, conservative_expected_roi: -0.174 },
]);
assert.equal(dailyChallenge.action, "challenge");
assert.equal(dailyChallenge.target, 1.4);
assert.equal(dailyChallenge.budget_ratio, 0.1);
const tooRiskyForDailyChallenge = recommendationFromPlans([
  { ok: true, target: 1.4, calibrated_hit_est: 0.60,
    conservative_expected_roi: -0.201 },
]);
assert.equal(tooRiskyForDailyChallenge.action, "challenge");
const buy = recommendationFromPlans([{ ok: true, target: 3, actual_odds: 3,
  conservative_hit_est: 0.35, conservative_expected_roi: 0.05 }]);
assert.equal(buy.action, "buy");

const derivativePenalty = recommendationFromPlans([
  { ok: true, target: 2, actual_odds: 2, legs: 1, conservative_expected_roi: -0.03,
    calibrated_hit_est: 0.48, picks: [{ market: "핸디캡", market_label: "홈 -2.0" }] },
  { ok: true, target: 3, actual_odds: 2.9, legs: 2, conservative_expected_roi: -0.08,
    calibrated_hit_est: 0.36, picks: [{ market: "승패" }, { market: "언더오버" }] },
]);
assert.equal(derivativePenalty.target, 3);

const challengePlans = [
  { ok: true, target: 1.4, actual_odds: 1.4, calibrated_hit_est: 0.70,
    conservative_expected_roi: -0.12 },
  { ok: true, target: 2, actual_odds: 2, calibrated_hit_est: 0.45,
    conservative_expected_roi: -0.15 },
  { ok: true, target: 3, actual_odds: 3, calibrated_hit_est: 0.30,
    conservative_expected_roi: -0.18 },
  { ok: true, target: 5, actual_odds: 5, calibrated_hit_est: 0.18,
    conservative_expected_roi: -0.25 },
];
const challenges = challengeOptions(challengePlans, 10_000, 3);
assert.deepEqual(challenges.map((option) => option.stake), [1000, 3000, 5000, 10000]);
assert.deepEqual(challenges.map((option) => option.target), [3, 3, 3, 3],
  "투입 금액과 무관하게 사용자가 고른 도전 강도를 유지해야 한다");
assert.deepEqual(challenges.map((option) => option.net_profit), [2000, 6000, 10000, 20000]);
assert.deepEqual(challenges.map((option) => option.conservative_loss), [180, 540, 900, 1800]);
assert.ok(challenges.every((option) => option.requested_target === 3));

const doubleChallenges = challengeOptions(challengePlans, 10_000, 2);
assert.deepEqual(doubleChallenges.map((option) => option.target), [2, 2, 2, 2]);
const fivefoldChallenges = challengeOptions(challengePlans, 10_000, 5);
assert.deepEqual(fivefoldChallenges.map((option) => option.target), [5, 5, 5, 5]);
assert.equal(fivefoldChallenges.at(-1).net_profit, 40_000,
  "만원 전액도 사용자가 선택한 5배 도전에 투입할 수 있어야 한다");
assert.deepEqual(challengeOptions(challengePlans, 10_000).map((option) => option.target),
  [3, 3, 3, 3], "기본 도전 강도는 3배여야 한다");

const fallbackChallenge = challengeOptions([
  { ok: true, target: 1.4, actual_odds: 1.2, calibrated_hit_est: 0.8,
    conservative_expected_roi: -0.1 },
], 10_000);
assert.equal(fallbackChallenge[0].target, 1.4);
assert.equal(fallbackChallenge[0].requested_target, 3);
assert.deepEqual(challengeOptions([], 10_000), []);

assert.equal(SAFE_TARGET_BINS[5].length, 3);
assert.equal(SAFE_TARGET_BINS[8].length, 3);
assert.equal(SAFE_TARGET_BINS[12].length, 4);

assert.equal(kickoffTime({ date: "08.19(수) 07:00" }, 2026), Date.parse("2026-08-19T07:00:00+09:00"));
console.log("today-plan schedule tests passed");
