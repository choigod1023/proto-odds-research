import assert from "node:assert/strict";
import {
  adjustedGamesWithLiveOdds,
  liveOddsFreshness,
  syncTodayOdds,
} from "./live-odds.js";
import { ticketMetrics } from "./today-plan.js";

const NOW = Date.parse("2026-08-26T10:00:00Z");

assert.equal(
  liveOddsFreshness({ generated_at: "2026-08-26T18:55:00+09:00", odds: {} }, NOW).fresh,
  true,
  "+09와 UTC를 같은 절대시각으로 비교해야 한다",
);
assert.equal(
  liveOddsFreshness({ generated_at: "2026-08-26T09:50:00Z", odds: {} }, NOW).fresh,
  true,
  "정확히 10분은 허용하고 초과분부터 막는다",
);
assert.equal(
  liveOddsFreshness({ generated_at: "2026-08-26T09:49:59Z", odds: {} }, NOW).status,
  "stale",
  "10분을 넘긴 feed는 stale이다",
);
assert.equal(
  liveOddsFreshness({ generated_at: "2026-08-26T09:55:00", odds: {} }, NOW).fresh,
  false,
  "timezone 없는 시각은 로컬 시각으로 추측하지 않는다",
);

const home = {
  market: "승패", n_way: 2, label: "", "게임번호": "10", "선택": "홈",
  "배당": 1.8, "모델확률": 0.6, "시장확률": 0.53, "예상손익": 0.08,
};
const away = {
  market: "승패", n_way: 2, label: "", "게임번호": "10", "선택": "원정",
  "배당": 2.0, "모델확률": 0.4, "시장확률": 0.47, "예상손익": -0.2,
};
const game = {
  round: 7, date: "08.26(수) 19:00", league: "KBO", home: "A", away: "B",
  options: [home, away, { ...home }, { ...away }],
};
const feed = {
  generated_at: "2026-08-26T09:55:00Z",
  odds: { 7: { 10: [2.1, 1.6] } },
};

const fresh = adjustedGamesWithLiveOdds([game, { ...game }], feed, NOW);
assert.equal(fresh.games.length, 1, "중복 game도 첫 항목만 남긴다");
assert.equal(fresh.games[0].options.length, 2, "중복 선택이 live 배열 위치를 두 번 소비하면 안 된다");
assert.deepEqual(fresh.games[0].options.map((option) => option["배당"]), [2.1, 1.6]);
assert.deepEqual(fresh.games[0].options.map((option) => option["모델확률"]), [0.6, 0.4]);
assert.deepEqual(fresh.games[0].options.map((option) => option["시장확률"]), [null, null]);
assert.deepEqual(fresh.games[0].options.map((option) => option._is_current_favorite), [false, true]);
assert.ok(fresh.games[0].options.every((option) =>
  option._live_observed_at === "2026-08-26T09:55:00Z"));

const stale = adjustedGamesWithLiveOdds(
  [game], { ...feed, generated_at: "2026-08-26T09:49:59Z" }, NOW,
);
assert.equal(stale.freshness.status, "stale");
assert.deepEqual(stale.games[0].options.map((option) => option["배당"]), [1.8, 2.0]);
assert.ok(stale.games[0].options.every((option) => !option._live));

const laterHome = { ...home, round: 8, "게임번호": "20", "배당": 1.7 };
const laterAway = { ...away, round: 8, "게임번호": "20", "배당": 2.1 };
const crossRoundGame = {
  ...game,
  // 실제 경기 객체는 최초 판매 회차지만 옵션은 다음 판매 회차도 함께 가진다.
  options: [{ ...home, round: 7 }, { ...away, round: 7 }, laterHome, laterAway],
};
const crossRoundFeed = {
  ...feed,
  odds: {
    7: { 10: [2.1, 1.6] },
    8: { 20: [1.9, 1.8] },
  },
};
const crossRound = adjustedGamesWithLiveOdds([crossRoundGame], crossRoundFeed, NOW);
assert.deepEqual(
  crossRound.games[0].options.map((option) => option["배당"]),
  [2.1, 1.6, 1.9, 1.8],
  "합쳐진 경기에서도 각 옵션의 판매 회차 bucket을 사용해야 한다",
);

const legacyCrossRound = adjustedGamesWithLiveOdds([{
  ...game,
  // 다음 정상 생성 전의 구형 정적 JSON에는 option.round가 없었다.
  options: [home, away,
    { ...home, "게임번호": "20", "배당": 1.7 },
    { ...away, "게임번호": "20", "배당": 2.1 }],
}], crossRoundFeed, NOW);
assert.deepEqual(
  legacyCrossRound.games[0].options.map((option) => option.round),
  ["7", "7", "8", "8"],
  "구형 산출물도 live feed에서 유일한 판매 회차를 복원해야 한다",
);
assert.deepEqual(
  legacyCrossRound.games[0].options.map((option) => option["배당"]),
  [2.1, 1.6, 1.9, 1.8],
  "첫 재생성 전에도 다른 회차 옵션의 실시간 배당을 놓치면 안 된다",
);

const grades = { odds_bins: [
  { bin: "1.5-1.8", roi: -0.08, n: 1000 },
  { bin: "1.8-2.2", roi: -0.12, n: 800 },
] };
const today = { candidates: [{
  round: 7, game_no: "10", market: "승패", market_label: "", sel: "홈",
  odds: 1.8, model_prob: 0.6, market_prob: 0.53, failure_prob: 0.47,
  expected_roi: 0.08, is_market_favorite: true, bin: "1.8-2.2",
  hist_roi: -0.12, hist_n: 800,
}] };
const synced = syncTodayOdds(today, fresh.games, grades).candidates[0];
assert.equal(synced.odds, 2.1);
assert.equal(synced.actual_odds, 2.1);
assert.equal(synced.model_prob, 0.6, "모델 확률은 현재 값 그대로 유지한다");
assert.equal(synced.market_prob, null);
assert.equal(synced.expected_roi, null);
assert.equal(synced.is_market_favorite, false, "favorite은 전체 fresh 마켓 가격으로 재계산한다");
assert.equal(synced.odds_observed_at, feed.generated_at);

const laterToday = { candidates: [{
  round: 8, game_no: "20", market: "승패", market_label: "", sel: "홈",
  odds: 1.7, model_prob: 0.6, market_prob: 0.53, is_market_favorite: true,
}] };
const laterSynced = syncTodayOdds(laterToday, crossRound.games, grades).candidates[0];
assert.equal(laterSynced.odds, 1.9,
  "오늘 후보도 game.round가 아니라 option.round로 현재 가격에 연결해야 한다");
const legacyLaterSynced = syncTodayOdds(laterToday, legacyCrossRound.games, grades).candidates[0];
assert.equal(legacyLaterSynced.odds, 1.9,
  "구형 정적 JSON의 오늘 후보도 복원한 option.round로 연결해야 한다");

const second = { ...synced, odds: 1.6, hist_roi: -0.08, hist_n: 1000 };
const metrics = ticketMetrics([synced, second]);
assert.equal(metrics.actual_odds, 3.36, "실시간 다리 배당으로 조합배당을 다시 계산한다");
assert.equal(metrics.expected_roi, undefined, "stale market_prob로 시장 기대수익을 만들지 않는다");

console.log("live odds tests passed");
