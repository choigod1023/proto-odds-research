import assert from "node:assert/strict";
import { alignTodayRecommendations, buildTodayMemberships, canonicalOption, canonicalPick,
  selectionKey } from "./unified-recommendation.js";

const home = {
  selection_id: "sel_home", offer_id: "off_home",
  market: "승패", label: "", "게임번호": "10", "선택": "홈",
  "배당": 1.55, "시장확률": .62,
};
const away = {
  selection_id: "sel_away", offer_id: "off_away",
  market: "승패", label: "", "게임번호": "10", "선택": "원정",
  "배당": 2.1, "시장확률": .38,
};
const game = {
  event_id: "evt_game",
  round: 7,
  // 레거시 추천은 반대로 주입해도 snapshot만 따라야 한다.
  추천: away,
  options: [home, away],
  decision_snapshot: {
    schema_version: "decision-snapshot-v2",
    event_id: "evt_game",
    input_revision_hash: "a".repeat(64),
    action: "market_reference",
    selection_id: "sel_home",
    offer_id: "off_home",
    as_of: "2026-08-27T09:00:00+09:00",
    probability: { market: .62, ai_delta_applied: 0, final: .62 },
    model: {
      status: "shadow", validated_edge: false, promotion_gate: "not_passed",
      operating_version: "shin-market-anchor-v1",
    },
    stages: {
      market: { status: "used" },
      structured_ai: { status: "shadow" },
      availability_ai: { status: "missing" },
      language_ai: { status: "template" },
    },
    evidence: [],
    audit: {
      feature_cutoff_at: "2026-08-27T09:00:00+09:00",
      built_at: "2026-08-27T09:00:01+09:00",
    },
  },
};
const grades = { odds_bins: [{ bin: "1.5-1.8", roi: -.1, hit: .6, grade: "B" }] };

assert.equal(canonicalOption(game), home, "v2 snapshot 선택을 현재 선택지에 연결한다");
assert.equal(canonicalPick(game, game.options, grades).o, home);

const today = { candidates: [
  { round: 7, game_no: "10", market: "승패", market_label: "", sel: "홈", odds: 1.55 },
  { round: 7, game_no: "10", market: "승패", market_label: "", sel: "원정", odds: 2.1 },
] };
const alignedToday = alignTodayRecommendations(today, [game]);
assert.deepEqual(alignedToday.candidates.map((row) => row.sel), ["홈"]);
assert.equal(alignedToday.candidates[0].recommendation_basis, "game-decision");
assert.deepEqual(alignedToday.alignment, {
  input_candidates: 2,
  safe_candidates: 1,
  game_model_candidates: 1,
  market_fallback_candidates: 0,
  dropped_by_safety: 1,
});

const unmatched = { candidates: [
  { round: 8, game_no: "20", market: "승패", market_label: "", sel: "원정",
    odds: 1.85, is_market_favorite: true },
] };
assert.equal(alignTodayRecommendations(unmatched, [game]).candidates.length, 0,
  "단일 경기 판정과 맞지 않는 이름뿐인 보완 후보는 제거한다");
assert.equal(alignTodayRecommendations(unmatched, []).candidates.length, 0,
  "경기 판정 원장이 없으면 브라우저가 새 방향을 만들지 않는다");

const moved = { ...game, options: [{ ...home, "배당": 2.2 }, away] };
assert.equal(canonicalOption(moved), null, "안전 배당 범위를 벗어나면 예전 추천을 유지하지 않는다");

const liveMoved = { ...game, _liveOddsChanged: true };
assert.equal(canonicalOption(liveMoved), null, "실시간 배당 revision 뒤에는 재계산 전 선택을 숨긴다");

const started = { ...game, _liveStarted: true };
assert.equal(canonicalOption(started), null, "실시간 중계가 시작되면 오늘 후보에서 제거한다");

const legacy = { ...game, decision_snapshot: undefined, 추천: away };
assert.equal(canonicalOption(legacy), home,
  "스냅샷이 없으면 레거시 추천을 무시하고 시장 최유력으로 복구한다");
assert.equal(canonicalPick(legacy, legacy.options, grades).policy, "market-fallback");
const legacyAligned = alignTodayRecommendations(today, [legacy]);
assert.equal(legacyAligned.candidates[0].recommendation_basis, "market-fallback");
assert.equal(legacyAligned.alignment.market_fallback_candidates, 1);

const liveFavoriteFlipped = {
  ...game,
  decision_snapshot: null,
  _liveOddsRecalculated: true,
  options: [
    { ...home, "배당": 2.1, "시장확률": 0.39 },
    { ...away, "배당": 1.55, "시장확률": 0.61, _liveOverround: 1.121 },
  ],
};
const flippedToday = alignTodayRecommendations({
  ...today,
  odds_bins: [{ bin: "1.5-1.8", roi: -0.1, n: 1000, hit: 0.6, grade: "B" }],
  candidates: [today.candidates[0]],
}, [liveFavoriteFlipped]);
assert.equal(flippedToday.candidates[0].sel, "원정",
  "실시간 배당에서 최유력 방향이 바뀌면 이전 방향을 유지하지 않는다");
assert.equal(flippedToday.candidates[0].odds, 1.55);
assert.equal(flippedToday.candidates[0].market_prob, 0.61);
assert.equal(flippedToday.candidates[0].payout, 89.21);

const memberships = buildTodayMemberships({
  solo: alignedToday.candidates[0],
  plans: [
    { ok: true, target: 1.4, picks: [alignedToday.candidates[0]] },
    { ok: true, target: 2, picks: [alignedToday.candidates[0]] },
    { ok: false, target: 5, picks: [alignedToday.candidates[0]] },
  ],
});
const membership = memberships.get(selectionKey(home, game.round));
assert.equal(membership.solo, true);
assert.deepEqual(membership.targets, [1.4, 2],
  "경기 카드 하나에 실제로 포함된 오늘 조합만 붙인다");
assert.equal(memberships.size, 1, "별도 후보 행이 아니라 같은 판정 키로 통합한다");

console.log("unified recommendation tests passed");
