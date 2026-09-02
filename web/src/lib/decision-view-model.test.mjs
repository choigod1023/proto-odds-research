import assert from "node:assert/strict";
import test from "node:test";
import { buildDecisionViewModel, decisionLabel, resolveDecisionOption } from "./decision-view-model.js";

const eventId = "evt_test";
const option = {
  selection_id: "sel_home",
  offer_id: "off_home",
  market: "승패",
  선택: "승",
  시장확률: .62,
  모델확률: .81,
};

const stages = {
  market: { status: "used", affects_probability: true },
  structured_ai: { status: "shadow", affects_probability: false },
  availability_ai: { status: "missing", affects_probability: false },
  language_ai: { status: "template", affects_probability: false },
};

const snapshot = {
  schema_version: "decision-snapshot-v2",
  event_id: eventId,
  input_revision_hash: "a".repeat(64),
  action: "market_reference",
  selection_id: "sel_home",
  offer_id: "off_home",
  as_of: "2026-08-27T09:00:00+09:00",
  audit: {
    feature_cutoff_at: "2026-08-27T09:00:00+09:00",
    built_at: "2026-08-27T09:00:01+09:00",
    pre_registered: false,
  },
  probability: {
    market: .62,
    ai_candidate: .81,
    ai_delta_candidate: .19,
    ai_delta_applied: 0,
    final: .62,
  },
  model: {
    status: "shadow",
    validated_edge: false,
    promotion_gate: "not_passed",
    operating_version: "shin-market-anchor-v1",
    artifact_hash: null,
  },
  stages,
  evidence: [
    {
      id: "market_price",
      label: "시장",
      available: true,
      usage: {
        market_baseline: "used", ai_residual: "used",
        decision_gate: "used", explainer: "used",
      },
    },
    {
      id: "team",
      label: "팀 기록",
      available: true,
      usage: {
        market_baseline: "ignored", ai_residual: "shadow",
        decision_gate: "ignored", explainer: "used",
      },
    },
  ],
};

const gameFor = (decisionSnapshot = snapshot, extra = {}) => ({
  event_id: eventId,
  options: [option],
  decision_snapshot: decisionSnapshot,
  ...extra,
});

test("유효한 배당이 없으면 큰 모델확률로 화면 추천을 새로 만들지 않는다", () => {
  const decision = buildDecisionViewModel(
    { options: [{ ...option, 모델확률: .99 }] },
    null,
  );
  assert.equal(decision.action, "withhold");
  assert.equal(decision.probability.final, null);
  assert.equal(decision.option, null);
  assert.deepEqual(decision.contractErrors, ["prediction_ledger_required"]);
  assert.deepEqual(decision.gateCodes, [
    "invalid_decision_contract", "prediction_ledger_required",
  ]);
  assert.equal(decisionLabel(decision), "판정 원장 기록 대기");
  assert.equal(decision.withholdReasons[0].title, "사전 판정 원장 기록 대기");
  assert.match(decision.withholdReasons[0].body, /구매 후보로 사용하지 않습니다/);
});

test("shadow AI가 잘못 저장한 final도 시장확률로 fail-close한다", () => {
  const malformed = {
    ...snapshot,
    probability: { ...snapshot.probability, ai_delta_applied: .19, final: .81 },
  };
  const decision = buildDecisionViewModel(gameFor(malformed), option);
  assert.equal(decision.probability.market, .62);
  assert.equal(decision.probability.aiCandidate, .81);
  assert.equal(decision.probability.aiDeltaApplied, 0);
  assert.equal(decision.probability.final, .62);
  assert.equal(decision.probability.basis, "shin_market");
});

test("실시간 배당만 바뀌면 이전 확률과 해설 결론을 숨긴다", () => {
  const decision = buildDecisionViewModel(gameFor(snapshot, {
    _liveOddsChanged: true,
  }), option);
  assert.equal(decision.action, "recalculating");
  assert.equal(decision.probability.market, null);
  assert.equal(decision.probability.final, null);
  assert.equal(decision.staleReason, "live_price_revision_changed");
});

