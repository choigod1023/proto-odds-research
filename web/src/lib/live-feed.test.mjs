import test from "node:test";
import assert from "node:assert/strict";
import { mergeLiveFeed, buildLiveIndex } from "./live-feed.js";
import { gamePhase, gameStatusLabel } from "./match-status.js";

const at = "2026-09-06T00:30:00Z";
const now = Date.parse(at);
const row = { source: "named", game_id: "named:11868439", start: "2026-09-06T08:30:00+09:00",
  md: "09.06", home: "샬럿", away: "휴스턴 다이너모", home_alias: ["샬럿FC"], away_alias: ["휴스다이"],
  status: "STARTED", finished: false, home_score: 0, away_score: 0, observed_at: at };
const feed = { generated_at: at, games: [row] };
const game = { year: 2026, date: "09.06(일) 08:30", status: "경기전" };
const lookup = (f) => buildLiveIndex(f).get("샬럿FC|휴스다이|09.06");

test("partial checkpoint retains Charlotte aliases and latest scores", () => {
  const fresh = { ...row, observed_at: "2026-09-06T00:31:00Z", home_score: 1, home_alias: [], away_alias: [] };
  const merged = mergeLiveFeed(feed, { generated_at: fresh.observed_at, games: [fresh] }, now + 60000);
  assert.equal(lookup(merged).home_score, 1);
  assert.equal(gamePhase(game, lookup(merged), now + 60000), "live");
});
test("missing row and malformed response preserve last observation without restamping", () => {
  const merged = mergeLiveFeed(feed, { generated_at: "2026-09-06T00:31:00Z", games: [] }, now + 60000);
  assert.equal(lookup(merged).observed_at, at);
  assert.equal(mergeLiveFeed(feed, { error: "unavailable" }, now), feed);
  assert.equal(gamePhase(game, lookup(merged), now + 11 * 60000), "pending");
  assert.equal(gameStatusLabel(game, lookup(merged), now + 11 * 60000), "중계 갱신 지연");
});
test("out-of-order observations cannot replace fresher score; final result still arrives", () => {
  const ended = { ...row, finished: true, status: "RESULT", observed_at: "2026-09-06T00:32:00Z" };
  const merged = mergeLiveFeed(feed, { generated_at: ended.observed_at, games: [ended] }, now + 120000);
  assert.equal(gamePhase(game, lookup(mergeLiveFeed(merged, feed, now + 180000)), now + 180000), "finished");
  const older = { ...row, observed_at: "2026-09-06T00:00:00Z" };
  assert.equal(lookup({ games: [row, older] }).observed_at, at);
  assert.equal(lookup({ games: [older, row] }).observed_at, at);
});
test("expired carry-over does not leak into future days", () => {
  assert.equal(mergeLiveFeed(feed, { games: [] }, now + 25 * 3600000).games.length, 0);
  assert.equal(buildLiveIndex(feed).has("샬럿FC|휴스다이|09.07"), false);
});

test("temporary BEFORE regression preserves a previously observed live event without refreshing its age", () => {
  const before = { ...row, status: "BEFORE", observed_at: "2026-09-06T00:31:00Z" };
  const merged = mergeLiveFeed(feed, { generated_at: before.observed_at, games: [before] }, now + 60000);
  assert.equal(lookup(merged).status, "STARTED");
  assert.equal(lookup(merged).observed_at, at);
  assert.equal(gamePhase(game, lookup(merged), now + 60000), "live");
  assert.equal(gamePhase(game, lookup(merged), now + 11 * 60000), "pending");
});
