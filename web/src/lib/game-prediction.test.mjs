import test from "node:test";
import assert from "node:assert/strict";
import { predictionForGame, predictionStrengthLabel } from "./game-prediction.js";

const option = (selection, odds, probability, market = "승패") => ({
  "선택": selection, "배당": odds, "시장확률": probability, market,
});

test("안전 추천이 없어도 경기의 시장 최유력 픽을 관망으로 남긴다", () => {
  const prediction = predictionForGame([
    option("홈", 2.3, 0.45), option("원정", 2.4, 0.42),
  ]);
  assert.equal(prediction.option["선택"], "홈");
  assert.equal(prediction.recommendation, "watch");
  assert.equal(predictionStrengthLabel(prediction), "관망");
});

test("운영 가격대의 시장 최유력 픽은 추천 강도를 함께 표시한다", () => {
  const prediction = predictionForGame([
    option("홈", 1.8, 0.58), option("원정", 2.1, 0.42),
  ]);
  assert.equal(prediction.option["선택"], "홈");
  assert.equal(prediction.recommendation, "recommend");
});

test("파생 마켓이 최종 운영 추천이면 관망으로 누락하지 않고 대표 픽으로 표시한다", () => {
  const prediction = predictionForGame([
    option("홈", 1.5, 0.6), option("원정", 2.1, 0.4),
    option("오버", 1.7, 0.65, "언더오버"),
  ]);
  assert.equal(prediction.option["선택"], "오버");
  assert.equal(prediction.recommendation, "recommend");
  assert.equal(predictionStrengthLabel(prediction), "추천");
});

test("배당이 없거나 홀짝만 있으면 억지 픽을 만들지 않는다", () => {
  assert.equal(predictionForGame([]), null);
  assert.equal(predictionForGame([option("홀", 1.8, 0.5, "홀짝")]), null);
});
