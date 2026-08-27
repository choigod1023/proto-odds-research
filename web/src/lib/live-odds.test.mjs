import assert from "node:assert/strict";
import test from "node:test";
import { priceBucket, repriceGameOdds, repricePriceGame, shinProbabilities } from "./live-odds.js";

test("Python 운영식과 같은 Shin 확률을 계산한다", () => {
  const probabilities = shinProbabilities([2.92, 1.26]);
  assert.ok(Math.abs(probabilities[0] - 0.2744074798866407) < 1e-10);
  assert.ok(Math.abs(probabilities[1] - 0.7255925201133593) < 1e-10);
  assert.ok(Math.abs(probabilities.reduce((sum, value) => sum + value, 0) - 1) < 1e-12);
});

test("실시간 배당과 시장확률·최종 판정을 한 revision으로 다시 만든다", () => {
  const game = {
    round: 101,
    추천: { "선택": "원정" },
    decision_snapshot: { schema_version: "decision-snapshot-v2" },
    options: [
      { market: "승패", label: "", "게임번호": "7010", "선택": "홈", "배당": 3.38,
        "시장확률": 0.2278, "모델확률": 0.31 },
      { market: "승패", label: "", "게임번호": "7010", "선택": "원정", "배당": 1.19,
        "시장확률": 0.7722, "모델확률": 0.69 },
    ],
  };
  const repriced = repriceGameOdds(game, { 7010: [2.92, 1.26] }, "2026-08-27T05:02:24Z");
  assert.equal(repriced._liveOddsRecalculated, true);
  assert.equal(repriced._liveOddsRecalculatedAt, "2026-08-27T05:02:24Z");
  assert.equal(repriced.decision_snapshot, null, "이전 가격의 판정 계약을 재사용하지 않는다");
  assert.equal(repriced.options[0]["시장확률"], 0.2744);
  assert.equal(repriced.options[1]["시장확률"], 0.7256);
  assert.equal(repriced.options[1]["최종확률"], 0.7256);
  assert.equal(repriced.options[1]["확률근거"], "shin_market_live");
  assert.equal(repriced.options[1]._live, true);
  assert.equal(game.options[1]["배당"], 1.19, "원본 산출물은 변경하지 않는다");
});

test("가격 비교 카드도 확률·환급률·구간을 즉시 다시 계산한다", () => {
  const game = {
    booking_class: "2-way",
    selections: [
      { name: "홈", odds: 3.38, prob: 0.2278, bucket: "3.0–5.0", hist_roi: -0.14, hist_n: 10 },
      { name: "원정", odds: 1.19, prob: 0.7722, bucket: "1.0–1.5", hist_roi: -0.10, hist_n: 20 },
    ],
  };
  const repriced = repricePriceGame(game, [2.92, 1.26], "2026-08-27T05:02:24Z");
  assert.equal(repriced._liveOddsRecalculated, true);
  assert.equal(repriced.payout, 88.02);
  assert.equal(repriced.selections[0].bucket, "2.2–3.0");
  assert.equal(repriced.selections[0].hist_roi, null, "구간이 바뀌면 이전 구간 실측을 숨긴다");
  assert.equal(repriced.selections[1].hist_roi, -0.10);
  assert.match(repriced.comment, /원정 73%/);
  assert.equal(priceBucket(2.92), "2.2–3.0");
});