test("실시간 중계가 시작되면 사전 선택과 확률을 마감한다", () => {
  const decision = buildDecisionViewModel(gameFor(snapshot, {
    _liveStarted: true,
  }), option);
  assert.equal(decision.action, "closed");
  assert.equal(decision.option, null);
  assert.equal(decision.probability.final, null);
  assert.equal(decisionLabel(decision), "경기 시작 · 사전 판정 마감");
});

test("중복 단계나 근거 id는 조용히 합치지 않고 계약 오류로 보류한다", () => {
  const malformed = {
    ...snapshot,
    stages: [
      { id: "market", status: "used" },
      { id: "market", status: "used" },
      { id: "availability_ai", status: "missing" },
      { id: "language_ai", status: "template" },
    ],
    evidence: [
      snapshot.evidence[0],
      { ...snapshot.evidence[0], label: "중복" },
    ],
  };
  const decision = buildDecisionViewModel(gameFor(malformed), option);
  assert.equal(decision.action, "withhold");
  assert.ok(decision.contractErrors.includes("invalid_stage_ids"));
  assert.ok(decision.contractErrors.includes("duplicate_evidence_ids"));
  assert.deepEqual(decision.withholdReasons.map((reason) => reason.title), [
    "계산 단계가 누락되거나 중복됨",
    "같은 근거가 중복 기록됨",
  ]);
  assert.ok(decision.withholdReasons.every((reason) => /가지 않습니다/.test(reason.body)));
});

test("operational 문자열과 통과 표식만으로는 미승격 artifact가 확률을 바꾸지 못한다", () => {
  const operational = {
    ...snapshot,
    model: {
      status: "operational",
      validated_edge: true,
      promotion_gate: "passed",
      operating_version: "unlisted-model",
      artifact_hash: "f".repeat(64),
    },
    probability: { ...snapshot.probability, ai_delta_applied: .03, final: .65 },
  };
  const decision = buildDecisionViewModel(gameFor(operational), option);
  assert.equal(decision.probability.final, .62);
  assert.equal(decision.probability.aiDeltaApplied, 0);
  assert.equal(decision.probability.basis, "shin_market");
  assert.equal(decisionLabel(decision), "예상 적중 비교");
});

test("정책 승인만 받은 내부요인 운영식은 감사 표식만 남기고 시장확률로 복귀한다", () => {
  const internal = {
    ...snapshot,
    model: {
      status: "operational",
      validated_edge: false,
      policy_authorized: true,
      promotion_gate: "passed",
      operating_version: "internal-context-blend-v2",
      artifact_hash: null,
    },
    probability: {
      ...snapshot.probability,
      ai_delta_applied: .03,
      final: .65,
      basis: "internal-context-blend-v2",
    },
  };
  const decision = buildDecisionViewModel(gameFor(internal), option);
  assert.equal(decision.probability.final, .62);
  assert.equal(decision.probability.aiDeltaApplied, 0);
  assert.equal(decision.probability.basis, "shin_market");
  assert.equal(decision.model.validatedEdge, false);
  assert.equal(decision.model.policyAuthorized, true);
});

test("v2 압축 원장의 단계 설명과 자료 사용 상태를 공용 카탈로그에서 복원한다", () => {
  const compact = {
    ...snapshot,
    evidence: [{
      id: "lineup",
      label: "선발·라인업",
      available: true,
      usage: {
        market_baseline: "ignored",
        ai_residual: "ignored",
        decision_gate: "ignored",
        explainer: "context_only",
      },
      reason_codes: { ai_residual: "no_first_seen_prediction_ledger" },
    }],
  };
  const decision = buildDecisionViewModel(gameFor(compact), option);
  assert.equal(decision.stages[0].label, "배당 기반 확률");
  assert.match(decision.stages[0].summary, /운영사 마진을 제거/);
  assert.equal(decision.evidence[0].display_status, "context_only");
});

