import test from "node:test";
import assert from "node:assert/strict";
import { buildDecisionViewModel, decisionLabel, canApplyDecisionProbability, FIXED_TIPSTER_VERSION, recommendedSelectionForModel } from "./decision-view-model.js";
import { canonicalOption } from "./unified-recommendation.js";
import { TIPSTER_VOTE_VERSION, tipsterVoteSummary } from "./fixed-tipster-policy.js";

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

test("upcoming card and detail header use the authorized hybrid probability", async () => {
  const { createServer } = await import("vite");
  const { fileURLToPath } = await import("node:url");
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const server = await createServer({ configFile: false, root: fileURLToPath(new URL("../../", import.meta.url)),
    esbuild: { jsx: "automatic" }, optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null }, appType: "custom" });
  const originalNow = Date.now;
  Date.now = () => Date.parse("2026-10-25T22:00:00Z");
  try {
    const { Game } = await server.ssrLoadModule("/src/pages/Markets.jsx");
    const g = fixture();
    g.sport = "bs";
    g.decision_snapshot.tipster_hybrid = {status: "applied", authors: 1, evidence: []};
    const props = {g, opts:g.options, todayOption:g.options[1], grades:{odds_bins:[]}, year:2026};
    const row = renderToStaticMarkup(createElement(Game, props));
    assert.match(row, /혼합 모델 확률 53.2%/);
    const detail = renderToStaticMarkup(createElement(Game, {...props, detailOnly:true}));
    assert.match(detail.replace(/<[^>]+>/g, " "), /경기 전 적중 확률\s+53.2%/);
    g.decision_snapshot.model.policy_authorized = false;
    const rejected = renderToStaticMarkup(createElement(Game, {...props, detailOnly:true}));
    assert.doesNotMatch(rejected.replace(/<[^>]+>/g, " "), /경기 전 적중 확률\s+53.2%/);
  } finally { Date.now = originalNow; await server.close(); }
});

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

test("actual tipster provenance is exposed only for a valid applied revision", () => {
  const g = fixture();
  g.decision_snapshot.tipster_hybrid = { status: "applied", authors: 1, evidence: [{
    id: "v1", author: "pick79.com:픽천국", side: "원정", observed_at: g.decision_snapshot.as_of,
    source_url: "https://pick79.com/posts/detail/example" }] };
  assert.equal(buildDecisionViewModel(g, g.options[1]).tipster.evidence[0].author, "pick79.com:픽천국");
  g.decision_snapshot.tipster_hybrid.status = "market_fallback";
  assert.equal(buildDecisionViewModel(g, g.options[1]).tipster, null);
  g.decision_snapshot.tipster_hybrid.status = "applied";
  g.decision_snapshot.selection_id = "missing";
  assert.equal(buildDecisionViewModel(g, g.options[1]).tipster, null);
});

function voteFixture() {
  const g = fixture();
  const raw = g.decision_snapshot;
  Object.assign(raw.model, { operating_version: TIPSTER_VOTE_VERSION, selection_policy: TIPSTER_VOTE_VERSION,
    affects_selection: true, affects_probability: false });
  Object.assign(raw.probability, { market: .48, final: .48, ai_delta_applied: 0, basis: "shin_market" });
  g.options.forEach(row => { row.최종확률 = row.시장확률; row.AI반영 = false; });
  raw.tipster_hybrid = { version: TIPSTER_VOTE_VERSION, status: "applied", selected_side: "원정", authors: 3,
    vote_counts: {홈: 1, 무: 0, 원정: 2}, vote_shares: {홈: 1/3, 무: 0, 원정: 2/3},
    evidence: ["홈", "원정", "원정"].map((side, i) => ({id: `vote-${i}`, author: `author-${i}`, side, observed_at: raw.as_of})) };
  return g;
}

test("plurality can select the market nonfavorite without authorizing a probability override", () => {
  const g = voteFixture();
  assert.equal(canonicalOption(g), g.options[1]);
  assert.equal(canApplyDecisionProbability(g.decision_snapshot.model), false);
  g.decision_snapshot.probability.final = .999; // A malformed vote share must never become a probability.
  const view = buildDecisionViewModel(g, g.options[1]);
  assert.equal(view.action, "market_reference");
  assert.equal(view.probability.final, .48);
  assert.equal(view.probability.aiDeltaApplied, 0);
  assert.equal(view.tipsterVote, true);
  assert.equal(tipsterVoteSummary(view.tipster).share, 2/3);
});

test("new frozen snapshot vote is preserved against newer game metadata", () => {
  const g = voteFixture();
  g.tipster_hybrid = {...g.decision_snapshot.tipster_hybrid, selected_side: "홈"};
  assert.equal(canonicalOption(g), g.options[1]);
  assert.equal(buildDecisionViewModel(g, g.options[1]).tipster.selected_side, "원정");
});

test("new game-level metadata is supported but never relabels historical hybrid snapshots", () => {
  const g = voteFixture();
  g.tipster_hybrid = g.decision_snapshot.tipster_hybrid;
  delete g.decision_snapshot.tipster_hybrid;
  assert.equal(canonicalOption(g), g.options[1]);
  const old = fixture();
  old.tipster_hybrid = g.tipster_hybrid;
  const view = buildDecisionViewModel(old, old.options[1]);
  assert.equal(view.tipsterVote, false);
  assert.equal(view.probability.final, .532);
});

