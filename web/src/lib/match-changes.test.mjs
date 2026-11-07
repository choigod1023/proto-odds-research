import test from "node:test";
import assert from "node:assert/strict";
import { indexMarketHistory, matchMarketHistory, marketHistoryGroups, matchTimelineEvents, recentMatchChanges } from "./match-changes.js";

const game = { round: 100, year: 2026, date: "11.07(토) 18:00", home: "A", away: "B", sport: "bs", league: "KBO" };
const row = (time, line, odds = [1.7, 1.9]) => ({
  gameNo: "90", market: "언더오버", label: `U ${line}`, line, observed_at: `2026-11-07T0${time}:00:00Z`, odds,
});

test("odds history follows the exact fixture and separates every change of line", () => {
  const snapshots = [row(2, 9.5), row(4, 8.5), row(5, 9.5), row(3, 9.5, [1.8, 1.9])];
  const index = indexMarketHistory({ markets: { 100: { 90: { ...game, game_no: 90 } } },
    history: { 100: { 90: snapshots } } });
  assert.equal(matchMarketHistory(index, { ...game, date: "11.08(일) 18:00" }).length, 0);
  assert.equal(matchMarketHistory(index, { ...game, sport: "bk" }).length, 0);
  assert.equal(matchMarketHistory(index, { ...game, round: 101 }).length, 0);
  assert.equal(matchMarketHistory(index, { ...game, year: 2025 }).length, 0);
  const history = matchMarketHistory(index, game);
  const [offer] = marketHistoryGroups([...history, { ...row(6, 8.5), label: "U 10.5" }]);
  assert.deepEqual(offer.segments.map(group => [group.line, group.points.length]), [[9.5, 2], [8.5, 1], [9.5, 1]]);
  const changes = recentMatchChanges({ ...game, _marketHistory: history }, Date.parse("2026-11-07T06:00:00Z"));
  assert.deepEqual(changes.map(event => [event.kind, event.label]), [
    ["line", "언더오버 8.5 → 9.5"], ["odds", "언더오버 언더 1.70 → 1.80배"],
  ]);
});

test("missing, future or undated evidence does not become a recent change", () => {
  const noChanges = { ...game, _marketHistory: [row(2, 9.5), row(2, 8.5), row(5, 7.5)],
    baseball_current: { lineup_status: { official_today: false, observed_at: "2026-11-07T02:00:00Z" } },
    prediction_record: { result: "hit", settled_at: "2026-11-07T03:00:00Z" } };
  assert.deepEqual(recentMatchChanges(noChanges, Date.parse("2026-11-07T04:00:00Z")), []);
  assert.deepEqual(recentMatchChanges(noChanges, Date.parse("2026-11-07T06:00:00Z")), []);
  const timed = { ...game,
    baseball_current: { lineup_status: { official_today: true, observed_at: "2026-11-07T06:00:00Z" } },
    prediction_record: { result: "hit", captured_at: "2026-11-07T07:00:00Z", settled_at: "2026-11-07T11:30:00Z" } };
  assert.deepEqual(matchTimelineEvents(timed, Date.parse("2026-11-07T12:00:00Z")).map(event => event.kind), ["lineup", "pick", "kickoff", "settled"]);
  assert.deepEqual(matchTimelineEvents(timed, Date.parse("2026-11-07T05:00:00Z")).map(event => event.kind), ["kickoff"]);
  assert.deepEqual(recentMatchChanges(timed, Date.parse("2026-11-08T08:00:00Z")).map(event => event.kind), ["settled"]);
});