test("스냅샷이 없으면 현재 시장값은 설명만 하고 원장 기록을 기다린다", () => {
  const marketFavorite = {
    ...option, selection_id: undefined, offer_id: undefined,
    배당: 1.55, 시장확률: .60, 모델확률: .10,
  };
  const modelFavorite = {
    ...option, selection_id: undefined, offer_id: undefined,
    선택: "원정", 배당: 2.05, 시장확률: .40, 모델확률: .99,
  };
  const legacy = {
    event_id: eventId,
    options: [marketFavorite, modelFavorite],
    추천: modelFavorite,
  };
  assert.equal(resolveDecisionOption(legacy), null);
  const decision = buildDecisionViewModel(legacy, marketFavorite);
  assert.equal(decision.action, "withhold");
  assert.equal(decision.probability.market, .60);
  assert.equal(decision.probability.final, null);
  assert.equal(decision.probability.basis, "shin_market_reference_only");
  assert.equal(decision.ledgerRequired, true);
  assert.equal(decision.marketReference.selection, marketFavorite["선택"]);
  assert.equal(decision.marketReference.actionable, false);
  assert.deepEqual(decision.contractErrors, ["prediction_ledger_required"]);
  assert.match(decision.withholdReasons[0].body, /구매 후보로 사용하지 않습니다/);
  assert.equal(decisionLabel(decision), "판정 원장 기록 대기");
});

test("실시간 배당만 재계산되고 스냅샷이 없으면 원장 기록을 기다린다", () => {
  const live = {
    event_id: eventId,
    decision_snapshot: null,
    _liveOddsRecalculated: true,
    _liveOddsRecalculatedAt: "2026-08-27T05:02:24Z",
    options: [
      { ...option, selection_id: undefined, offer_id: undefined,
        배당: 2.92, 시장확률: .2744, 모델확률: .31 },
      { ...option, selection_id: undefined, offer_id: undefined,
        선택: "원정", 배당: 1.26, 시장확률: .7256, 모델확률: .69 },
    ],
  };
  const selected = resolveDecisionOption(live);
  const decision = buildDecisionViewModel(live, selected);
  assert.equal(selected, null);
  assert.equal(decision.action, "withhold");
  assert.equal(decision.probability.market, .7256);
  assert.equal(decision.probability.final, null);
  assert.equal(decision.recommendationEligible, null);
  assert.equal(decision.recommendationPriority, null);
  assert.ok(decision.gateCodes.includes("prediction_ledger_required"));
  assert.equal(decision.liveOddsRecalculated, true);
  assert.equal(decision.asOf, "2026-08-27T05:02:24Z");
  assert.equal(decisionLabel(decision), "판정 원장 기록 대기");
});

test("1.50 미만이어도 시장 최유력 방향을 유지한다", () => {
  const lowOption = { ...option, 배당: 1.49 };
  const decision = buildDecisionViewModel(
    gameFor(snapshot, { options: [lowOption] }),
    lowOption,
  );
  assert.equal(decision.action, "market_reference");
  assert.equal(decision.probability.final, .62);
  assert.equal(decision.recommendationEligible, true);
  assert.equal(decision.recommendationPriority, "fallback");
  assert.deepEqual(decision.gateCodes, ["lower_odds_fallback"]);
  assert.equal(decisionLabel(decision), "예상 적중 비교 · 1.50 미만 저배당");
});

test("1.50 이상 우선 정책과 다른 구형 스냅샷은 새 원장 기록을 기다린다", () => {
  const lowOption = { ...option, 배당: 1.48 };
  const eligibleOption = {
    ...option,
    selection_id: "sel_under",
    offer_id: "off_under",
    market: "언더오버",
    선택: "언더",
    배당: 1.75,
    시장확률: .55,
    모델확률: .57,
  };
  const game = gameFor(snapshot, { options: [lowOption, eligibleOption] });
  const selected = resolveDecisionOption(game);
  const decision = buildDecisionViewModel(game, selected);
  assert.equal(selected, null);
  assert.equal(decision.action, "withhold");
  assert.equal(decision.probability.final, null);
  assert.equal(decision.recommendationEligible, null);
  assert.equal(decision.ledgerRequired, true);
  assert.equal(decisionLabel(decision), "판정 원장 기록 대기");
});

