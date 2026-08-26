import assert from "node:assert/strict";
import { alignTodayRecommendations, canonicalOption, canonicalPick } from "./unified-recommendation.js";

const home = { market: "승패", label: "", "게임번호": "10", "선택": "홈", "배당": 1.55 };
const away = { market: "승패", label: "", "게임번호": "10", "선택": "원정", "배당": 2.1 };
const game = { round: 7, "추천": { ...home, "배당": 1.6 }, options: [home, away] };
const grades = { odds_bins: [{ bin: "1.5-1.8", roi: -.1, hit: .6, grade: "B" }] };

assert.equal(canonicalOption(game), home, "생성 시점 추천을 현재 배당 선택지에 연결한다");
assert.equal(canonicalPick(game, game.options, grades).o, home);

const today = { candidates: [
  { round: 7, game_no: "10", market: "승패", market_label: "", sel: "홈", odds: 1.55 },
  { round: 7, game_no: "10", market: "승패", market_label: "", sel: "원정", odds: 2.1 },
] };
const alignedToday = alignTodayRecommendations(today, [game]);
assert.deepEqual(alignedToday.candidates.map((row) => row.sel), ["홈"]);
assert.equal(alignedToday.candidates[0].recommendation_basis, "game-model");
assert.deepEqual(alignedToday.alignment, {
  input_candidates: 2,
  safe_candidates: 1,
  game_model_candidates: 1,
  market_fallback_candidates: 0,
  dropped_by_safety: 1,
});

const fallback = { candidates: [
  { round: 8, game_no: "20", market: "승패", market_label: "", sel: "원정",
    odds: 1.85, is_market_favorite: true },
] };
const alignedFallback = alignTodayRecommendations(fallback, [game]).candidates[0];
assert.equal(alignedFallback.sel, "원정", "모델 추천이 없는 경기는 시장 최유력으로 보완한다");
assert.equal(alignedFallback.recommendation_basis, "market-favorite-fallback");

const withoutGames = alignTodayRecommendations(fallback, []);
assert.equal(withoutGames.candidates.length, 1,
  "경기 카드 수집이 늦거나 비어도 안전한 시장 후보를 없애지 않는다");
assert.equal(withoutGames.alignment.market_fallback_candidates, 1);

const unsafeFallback = { candidates: [
  { round: 8, game_no: "21", market: "승패", market_label: "", sel: "원정",
    odds: 2.2, is_market_favorite: true },
  { round: 8, game_no: "22", market: "홀짝", market_label: "", sel: "홀",
    odds: 1.8, is_market_favorite: true },
] };
const rejected = alignTodayRecommendations(unsafeFallback, []);
assert.equal(rejected.candidates.length, 0, "보완 후보도 안전 필터를 우회할 수 없다");
assert.equal(rejected.alignment.dropped_by_safety, 2);

const moved = { ...game, options: [{ ...home, "배당": 2.2 }, away] };
assert.equal(canonicalOption(moved), null, "안전 배당 범위를 벗어나면 예전 추천을 유지하지 않는다");
console.log("unified recommendation tests passed");