test("withheld, missing, invalid, or unauthorized votes never fall back to a market pick", () => {
  for (const reason of ["tied_votes", "no_eligible_pregame_votes"]) {
    const g = voteFixture();
    Object.assign(g.decision_snapshot, {action: "withhold", selection_id: null, offer_id: null});
    Object.assign(g.decision_snapshot.tipster_hybrid, {status: "withheld", selected_side: null, reason});
    assert.equal(canonicalOption(g), null);
    assert.equal(buildDecisionViewModel(g).action, "withhold");
  }
  const g = voteFixture();
  g.decision_snapshot.tipster_hybrid.selected_side = "무";
  assert.equal(canonicalOption(g), null);
  g.decision_snapshot.tipster_hybrid.selected_side = "원정";
  g.decision_snapshot.model.policy_authorized = false;
  assert.equal(canonicalOption(g), null);
});

test("three-way vote chooses draw by plurality rather than the market maximum", () => {
  const g = voteFixture();
  g.options = ["홈", "무", "원정"].map((side, i) => ({ selection_id: side, offer_id: `offer-${side}`,
    market: "승무패", label: "", 선택: side, 배당: 3, 시장확률: [.5,.2,.3][i], 최종확률: [.5,.2,.3][i] }));
  Object.assign(g.decision_snapshot, {selection_id: "무", offer_id: "offer-무"});
  Object.assign(g.decision_snapshot.probability, {market: .2, final: .2});
  g.decision_snapshot.tipster_hybrid.selected_side = "무";
  assert.equal(canonicalOption(g), g.options[1]);
});

test("rendered vote card, detail and evidence distinguish support from market probability and suppress withheld fallback", async () => {
  const { createServer } = await import("vite");
  const { fileURLToPath } = await import("node:url");
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const server = await createServer({ configFile: false, root: fileURLToPath(new URL("../../", import.meta.url)),
    esbuild: { jsx: "automatic" }, optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null }, appType: "custom" });
  const originalNow = Date.now;
  Date.now = () => Date.parse("2026-10-25T22:00:00Z");
  try {
    const { Game } = await server.ssrLoadModule("/src/pages/Markets.jsx");
    const { default: PredictionPanel } = await server.ssrLoadModule("/src/components/PredictionPanel.jsx");
    const g = voteFixture();
    g.sport = "bs";
    const props = {g, opts: g.options, todayOption: g.options[0], grades: {odds_bins: []}, year: 2026};
    const render = extra => renderToStaticMarkup(createElement(Game, {...props, ...extra})).replace(/<[^>]+>/g, " ");
    const row = render();
    assert.match(row, /픽스터 투표/);
    assert.match(row, /3명\/발행처/);
    assert.match(row, /원정 2표/);
    assert.match(row, /투표 66.7% \(지지율 · 예상 적중률 아님\)/);
    assert.match(row, /배당 기반 시장확률 48.0%/);
    assert.doesNotMatch(row, /혼합 모델 확률|시장 90%/);
    const detail = render({detailOnly: true});
    assert.match(detail, /배당 기반 시장확률\s+48.0%/);
    const decision = buildDecisionViewModel(g, g.options[1]);
    const panel = renderToStaticMarkup(createElement(PredictionPanel, {analysis: {decision,
      prediction: {probability: .48}, reasons: [], cautions: []}})).replace(/<[^>]+>/g, " ");
    assert.match(panel, /author-0/);
    assert.match(panel, /투표 66.7%/);
    assert.doesNotMatch(panel, /시장확률 90%|픽스터 미반영/);
    Object.assign(g.decision_snapshot, {selection_id: "h", offer_id: "oh"});
    Object.assign(g.decision_snapshot.probability, {market: .52, final: .52});
    g.decision_snapshot.tipster_hybrid = {version: TIPSTER_VOTE_VERSION, status: "applied",
      reason: "market_fallback_no_votes", selection_source: "market_fallback", selected_side: "홈", authors: 0};
    for (const fallback of [render(), render({detailOnly: true})]) {
      assert.match(fallback, /시장 확률 기준 · 픽스터 없음/);
      assert.doesNotMatch(fallback, /최다 득표|픽스터 투표 · 0명/);
    }
    Object.assign(g.decision_snapshot, {action: "withhold", selection_id: null, offer_id: null});
    Object.assign(g.decision_snapshot.tipster_hybrid, {status: "withheld", selected_side: null, reason: "tied_votes"});
    const withheld = render({detailOnly: true});
    assert.match(withheld, /픽스터 투표 동률 · 보류/);
    assert.doesNotMatch(withheld, /detail-pick|배당 1.49|48.0%|52.0%/);
  } finally { Date.now = originalNow; await server.close(); }
});


test("market fallback keeps the selected market side and is labelled without tipster votes", () => {
  const g = voteFixture();
  const raw = g.decision_snapshot;
  Object.assign(raw, {selection_id: "h", offer_id: "oh"});
  Object.assign(raw.probability, {market: .52, final: .52});
  raw.tipster_hybrid = {version: TIPSTER_VOTE_VERSION, status: "applied", reason: "market_fallback_no_votes",
    selection_source: "market_fallback", selected_side: "홈", authors: 0};
  assert.equal(canonicalOption(g), g.options[0]);
  const view = buildDecisionViewModel(g, g.options[0]);
  assert.equal(view.action, "market_reference");
  assert.equal(decisionLabel(view), "시장 확률 기준 · 픽스터 없음");
  assert.equal(tipsterVoteSummary(view.tipster).text, "시장 확률 기준 · 픽스터 없음");
  assert.equal(tipsterVoteSummary(view.tipster).share, null);
});
