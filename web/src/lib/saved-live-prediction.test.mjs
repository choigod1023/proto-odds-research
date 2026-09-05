import test from "node:test";
import assert from "node:assert/strict";
import { savedLivePrediction } from "./saved-live-prediction.js";

const now = Date.parse("2026-09-05T11:00:00Z");
const record = { selection_id: "saved", offer_id: "old-offer", market: "승패", label: "",
  selection: "홈", odds: 1.8, probability: .57, captured_at: "2026-09-05T08:00:00Z" };
const game = { year: 2026, date: "09.05(토) 18:00", sport: "bs", home: "홈팀", away: "원정팀",
  prediction_record: record, options: [], _liveFeedAt: new Date(now).toISOString() };
const live = { status: "STARTED", status_text: "6회초", home_score: 4, away_score: 1 };

test("empty options retain the recorded pick and probability for live estimates", () => {
  const result = savedLivePrediction(game, live, now);
  assert.equal(result.option.선택, "홈");
  assert.equal(result.openingProbability, .57);
  assert.ok(result.estimate.probability > .57);
  assert.equal(record.probability, .57);
});
test("changed live odds, selection identity and probabilities never overwrite the prior", () => {
  const result = savedLivePrediction({ ...game, _liveOddsChanged: true,
    options: [{ selection_id: "new", 선택: "원정", 시장확률: .9, 배당: 1.1 }] }, live, now);
  assert.equal(result.option.selection_id, "saved");
  assert.equal(result.option.배당, 1.8);
  assert.equal(result.openingProbability, .57);
});
for (const value of [null, undefined, "", " ", true, 0, 1, -1, NaN, Infinity]) {
  test(`missing/invalid saved probability (${String(value)}) keeps the pick but no fabricated estimate`, () => {
    const result = savedLivePrediction({ ...game, prediction_record: { ...record, probability: value } }, live, now);
    assert.equal(result.option.선택, "홈");
    assert.equal(result.openingProbability, null);
    assert.equal(result.estimate, null);
  });
}
test("no record or post-kickoff record never creates a retrospective pick", () => {
  assert.equal(savedLivePrediction({ ...game, prediction_record: null }, live, now), null);
  for (const captured_at of ["2026-09-05T09:00:00Z", "2026-09-05T10:00:00Z", "bad", null]) {
    assert.equal(savedLivePrediction({ ...game, prediction_record: { ...record, captured_at } }, live, now), null);
  }
});
test("missing/stale scores preserve the fixed prior, not a current estimate", () => {
  for (const value of [null, undefined, "", true, -1]) {
    assert.equal(savedLivePrediction(game, { ...live, home_score: value }, now).estimateStatus, "missing_score");
  }
  const stale = savedLivePrediction({ ...game, _liveFeedAt: "2026-09-05T10:00:00Z" }, live, now);
  assert.equal(stale.openingProbability, .57);
  assert.equal(stale.estimate, null);
  assert.equal(stale.estimateStatus, "stale_live");
});
test("totals always estimate against the saved line, never current lines", () => {
  const result = savedLivePrediction({ ...game, prediction_record: { ...record, market: "언더오버", label: "U/O 7.5", selection: "언더" },
    options: [{ market: "언더오버", label: "U/O 9.5", line: 9.5 }] }, live, now);
  assert.equal(result.option.label, "U/O 7.5");
  assert.equal(result.estimateStatus, "available");
});
test("unsupported contracts and finished games retain the prior without generic win estimates", () => {
  for (const market of ["핸디캡", "전반승패", "승①패", "승⑤패"]) {
    const result = savedLivePrediction({ ...game, prediction_record: { ...record, market } }, live, now);
    assert.equal(result.openingProbability, .57);
    assert.equal(result.estimateStatus, "unsupported_market");
  }
  assert.equal(savedLivePrediction(game, { ...live, finished: true }, now).estimateStatus, "closed");
});
