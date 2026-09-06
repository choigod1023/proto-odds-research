import test from "node:test";
import assert from "node:assert/strict";
import { addSelection, favoriteKey, isFavoriteGame, readFavorites, readTheme } from "./explorer-preferences.js";
import { matchEvidence } from "./match-evidence.js";
import { matchesGameDate } from "./game-date-filter.js";
import { createSelectionRecord } from "./selection-record.js";

const game = { sport: "bs", league: "KBO", home: "홈팀", away: "원정팀", year: 2099, round: 1, date: "09.06(일) 18:00", status: "경기전" };
test("favorites are scoped by sport/league and combine as OR", () => {
  const team = favoriteKey(game, "team", game.home), league = favoriteKey(game, "league", game.league);
  assert.ok(isFavoriteGame(game, [team]));
  assert.ok(isFavoriteGame({ ...game, home: "다른팀" }, [league]));
  assert.ok(!isFavoriteGame({ ...game, sport: "bk" }, [team]));
  assert.ok(!isFavoriteGame({ ...game, league: "NPB" }, [team]));
  assert.ok(isFavoriteGame({ ...game, away: game.home, home: "다른팀" }, [team]));
});
test("bad or unavailable storage is recoverable", () => {
  assert.deepEqual(readFavorites({ getItem: () => '{broken' }), []);
  assert.deepEqual(readFavorites({ getItem: () => '[42,"oops",null]' }), []);
  assert.equal(readTheme({ getItem() { throw Error("denied"); } }), "system");
  assert.equal(readTheme({ getItem: () => "dark" }), "dark");
  assert.equal(readTheme({ getItem: () => "unrecognized" }), "system");
});
test("selection deduplication preserves first-seen odds across polling and separates match dates", () => {
  const option = { market: "승패", 선택: "홈", 배당: 1.8, 시장확률: .55 };
  const items = addSelection([], game, option);
  option.배당 = 1.5;
  assert.equal(items[0].option.배당, 1.8);
  assert.equal(addSelection(items, game, option).length, 1);
  assert.equal(addSelection(items, { ...game, date: "09.07(월) 18:00" }, option).length, 2);
  const record = createSelectionRecord(items[0], { stake: 1000, purchaseOdds: 1.7 });
  assert.equal(record.purchaseOdds, 1.7);
  assert.equal(record.selectionSnapshot.option.배당, 1.8);
  assert.equal(record.openingProbability, .55);
  assert.equal(createSelectionRecord({ game, option: { ...option, 시장확률: null } }).openingProbability, null);
});
test("match facts never turn missing stats into zero and never reconstruct historical reasons", () => {
  assert.equal(matchEvidence({ ...game, form_home: { avg_scored: null, avg_conceded: "" } }).facts.length, 0);
  assert.match(matchEvidence({ ...game, form_home: { last10: "7승 3패" } }).summary, /7승 3패/);
  const finished = matchEvidence({ ...game, status: "정산", form_home: { last10: "10승 0패" } });
  assert.equal(finished.frozen, true);
  assert.deepEqual(finished.facts, []);
});
test("yesterday filter uses Korean calendar dates", () => {
  const now = Date.parse("2099-09-07T00:30:00+09:00");
  assert.ok(matchesGameDate(game, "yesterday", now));
  assert.ok(!matchesGameDate(game, "today", now));
});
