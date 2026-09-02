import assert from "node:assert/strict";
import { eligibleAutoSelections, eligibleFinalSelections, finalRecommendedSelection,
  hitProbabilityOf, MAX_AUTO_ODDS, MIN_AUTO_ODDS, PREFERRED_AUTO_ODDS,
  qualifiedUnderdogSelections, recommendationPriority,
  UPSET_MAX_MODEL_GAP, UPSET_MAX_MODEL_PROBABILITY, UPSET_MAX_ODDS,
  UPSET_MIN_MARKET_PROBABILITY, UPSET_MIN_MODEL_GAP, UPSET_MIN_MODEL_PROBABILITY,
  UPSET_MIN_ODDS } from "./recommendation-policy.js";
import { lessBadPick } from "./fmt.js";

const choice = (sel, odds, probability, market = "승패") => ({
  event_key: "game-a",
  market,
  market_label: "",
  sel,
  odds,
  market_prob: probability,
});

const favorite = choice("홈", 1.65, 0.58);
const reverse = { ...choice("원정", 2.05, 0.42), model_prob: 0.55 };
const high = { ...choice("홈", 2.2, 0.52), event_key: "game-b" };
const oddEven = { ...choice("홀", 1.7, 0.55, "홀짝"), event_key: "game-c" };
const tooLow = { ...choice("홈", 1.49, 0.68), event_key: "game-d" };

assert.equal(MIN_AUTO_ODDS, 1.5);
assert.equal(PREFERRED_AUTO_ODDS, 1.5);
assert.equal(MAX_AUTO_ODDS, 2.2);
assert.deepEqual(
  [UPSET_MIN_ODDS, UPSET_MAX_ODDS, UPSET_MIN_MARKET_PROBABILITY,
    UPSET_MIN_MODEL_PROBABILITY, UPSET_MAX_MODEL_PROBABILITY,
    UPSET_MIN_MODEL_GAP, UPSET_MAX_MODEL_GAP],
  [1.5, 3.0, 0.28, 0.50, 0.75, 0.08, 0.25],
);
assert.deepEqual(eligibleAutoSelections([favorite, reverse, high, oddEven, tooLow]), [favorite, tooLow]);
assert.equal(recommendationPriority(favorite), 1);
assert.equal(recommendationPriority(tooLow), 0);
assert.equal(eligibleAutoSelections([{ ...tooLow, odds: 1.5 }]).length, 1,
  "배당 1.50은 1순위 경계에 포함한다");
assert.deepEqual(
  eligibleAutoSelections([{ ...favorite, is_market_favorite: false }]),
  [],
  "생성기가 역배로 표시한 선택지는 단독으로 남아도 추천하면 안 된다",
);
assert.deepEqual(qualifiedUnderdogSelections([favorite, reverse]), [reverse],
  "중간 배당·비극단 모델 괴리 역배만 전환 후보로 남긴다");
assert.equal(finalRecommendedSelection([favorite, reverse]), favorite,
  "이변 후보는 관찰만 하고 운영 선택은 시장 최유력을 유지한다");
assert.equal(finalRecommendedSelection([tooLow, favorite]), favorite,
  "1.50 이상 후보가 있으면 저배당 고확률 후보보다 먼저 쓴다");
assert.equal(finalRecommendedSelection([tooLow]), tooLow,
  "1.50 이상 후보가 없을 때는 저배당 최유력을 보조 추천으로 남긴다");
const validatedLower = { ...favorite, event_key: "game-e", market_prob: 0.62,
  predicted_hit_prob: 0.57, has_validated_edge: true };
const validatedHigher = { ...favorite, event_key: "game-f", odds: 1.70,
  market_prob: 0.58, predicted_hit_prob: 0.65, has_validated_edge: true };
assert.equal(hitProbabilityOf(validatedHigher), 0.65);
assert.equal(finalRecommendedSelection([validatedLower, validatedHigher]), validatedHigher,
  "검증 보정 최종 적중확률이 원시 시장확률보다 추천 정렬에 우선한다");
assert.deepEqual(eligibleFinalSelections([
  { ...reverse, is_market_favorite: false, final_reversal: true },
]), []);
assert.deepEqual(qualifiedUnderdogSelections([
  favorite,
  { ...reverse, odds: 3.0 },
  { ...reverse, model_prob: 0.95 },
  { ...reverse, model_prob: 0.48 },
]), [], "극단 배당·과신 모델·괴리 부족 역배는 제외한다");

const grades = {
  odds_bins: [
    { bin: "1.5-1.8", roi: -0.10, hit: 0.58, grade: "B" },
    { bin: "1.8-2.2", roi: -0.13, hit: 0.42, grade: "C" },
  ],
  market_bins: [
    { fam: "승패", bin: "1.5-1.8", stable: true, roi: -0.10, hit: 0.58 },
    // 일부 과거 셀의 점추정 ROI가 좋아도 시장의 반대편이면 자동 추천하지 않는다.
    { fam: "승패", bin: "1.8-2.2", stable: true, roi: 0.20, hit: 0.42 },
  ],
};
const cardOptions = [
  { market: "승패", label: "", line: null, n_way: 2,
    "선택": "홈", "배당": 1.65, "시장확률": 0.58 },
  { market: "승패", label: "", line: null, n_way: 2,
    "선택": "원정", "배당": 2.05, "시장확률": 0.42 },
];
assert.equal(lessBadPick(grades, cardOptions, "roi").o["선택"], "홈");

console.log("recommendation policy tests passed");