test("스냅샷이 없으면 레거시 최종확률을 지우고 시장확률만 설명한다", () => {
  const staleModelFavorite = {
    ...option, selection_id: "sel_under", offer_id: "off_under",
    market: "언더오버", 선택: "언더", 배당: 1.70,
    시장확률: .55, 최종확률: .90, predicted_hit_prob: .90,
  };
  const marketFavorite = { ...option, 배당: 1.48, 시장확률: .60, 최종확률: .60 };
  const game = gameFor(undefined, { options: [marketFavorite, staleModelFavorite] });

  const selected = resolveDecisionOption(game);
  const decision = buildDecisionViewModel(game, selected);

  assert.equal(selected, null);
  assert.equal(decision.action, "withhold");
  assert.equal(decision.probability.market, .55);
  assert.equal(decision.probability.final, null);
  assert.equal(decision.marketReference.selection, staleModelFavorite["선택"]);
  assert.equal(decision.ledgerRequired, true);
});

test("이전 정책의 1.50 미만 보류도 서버 원장 재판정을 기다린다", () => {
  const lowOption = { ...option, 배당: 1.42 };
  const withheld = {
    ...snapshot,
    action: "withhold",
    selection_id: null,
    offer_id: null,
    probability: {
      market: null, ai_candidate: null, ai_delta_candidate: null,
      ai_delta_applied: 0, final: null,
    },
    gate_codes: ["no_eligible_market_reference"],
  };
  const game = gameFor(withheld, { options: [lowOption] });
  const selected = resolveDecisionOption(game);
  const decision = buildDecisionViewModel(game, selected);
  assert.equal(selected, null);
  assert.equal(decision.action, "withhold");
  assert.equal(decision.recommendationPriority, null);
  assert.equal(decision.ledgerRequired, true);
  assert.ok(decision.contractErrors.includes("selection_policy_mismatch"));
  assert.ok(decision.contractErrors.includes("prediction_ledger_required"));
});

test("선택 식별자가 깨지면 현재 유효 배당이 있어도 보류한다", () => {
  const malformed = { ...snapshot, offer_id: "wrong_offer" };
  const pricedOption = { ...option, 배당: 1.6 };
  const game = gameFor(malformed, { options: [pricedOption] });
  const selected = resolveDecisionOption(game);
  const decision = buildDecisionViewModel(game, selected);
  assert.equal(selected, null);
  assert.equal(decision.action, "withhold");
  assert.equal(decision.contractReconstructed, false);
  assert.equal(decision.ledgerRequired, true);
  assert.deepEqual(decision.contractErrors, [
    "selection_not_unique", "market_probability_mismatch", "prediction_ledger_required",
  ]);
  assert.equal(decision.marketReference.probability, .62);
  assert.equal(decision.marketReference.actionable, false);
});

test("현재 선택의 offer 식별자가 달라지면 시장값은 설명만 하고 보류한다", () => {
  const changed = { ...option, offer_id: "off_changed", 배당: 1.6 };
  const game = gameFor(snapshot, { options: [changed] });
  const selected = resolveDecisionOption(game);
  const decision = buildDecisionViewModel(game, selected);
  assert.equal(selected, null);
  assert.equal(decision.action, "withhold");
  assert.equal(decision.contractReconstructed, false);
  assert.equal(decision.ledgerRequired, true);
  assert.deepEqual(decision.contractErrors, [
    "selection_not_unique", "market_probability_mismatch", "prediction_ledger_required",
  ]);
  assert.equal(decision.marketReference.odds, 1.6);
  assert.equal(decision.marketReference.actionable, false);
});

test("경기 ID나 시각 오류는 현재 배당이 있어도 보류하고 이유를 설명한다", () => {
  const unsafe = {
    ...snapshot,
    event_id: "evt_other_game",
    audit: {
      ...snapshot.audit,
      feature_cutoff_at: "2026-08-27T10:00:00+09:00",
      built_at: "2026-08-27T09:00:01+09:00",
    },
  };
  const game = gameFor(unsafe);
  const decision = buildDecisionViewModel(game, null);
  assert.equal(resolveDecisionOption(game), null);
  assert.equal(decision.action, "withhold");
  assert.equal(decision.contractReconstructed, false);
  assert.ok(decision.contractErrors.includes("event_id_mismatch"));
  assert.ok(decision.contractErrors.includes("invalid_cutoff"));
  assert.match(decision.withholdReasons[0].body, /다른 경기/);
  assert.match(decision.withholdReasons[1].body, /경기 후 정보/);
});
