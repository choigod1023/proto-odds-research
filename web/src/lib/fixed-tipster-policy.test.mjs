import test from "node:test";
import assert from "node:assert/strict";
import { buildDecisionViewModel, canApplyDecisionProbability, FIXED_TIPSTER_VERSION, recommendedSelectionForModel } from "./decision-view-model.js";
import { canonicalOption } from "./unified-recommendation.js";

function fixture(applied = true) {
  const options = [
    { selection_id: "h", offer_id: "oh", market: "승패", label: "", 선택: "홈", 배당: 1.49, 시장확률: .52, 최종확률: applied ? .468 : .52 },
    { selection_id: "a", offer_id: "oa", market: "승패", label: "", 선택: "원정", 배당: 2.1, 시장확률: .48, 최종확률: applied ? .532 : .48 },
    { selection_id: "t", offer_id: "ot", market: "언더오버", label: "8.5", 선택: "언더", 배당: 1.8, 시장확률: .8, 최종확률: .8 },
  ];
  const option = options[applied ? 1 : 0];
  const at = "2026-10-25T22:00:00Z";
  return { event_id: "evt", league: "MLB", home: "뉴욕양키", away: "보스레드", date: "10.26(월) 08:10", year: 2026,
    options, decision_snapshot: {
      schema_version: "decision-snapshot-v2", event_id: "evt", decision_id: "dec", input_revision_hash: "a".repeat(64),
      as_of: at, audit: { feature_cutoff_at: at, built_at: at }, action: "market_reference",
      selection_id: option.selection_id, offer_id: option.offer_id,
      model: { status: applied ? "operational" : "shadow", validated_edge: false,
        operating_version: applied ? FIXED_TIPSTER_VERSION : "shin-market-anchor-v1",
        selection_policy: FIXED_TIPSTER_VERSION, policy_authorized: applied,
        promotion_gate: applied ? "user_authorized_unvalidated" : "not_passed" },
      probability: { market: option.시장확률, final: option.최종확률, ai_delta_applied: applied ? .052 : 0,
        basis: applied ? FIXED_TIPSTER_VERSION : "shin_market" },
      stages: Object.fromEntries(["market", "structured_ai", "availability_ai", "language_ai"].map(id => [id, { status: "missing" }])),
      evidence: [],
    } };
}

test("authorized fixed91 reversal survives frontend contract without claiming verified edge", () => {
  const g = fixture();
  assert.equal(canonicalOption(g), g.options[1]);
  const view = buildDecisionViewModel(g, g.options[1]);
  assert.equal(view.action, "market_reference");
  assert.equal(view.probability.final, .532);
  assert.equal(view.probability.basis, FIXED_TIPSTER_VERSION);
  assert.equal(view.model.validatedEdge, false);
  assert.equal(view.model.policyAuthorized, true);
});

test("missing votes retain low-odds MLB moneyline, not unrelated totals", () => {
  const g = fixture(false);
  assert.equal(canonicalOption(g), g.options[0]);
  const view = buildDecisionViewModel(g, g.options[0]);
  assert.equal(view.action, "market_reference");
  assert.equal(view.probability.final, .52);
  assert.equal(view.model.validatedEdge, false);
});

test("generic legacy policy flags cannot authorize an arbitrary model", () => {
  const model = fixture().decision_snapshot.model;
  assert.equal(canApplyDecisionProbability(model), true);
  assert.equal(canApplyDecisionProbability({ ...model, operating_version: "other" }), false);
  assert.equal(canApplyDecisionProbability({ ...model, validated_edge: true }), false);
});

test("old frozen MLB policy remains readable without relabelling history", () => {
  const g = fixture();
  g.decision_snapshot.model.operating_version = "mlb-market90-tipster10-v1";
  g.decision_snapshot.model.selection_policy = "mlb-market90-tipster10-v1";
  g.decision_snapshot.probability.basis = "mlb-market90-tipster10-v1";
  assert.equal(canonicalOption(g), g.options[1]);
  assert.equal(buildDecisionViewModel(g, g.options[1]).probability.basis, "mlb-market90-tipster10-v1");
});

test("three-way fallback includes draw and rejects missing or duplicate sides", () => {
  const model = fixture(false).decision_snapshot.model;
  const options = ["홈", "무", "원정"].map((side, i) => ({market: "승무패", 선택: side, 배당: 3, 시장확률: [.3,.4,.3][i]}));
  assert.equal(recommendedSelectionForModel(options, model), options[1]);
  assert.equal(recommendedSelectionForModel(options.slice(0, 2), model), null);
  assert.equal(recommendedSelectionForModel([...options, options[0]], model), null);
});
