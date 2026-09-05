import assert from "node:assert/strict";
import test from "node:test";
import { isOvernightGame, matchesGameDate } from "./game-date-filter.js";
import { gamePhase } from "./match-status.js";
import { trackTodayPicks } from "./today-pick-tracking.js";

const now = Date.parse("2026-09-06T00:25:00+09:00");
const game = { year: 2026, date: "09.05(토) 23:00", round: 105, sport: "sc", league: "EPL",
  home: "홈", away: "원정", status: "경기전", options: [],
  _liveState: { status: "STARTED", home_score: 1, away_score: 0, observed_at: new Date(now).toISOString() },
  prediction_record: { selection_id: "saved", market: "승무패", selection: "홈", odds: 1.6,
    probability: .6, captured_at: "2026-09-05T21:00:00+09:00" } };

test("today preserves yesterday's live game at midnight without including it in tomorrow", () => {
  assert.equal(matchesGameDate(game, "today", now - 30 * 60000), true);
  assert.equal(isOvernightGame(game, now), true);
  assert.equal(matchesGameDate(game, "today", now), true);
  assert.equal(matchesGameDate(game, "tomorrow", now), false);
  assert.equal(matchesGameDate(game, "all", now), true);
  assert.equal(matchesGameDate({ ...game, date: "09.06(일) 23:00" }, "today", now), true);
  assert.equal(matchesGameDate({ ...game, date: "09.07(월) 00:15" }, "tomorrow", now), true);
});

test("finished, cancelled, postponed, old and unmatched previous-day games are excluded", () => {
  for (const row of [{ ...game, _liveState: null }, { ...game, date: "09.04(금) 23:00" },
    { ...game, date: "09.05(토) 00:00" }, { ...game, year: 2025 },
    ...["RESULT", "ENDED", "BEFORE"].map(status => ({ ...game, _liveState: { ...game._liveState, status } })),
    ...["finished", "cancelled", "postponed"].map(key => ({ ...game, _liveState: { ...game._liveState, [key]: true } }))]) {
    assert.equal(matchesGameDate(row, "today", now), false);
  }
});

test("temporary stale feed preserves visibility without falsely claiming LIVE", () => {
  const stale = { ...game, _liveState: { ...game._liveState, observed_at: "2026-09-05T23:50:00+09:00" } };
  assert.equal(matchesGameDate(stale, "today", now), true);
  assert.equal(gamePhase(stale, stale._liveState, now), "pending");
});

test("year boundary uses original kickoff year and recommendation priors stay unchanged", () => {
  const january = Date.parse("2027-01-01T00:25:00+09:00");
  assert.equal(isOvernightGame({ ...game, date: "12.31(목) 23:00" }, january), true);
  const [pick] = trackTodayPicks({ games: [game], now });
  assert.equal(pick.option.selection_id, "saved");
  assert.equal(pick.originalOdds, 1.6);
  assert.equal(pick.openingProbability, .6);
  assert.equal(pick.source, "recorded");
  assert.deepEqual(trackTodayPicks({ games: [{ ...game, _liveState: { ...game._liveState, finished: true } }], now }), []);
});
