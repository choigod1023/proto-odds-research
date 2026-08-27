import assert from "node:assert/strict";
import { eligibleAutoSelections, MAX_AUTO_ODDS, MIN_AUTO_ODDS,
  PREFERRED_AUTO_ODDS, recommendationPriority } from "./recommendation-policy.js";
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
const reverse = choice("원정", 2.05, 0.42);
const high = { ...choice("홈", 2.2, 0.52), event_key: "game-b" };
const oddEven = { ...choice("홀", 1.7, 0.55, "홀짝"), event_key: "game-c" };
const tooLow = { ...choice("홈", 1.49, 0.68), event_key: "game-d" };

assert.equal(MIN_AUTO_ODDS, 1.5);
assert.equal(PREFERRED_AUTO_ODDS, 1.5);
assert.equal(MAX_AUTO_ODDS, 2.2);
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
