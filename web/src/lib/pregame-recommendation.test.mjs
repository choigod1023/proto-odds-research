import test from "node:test";
import assert from "node:assert/strict";
import { pregameRecommendation } from "./pregame-recommendation.js";

test("chooses one existing pick across markets, and refuses unmatched or research offers", () => {
  const market = (gameNo, market, label, line, sides, prices) => sides.map((side, i) => ({
    게임번호: gameNo, market, label, line, 선택: side, 배당: prices[i],
    n_way: sides.length, period: "full_game", settlement_scope: "full",
    selection_id: `s${gameNo}-${i}`, offer_id: `o${gameNo}-${i}`,
  }));
  const winner = market("1", "승무패", "", null, ["홈", "무", "원정"], [1.8, 3.4, 4]);
  const handicap = market("2", "핸디캡", "H -2.5", -2.5, ["핸디홈", "핸디원정"], [3.16, 1.2]);
  const total = market("3", "언더오버", "U 9.5", 9.5, ["언더", "오버"], [1.66, 1.83]);
  const recommendation = (option, changes = {}) => ({ option, item: {
    market: option.market, automatic_recommendation: true, ...changes,
  } });
  const all = [...winner, ...handicap, ...total];
  const picks = [recommendation(handicap[1]), recommendation(total[0])];
  const selected = pregameRecommendation(winner[2], picks, all);
  assert.equal(selected.option, total[0]);
  assert.ok(Math.abs(selected.probability - 1.83 / 3.49) < 1e-12);
  assert.ok(Math.abs(selected.estimatedRoi - (selected.probability * 1.66 - 1)) < 1e-12);
  assert.equal(pregameRecommendation(null, [picks[0]], all).option, handicap[1]);
  assert.equal(pregameRecommendation(winner[0], [picks[0]], all).option, winner[0]);
  assert.equal(pregameRecommendation(null, [picks[1]], all.filter((row) => row !== total[1])), null);
  assert.equal(pregameRecommendation(null, [recommendation(total[0], {candidate_only: true})], all), null);
  assert.equal(pregameRecommendation(null, [recommendation({...total[0], 배당: 1.95})], all), null);
  assert.equal(pregameRecommendation(null, [picks[1]], [...all, total[0]]), null);
});
