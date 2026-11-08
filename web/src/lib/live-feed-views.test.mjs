import test from 'node:test';
import assert from 'node:assert/strict';
import { attachScoreDetail, marketHistoryForGame, marketHistoryNumbers, oddsHistoryUrl } from './live-feed-views.js';

test('modal details cannot overwrite live status or cross a fixture or round', () => {
  const live = {game_id: 'named:1', source: 'named', start: '2026-11-06T23:00:00+09:00', home_score: 4};
  assert.equal(attachScoreDetail(live, {identity: ['named:1', 'named', 'another'], detail: {timeline: [1]}}), live);
  const detail = attachScoreDetail(live, {identity: Object.values(live).slice(0, 3), detail: {timeline: [2], home_score: 1}});
  assert.deepEqual(detail.timeline, [2]);
  assert.equal(detail.home_score, 4);
  const game = {round: 132, home: '삼성', away: 'KT', date: '11.06(금) 18:30'};
  const odds = {markets: {132: {3: {...game, game_no: 3}, 4: {...game, date: 'yesterday', game_no: 4}}}};
  const numbers = marketHistoryNumbers(game, odds);
  assert.deepEqual(numbers, ['3']);
  assert.match(oddsHistoryUrl(game, numbers, odds), /round=132&game_nos=3$/);
  assert.deepEqual(marketHistoryForGame(game, numbers, odds, {round: '133', history: {3: [{odds: 2}]}}), []);
  assert.deepEqual(marketHistoryForGame(game, numbers, odds, {round: '132', history: {3: [{odds: 2}], 4: [{odds: 9}]}}),
    [{gameNo: '3', odds: 2}]);
});
