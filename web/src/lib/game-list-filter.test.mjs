import assert from "node:assert/strict";
import test from "node:test";
import { matchesGameMarketFilters } from "./game-list-filter.js";

test("events remain visible without currently available odds", () => {
  for (const status of ["경기전", "배당대기", "결과확인", "정산"]) {
    assert.equal(matchesGameMarketFilters({ status, options: [] }), true);
  }
});

test("saved market and original odds satisfy explicit filters without new offers", () => {
  const game = { options: [], prediction_record: { market: "승패", odds: 1.59 } };
  assert.equal(matchesGameMarketFilters(game, "승패", 1.6), true);
  assert.equal(matchesGameMarketFilters(game, "언더오버"), false);
  assert.equal(matchesGameMarketFilters(game, "승패", 1.5), false);
  assert.deepEqual(game.options, []);
});

test("unknown prices do not satisfy price caps", () => {
  for (const odds of [null, "", 0, 1, "invalid", Infinity]) {
    assert.equal(matchesGameMarketFilters({ prediction_record: { odds } }, "", 2), false);
  }
  assert.equal(matchesGameMarketFilters({ options: [{ market: "승패", 배당: 1.8 }] }, "승패", 2), true);
});
