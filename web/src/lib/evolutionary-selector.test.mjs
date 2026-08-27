import test from "node:test";
import assert from "node:assert/strict";

import { rankEvolutionaryCandidates, refreshEvolutionarySelector } from "./evolutionary-selector.js";

const genome = {
  confidence: 1, odds: 0, overround: 0, market_gap: 0, price_distance: 0,
  three_way: 0, handicap: 0, totals: 0, first_half: 0,
  baseball: 0, basketball: 0, volleyball: 0, soccer: 0,
};
const rule = {
  profile: "balanced",
  genome,
  constraints: { odds_min: 1.4, odds_max: 1.85, target_odds: 1.58 },
};
const candidate = (probability, odds, kickoff = "2026-08-27T19:00:00+09:00") => ({
  sport: "sc", league: "테스트", market: "승무패", n_way: 3,
  market_prob: probability, market_gap: .2, overround: 1.13, odds, kickoff_at: kickoff,
});

test("브라우저 재판정도 학습기와 같은 배당 범위·유전자를 사용한다", () => {
  const ranked = rankEvolutionaryCandidates([
    candidate(.58, 1.55), candidate(.62, 1.45), candidate(.80, 1.20),
  ], rule);
  assert.equal(ranked.length, 2);
  assert.equal(ranked[0].market_prob, .62);
});

test("역사 감사에서 탈락한 전략은 현재 후보를 만들지 않는다", () => {
  const selector = refreshEvolutionarySelector({
    status: "shadow_only",
    profiles: {
      balanced: { rule, historical_status: "promising_but_unproven" },
      challenge: { rule, historical_status: "rejected_in_historical_audit" },
    },
  }, [candidate(.62, 1.45)]);
  assert.equal(selector.profiles.balanced.selected.market_prob, .62);
  assert.equal(selector.profiles.challenge.selected, null);
  assert.deepEqual(selector.profiles.challenge.alternatives, []);
});
