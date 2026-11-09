import assert from "node:assert/strict";
import test from "node:test";
import { startForegroundPoll } from "./foreground-poll.js";
import { nextDecisionFreeze } from "./match-status.js";
import { rowsForGame } from "./live-odds.js";

test("a hidden app pauses polling and resumes on visibility without leaking listeners", () => {
  const page = new EventTarget();
  const target = new EventTarget();
  page.visibilityState = "hidden";
  let tick, loads = 0, cleared = false;
  target.setInterval = (handler) => { tick = handler; return 1; };
  target.clearInterval = (timer) => { assert.equal(timer, 1); cleared = true; };
  const stop = startForegroundPoll(() => loads++, 15000, { page, target });
  tick();
  target.dispatchEvent(new Event("online"));
  target.dispatchEvent(new Event("focus"));
  assert.equal(loads, 0);
  page.visibilityState = "visible";
  page.dispatchEvent(new Event("visibilitychange"));
  tick();
  assert.equal(loads, 2);
  stop();
  page.dispatchEvent(new Event("visibilitychange"));
  assert.equal(loads, 2);
  assert.equal(cleared, true);
});

test("the next price freeze uses kickoff minus 30 minutes in KST", () => {
  const games = [{ year: 2026, date: "11.09(월) 19:00" }, { year: 2026, date: "11.09(월) 18:00" }];
  const boundary = Date.parse("2026-11-09T18:30:00+09:00");
  assert.equal(nextDecisionFreeze(games, boundary - 1), boundary);
  assert.equal(nextDecisionFreeze(games, boundary), Infinity);
});

test("round market index preserves price order and separates repeat fixtures", () => {
  const game = { home: "A", away: "B", date: "11.09(월) 19:00" };
  const later = { ...game, date: "11.10(화) 19:00" };
  const round = { three: { ...game, game_no: 3 }, one: { ...later, game_no: 1 },
    two: { ...game, game_no: 2 } };
  assert.deepEqual(rowsForGame(game, round).map(row => row.game_no), [2, 3]);
  assert.deepEqual(rowsForGame(later, round).map(row => row.game_no), [1]);
  assert.deepEqual(rowsForGame(game, { one: round.one }), []);
});
