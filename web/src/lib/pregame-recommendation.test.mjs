import test from "node:test";
import assert from "node:assert/strict";
import { pregameRecommendation } from "./pregame-recommendation.js";

test("only displays the saved featured offer at its saved price", () => {
  const pick = {version: "featured-pregame-v1", market: "언더오버", label: "U 9.5", line: 9.5,
    game_no: "3", selection: "언더", odds: 1.66, probability: .524, estimated_roi: -.13,
    selection_id: "sel", offer_id: "offer"};
  const record = {prediction_snapshot_id: "p1", kickoff: "2026-11-06T10:00:00Z",
    captured_at: "2026-11-06T09:00:00Z", pick, result: {result: "hit"}};
  const option = {게임번호: "3", market: pick.market, label: pick.label, line: pick.line,
    선택: pick.selection, 배당: pick.odds, selection_id: pick.selection_id, offer_id: pick.offer_id};
  const game = {date: "11.06(금) 19:00", year: 2026, prediction_revision_id: "p1",
    featured_pick_record: record, options: [option]};
  assert.equal(pregameRecommendation(game).price, 1.66);
  assert.equal(pregameRecommendation({...game, featured_pick_record: null}), null);
  assert.equal(pregameRecommendation({...game, prediction_revision_id: "p2"}), null);
  assert.equal(pregameRecommendation({...game, options: [option, option]}), null);
  const changed = {...game, options: [{...option, 배당: 2.1}]};
  assert.equal(pregameRecommendation(changed), null);
  assert.equal(pregameRecommendation({...game, _liveOddsChanged: true}), null);
  assert.equal(pregameRecommendation(changed, {frozen: true}).price, 1.66);
  assert.equal(pregameRecommendation(changed, {frozen: true}).result, "hit");
  assert.equal(pregameRecommendation({...game, featured_pick_record: {
    ...record, captured_at: "2026-11-06T09:30:00Z",
  }}, {frozen: true}), null);
});
