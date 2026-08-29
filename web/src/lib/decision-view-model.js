import { eligibleAutoSelections, finalRecommendedSelection, qualifiedUnderdogSelections,
  recommendationPriority } from "./recommendation-policy.js";

const finite = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

export const DECISION_SCHEMA = "decision-snapshot-v2";
// 검증을 마쳐 배포된 artifact가 생길 때만 해시를 코드 리뷰로 추가한다.
const PROMOTED_ARTIFACT_HASHES = new Set();
const POLICY_AUTHORIZED_MODELS = new Set(["internal-context-blend-v2"]);

export const AI_STAGE_CATALOG = [
  {
    id: "market",
    label: "시장 기준선",
    status: "used",
    summary: "동일 시점 배당을 Shin 방식으로 마진 제거해 예상 적중확률의 기준선으로 씁니다.",
  },
  {
    id: "structured_ai",
    label: "수치 AI 후보",
    status: "shadow",
    summary: "구조 모델은 미래 검증 관문을 통과한 계수만 최종 적중확률에 반영합니다.",
  },
  {
    id: "availability_ai",
    label: "선수·출전 정보 AI",
    status: "context_only",
    summary: "공식 발표에서 변화를 구조화하되 최초 관측 시각과 대체선수 영향이 검증되기 전에는 설명에만 씁니다.",
  },
  {
    id: "language_ai",
    label: "생성형 AI",
    status: "wording_only",
    summary: "확정된 사실의 문체만 다듬고 선택·확률·기준점·금액은 만들거나 바꾸지 않습니다.",
  },
];

export const AI_STATUS_LABELS = {
  used: "최종 반영",
  operational: "검증 반영",
  shadow: "연구 중·미반영",
  selection_gate: "조건부 선택 반영",
  context_only: "설명만",
  wording_only: "문체만",
  template: "규칙 문장",
  ignored: "미반영",
  missing: "자료 없음",
  unavailable: "자료 없음",
};

export const AI_EVIDENCE_CATALOG = {
  market_price: { label: "동일 시점 프로토 배당", type: "market" },
  team_performance: { label: "팀 경기력 기록", type: "team" },
  lineup: { label: "선발·라인업", type: "player" },
  availability: { label: "결장·출전 상태", type: "player" },
  cross_market: { label: "교차 마켓 진단", type: "market" },
};

// 현재 선택지의 유효한 배당·Shin 확률만으로 안전하게 다시 만들 수 있는 오류다.
// 저장된 추천 포인터나 당시 확률은 버리고 재계산하므로 AI 값이나 과거 방향을
// 억지로 복원하지 않는다. 아래 목록 밖의 출처·시각·스키마 오류는 계속 보류한다.
const RECOVERABLE_CONTRACT_ERRORS = new Set([
  "missing_selection_identity",
  "selection_not_unique",
  "market_probability_mismatch",
  "withhold_has_selection",
]);

const CONTRACT_ERROR_REASONS = {
  unsupported_schema: {
    title: "판정 규격을 확인할 수 없음",
    body: "현재 화면이 이해하지 못하는 스키마라 선택·확률 필드의 의미를 보장할 수 없습니다. 잘못된 필드를 픽으로 읽을 위험 때문에 가지 않습니다.",
  },
  event_id_mismatch: {
    title: "다른 경기 판정이 섞일 위험",
    body: "판정에 저장된 경기 ID와 현재 경기 ID가 다릅니다. 다른 경기의 선택이나 확률을 적용할 수 있으므로 가지 않습니다.",
  },
  missing_input_revision: {
    title: "계산 입력 버전을 추적할 수 없음",
    body: "어떤 배당·선발·팀 자료로 계산했는지 확인할 해시가 없습니다. 결과를 재현할 수 없어 가지 않습니다.",
  },
  invalid_stage_ids: {
    title: "계산 단계가 누락되거나 중복됨",
    body: "시장·수치 AI·출전 정보·문장 생성 단계가 정확히 한 번씩 기록되지 않았습니다. 확률에 무엇이 반영됐는지 알 수 없어 가지 않습니다.",
  },
  duplicate_evidence_ids: {
    title: "같은 근거가 중복 기록됨",
    body: "동일 자료가 여러 번 반영된 것처럼 보일 수 있어 확률을 과대평가할 위험이 있습니다. 중복이 제거될 때까지 가지 않습니다.",
  },
  invalid_cutoff: {
    title: "계산 시점의 순서가 잘못됨",
    body: "입력 마감보다 계산 시각이 앞서거나 시각 자체가 없습니다. 경기 후 정보가 섞이지 않았다고 보장할 수 없어 가지 않습니다.",
  },
  as_of_cutoff_mismatch: {
    title: "표시 시점과 입력 마감이 다름",
    body: "화면에 표시한 기준 시각과 실제 입력 마감 시각이 일치하지 않습니다. 당시 알 수 없던 정보가 섞였을 가능성 때문에 가지 않습니다.",
  },
  evidence_after_cutoff: {
    title: "입력 마감 뒤의 자료가 포함됨",
    body: "예측 시점 이후에 관측된 자료가 근거에 들어 있습니다. 미래 정보 누출로 적중확률이 부풀려질 수 있어 가지 않습니다.",
  },
  unknown_action: {
    title: "판정 상태를 해석할 수 없음",
    body: "추천·보류 중 어느 상태인지 계약에 없는 값입니다. 구매 가능한 픽으로 확정할 수 없어 가지 않습니다.",
  },
  missing_selection_identity: {
    title: "추천 선택을 현재 용지에서 찾을 수 없음",
    body: "저장된 선택의 식별자가 없어 현재 배당과 대조하지 못했습니다. 현재 배당으로도 자동 복구할 후보가 없어 가지 않습니다.",
  },
  selection_not_unique: {
    title: "추천 선택이 현재 용지와 일치하지 않음",
    body: "저장된 선택을 현재 발매 선택지에서 정확히 하나로 찾지 못했고 현재 배당으로도 복구하지 못했습니다. 다른 번호를 살 위험 때문에 가지 않습니다.",
  },
  market_probability_mismatch: {
    title: "저장 확률과 현재 배당 확률이 다름",
    body: "배당 변화 뒤 이전 확률이 남아 있으며 현재 값으로 안전하게 재계산하지 못했습니다. 낡은 확률에 기대게 되므로 가지 않습니다.",
  },
  withhold_has_selection: {
    title: "보류 판정에 선택값이 함께 저장됨",
    body: "보류와 추천이 동시에 기록되어 어느 쪽이 최종 판단인지 확정할 수 없습니다. 현재 배당으로도 복구하지 못해 가지 않습니다.",
  },
};

const uniqueById = (rows) => {
  const seen = new Set();
  return (rows || []).filter((row) => {
    const id = String(row?.id || "");
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
};

const hasDuplicateIds = (rows) => {
  const ids = (rows || []).map((row) => String(row?.id || ""));
  return ids.some((id) => !id) || new Set(ids).size !== ids.length;
};

export const evidenceUsageRows = (evidence) => {
  const usage = evidence?.usage;
  if (Array.isArray(usage)) return usage;
  if (!usage || typeof usage !== "object") return [];
  const reasons = evidence?.reason_codes || {};
  return Object.entries(usage).map(([consumer, status]) => ({
    consumer,
    status,
    ...(reasons[consumer] ? { reason_code: reasons[consumer] } : {}),
  }));
};

const usageStatus = (evidence) => {
  if (evidence?.available === false) return "missing";
  const rows = evidenceUsageRows(evidence);
  const statusOf = (consumer) => rows.find((row) => row.consumer === consumer)?.status;
  if (statusOf("decision_gate") === "used") return "used";
  if (statusOf("ai_residual") === "shadow") return "shadow";
  if (statusOf("explainer") === "context_only") return "context_only";
  if ([statusOf("decision_gate"), statusOf("ai_residual")].includes("ignored")) return "ignored";
  return "missing";
};

const normalizeStages = (rawStages, option) => {
  const source = Array.isArray(rawStages)
    ? rawStages
    : rawStages && typeof rawStages === "object"
      ? Object.entries(rawStages).map(([id, value]) => ({ id, ...(value || {}) }))
      : fallbackStages(option);
  return uniqueById(source).map((row) => {
    const catalog = AI_STAGE_CATALOG.find((stage) => stage.id === row.id) || {};
    return { ...catalog, ...row };
  });
};

const fallbackStages = (option) => AI_STAGE_CATALOG.map((stage) => ({
  ...stage,
  status: stage.id === "market"
    ? (finite(option?.["시장확률"]) !== null ? "used" : "missing")
      : stage.id === "structured_ai"
      ? (option?.["최종전환"] === true ? "selection_gate"
        : finite(option?.["모델확률"]) !== null ? "shadow" : "missing")
      : stage.status,
  affects_probability: stage.id === "market" && finite(option?.["시장확률"]) !== null,
}));

const fallbackEvidence = (game, option) => [
  { id: "market_price", label: "프로토 배당", available: finite(option?.["시장확률"]) !== null,
    display_status: finite(option?.["시장확률"]) !== null ? "used" : "missing" },
  { id: "team_performance", label: "팀 경기력 기록",
    available: !!(game?.form_home || game?.form_away || game?.h2h),
    display_status: game?.form_home || game?.form_away || game?.h2h ? "shadow" : "missing" },
  { id: "lineup", label: "선발·라인업", available: !!game?.["선발"],
    display_status: game?.["선발"] ? "context_only" : "missing" },
];

const stageRows = (stages) => Array.isArray(stages)
  ? stages
  : stages && typeof stages === "object"
    ? Object.entries(stages).map(([id, value]) => ({ id, ...(value || {}) }))
    : [];

const reconstructMarketContract = (game) => {
  const valid = (game?.options || [])
    .filter((option) => {
      const probability = finite(option?.["시장확률"]);
      const price = finite(option?.["배당"]);
      return String(option?.market || "").trim() !== "홀짝" &&
        probability !== null && probability > 0 && probability < 1 && price > 1;
    });
  const resolved = finalRecommendedSelection(valid);
  const reversal = qualifiedUnderdogSelections(valid).includes(resolved);
  const market = finite(resolved?.["시장확률"]);
  const shadow = finite(resolved?.["모델확률"]);
  const recalculatedAt = game?._liveOddsRecalculatedAt || null;
  return {
    reconstructed: true,
    recommendationRecovered: Boolean(resolved),
    resolved,
    errors: [],
    raw: {
      action: resolved ? "market_reference" : "withhold",
      selection_id: null,
      offer_id: null,
      as_of: recalculatedAt,
      probability: {
        market,
        ai_candidate: shadow,
        ai_delta_applied: 0,
        final: market,
      },
      model: {
        status: shadow === null ? "unavailable" : "shadow",
        validated_edge: false,
        promotion_gate: "not_passed",
        operating_version: "shin-market-anchor-v1",
        artifact_hash: null,
      },
      gate_codes: resolved
        ? reversal
          ? ["qualified_market_reversal"]
          : [game?._liveOddsRecalculated
            ? "live_odds_recalculated" : "reconstructed_market_reference"]
        : ["no_eligible_market_reference"],
      explanation: { kind: "structured_ui", affects_probability: false },
      audit: recalculatedAt ? {
        feature_cutoff_at: recalculatedAt,
        built_at: recalculatedAt,
        pre_registered: false,
      } : null,
    },
  };
};

const snapshotContract = (game) => {
  const raw = game?.decision_snapshot;
  const errors = [];
  // 수집기가 새 스키마보다 먼저 돌면 문서 전체에서 snapshot이 빠질 수 있다.
  // 이때 레거시 추천이나 모델확률은 신뢰하지 않고, 현재 options의 Shin 시장확률과
  // 동일 안전정책만으로 비교 후보를 복구한다. 스냅샷이 있는데 깨진 경우는 그대로 막는다.
  if (!raw || typeof raw !== "object") return reconstructMarketContract(game);
  if (raw.schema_version !== DECISION_SCHEMA) errors.push("unsupported_schema");
  if (!raw.event_id || raw.event_id !== game?.event_id) errors.push("event_id_mismatch");
  if (!raw.input_revision_hash || !/^[a-f0-9]{64}$/.test(raw.input_revision_hash)) {
    errors.push("missing_input_revision");
  }
  const stages = stageRows(raw.stages);
  const expectedStageIds = new Set(AI_STAGE_CATALOG.map((stage) => stage.id));
  const actualStageIds = new Set(stages.map((stage) => stage.id));
  if (hasDuplicateIds(stages) || stages.length !== AI_STAGE_CATALOG.length
      || [...expectedStageIds].some((id) => !actualStageIds.has(id))) {
    errors.push("invalid_stage_ids");
  }
  if (hasDuplicateIds(raw.evidence || [])) errors.push("duplicate_evidence_ids");
  const cutoff = Date.parse(raw?.audit?.feature_cutoff_at || "");
  const built = Date.parse(raw?.audit?.built_at || "");
  if (!Number.isFinite(cutoff) || !Number.isFinite(built) || built < cutoff) {
    errors.push("invalid_cutoff");
  }
  if (Date.parse(raw.as_of || "") !== cutoff) errors.push("as_of_cutoff_mismatch");
  if ((raw.evidence || []).some((row) => {
    if (!row?.observed_at) return false;
    const observed = Date.parse(row.observed_at);
    return !Number.isFinite(observed) || !Number.isFinite(cutoff) || observed > cutoff;
  })) errors.push("evidence_after_cutoff");

  let resolved = null;
  if (raw.action === "market_reference") {
    if (!raw.selection_id || !raw.offer_id) errors.push("missing_selection_identity");
    const matches = (game?.options || []).filter((row) =>
      row?.selection_id === raw.selection_id && row?.offer_id === raw.offer_id);
    if (matches.length !== 1) errors.push("selection_not_unique");
    else resolved = matches[0];
    const rawMarket = finite(raw?.probability?.market);
    const optionMarket = finite(resolved?.["시장확률"]);
    if (!(rawMarket > 0 && rawMarket < 1) || optionMarket === null
        || Math.abs(rawMarket - optionMarket) > 1e-9) {
      errors.push("market_probability_mismatch");
    }
  } else if (raw.action === "withhold") {
    if (raw.selection_id || raw.offer_id) errors.push("withhold_has_selection");
  } else {
    errors.push("unknown_action");
  }
  const resolvedOdds = finite(resolved?.["배당"]);
  const eligibleNow = eligibleAutoSelections(game?.options || []);
  const finalNow = finalRecommendedSelection(game?.options || []);
  const shouldRebuildSelection = resolved && resolvedOdds !== null && finalNow !== resolved;
  if (!errors.length && shouldRebuildSelection) {
    const rebuilt = reconstructMarketContract(game);
    if (rebuilt.recommendationRecovered) {
      return { ...rebuilt, policyRecalculated: true };
    }
  }
  // 선택 식별자·당시 시장확률만 어긋난 경우에는 현재 유효 배당으로 다시 고른다.
  // 출처·경기 ID·시각·스키마 오류가 하나라도 섞이면 이 완화 규칙을 적용하지 않는다.
  const recoverableErrors = errors.length > 0
    && errors.every((error) => RECOVERABLE_CONTRACT_ERRORS.has(error));
  if (recoverableErrors) {
    const rebuilt = reconstructMarketContract(game);
    if (rebuilt.recommendationRecovered) {
      return {
        ...rebuilt,
        policyRecalculated: true,
        contractRecoveredErrors: [...errors],
      };
    }
  }
  // 이전 정책이 1.50 미만을 제외해 남긴 정상 withhold는 새 정책에서 복구한다.
  if (!errors.length && raw.action === "withhold" && eligibleNow.length &&
      (raw.gate_codes || []).some((code) =>
        ["no_eligible_market_reference", "minimum_recommendation_odds"].includes(code))) {
    const rebuilt = reconstructMarketContract(game);
    if (rebuilt.recommendationRecovered) {
      return { ...rebuilt, policyRecalculated: true };
    }
  }
  return { raw, errors, resolved, reconstructed: false };
};

const withholdReasonsFor = (game, contract, action) => {
  if (action !== "withhold") return [];
  if (contract.errors.length) {
    return [...new Set(contract.errors)].map((error) => CONTRACT_ERROR_REASONS[error] || {
      title: "판정 자료를 검증하지 못함",
      body: `검증 코드 ${error} 문제를 해결하기 전에는 선택과 확률을 신뢰할 수 없어 가지 않습니다.`,
    });
  }

  const options = game?.options || [];
  if (!options.length) {
    return [{
      title: "발매 배당이 아직 없음",
      body: "실제 구매 배당과 시장확률이 없어 적중 가능성·기대손익·정확한 기준점을 비교할 수 없습니다. 가격이 나온 뒤 다시 계산해야 하므로 지금은 가지 않습니다.",
    }];
  }
  const priced = options.filter((option) => {
    const price = finite(option?.["배당"]);
    const probability = finite(option?.["시장확률"]);
    return price > 1 && probability > 0 && probability < 1;
  });
  if (!priced.length) {
    return [{
      title: "배당 또는 시장확률이 불완전함",
      body: "선택지는 있지만 유효한 배당과 시장확률이 한 쌍으로 갖춰지지 않았습니다. 잘못된 가격으로 적중률을 계산할 수 있어 가지 않습니다.",
    }];
  }
  if (priced.every((option) => String(option?.market || "").trim() === "홀짝")) {
    return [{
      title: "검증되지 않은 홀짝 마켓만 남음",
      body: "홀짝은 팀 전력·선발·득점분포 신호가 결과 방향으로 충분히 검증되지 않았습니다. 분석 없이 찍는 선택에 가까워 자동 픽으로 가지 않습니다.",
    }];
  }
  if (priced.every((option) => finite(option?.["배당"]) >= 2.2)) {
    return [{
      title: "남은 선택이 모두 2.20배 이상임",
      body: "현재 운영 백테스트에서 손실이 급격히 커진 가격대만 남았습니다. 높은 환급보다 실패 위험 증가가 더 커 자동 픽으로 가지 않습니다.",
    }];
  }
  return [{
    title: "현재 안전조건을 만족하는 선택이 없음",
    body: "배당은 있지만 마켓 유효성·가격 범위·동일 마켓 최유력 조건을 함께 통과한 선택이 없습니다. 어느 조건을 어겼는지 확정되기 전에는 가지 않습니다.",
  }];
};

/** v2 판정 스냅샷과 현재 선택지가 정확히 하나로 맞을 때만 반환한다. */
export function resolveDecisionOption(game, options = game?.options || []) {
  const scopedGame = options === game?.options ? game : { ...game, options };
  const contract = snapshotContract(scopedGame);
  return contract.errors.length ? null : contract.resolved;
}

/**
 * 화면에서 읽는 유일한 판정 모델. 클라이언트가 추천이나 AI 확률을 새로 만들지 않는다.
 */
export function buildDecisionViewModel(game, option = null) {
  const contract = snapshotContract(game);
  const raw = contract.raw;
  const liveRevisionChanged = game?._liveOddsChanged === true;
  const rawSelectionId = raw?.selection_id || null;
  const optionSelectionId = option?.selection_id || null;
  const selectionMatches = !!option && contract.resolved === option && (
    contract.reconstructed ||
    (rawSelectionId === optionSelectionId && raw?.offer_id === option?.offer_id)
  );
  const contractValid = contract.errors.length === 0;
  const resolvedOdds = finite(contract.resolved?.["배당"]);
  const recommendationEligible = contract.resolved && resolvedOdds !== null
    ? finalRecommendedSelection(game?.options || []) === contract.resolved
    : null;
  const reversal = recommendationEligible
    && qualifiedUnderdogSelections(game?.options || []).includes(contract.resolved);
  const recommendationTier = recommendationEligible
    ? reversal ? "reversal"
      : (recommendationPriority(contract.resolved) === 1 ? "primary" : "fallback")
    : null;
  const action = contractValid && raw.action === "market_reference" && game?._liveStarted
    ? "closed"
    : contractValid && raw.action === "market_reference" && liveRevisionChanged
    ? "recalculating"
    : contractValid && raw.action === "market_reference" && selectionMatches
      ? "market_reference" : "withhold";

  const market = action === "market_reference"
    ? finite(raw?.probability?.market) : null;
  const modelStatus = raw?.model?.status || "unavailable";
  const validatedEdge = raw?.model?.validated_edge === true
    && raw?.model?.promotion_gate === "passed"
    && !!raw?.model?.operating_version
    && PROMOTED_ARTIFACT_HASHES.has(raw?.model?.artifact_hash);
  const policyAuthorized = raw?.model?.policy_authorized === true
    && POLICY_AUTHORIZED_MODELS.has(raw?.model?.operating_version);
  const canApply = modelStatus === "operational"
    && raw?.model?.promotion_gate === "passed"
    && (validatedEdge || policyAuthorized);
  const aiCandidate = action === "market_reference"
    ? finite(raw?.probability?.ai_candidate) : null;
  // 검증되지 않은 AI가 final을 바꿔 담은 잘못된 JSON도 화면에서 fail-close한다.
  const finalProbability = canApply
    ? finite(raw?.probability?.final)
    : market;
  const appliedDelta = canApply
    ? finite(raw?.probability?.ai_delta_applied) : 0;

  const stages = normalizeStages(raw?.stages, contractValid ? option : null);
  const evidence = uniqueById(raw?.evidence?.length ? raw.evidence : fallbackEvidence(game, option))
    .map((row) => ({
      ...(AI_EVIDENCE_CATALOG[row.id] || {}),
      ...row,
      display_status: row.display_status || usageStatus(row),
    }));
  const counts = { used: 0, shadow: 0, context_only: 0, ignored: 0, missing: 0 };
  evidence.forEach((row) => { counts[row.display_status] = (counts[row.display_status] || 0) + 1; });

  return {
    action,
    option: action === "market_reference" ? option : null,
    selectionId: action === "market_reference" ? optionSelectionId || rawSelectionId : null,
    asOf: raw?.as_of || null,
    probability: {
      market,
      aiCandidate,
      aiDeltaCandidate: market !== null && aiCandidate !== null ? aiCandidate - market : null,
      aiDeltaApplied: appliedDelta,
      final: finalProbability,
      basis: finalProbability !== null
        ? (validatedEdge ? "validated_ai_residual"
          : policyAuthorized ? raw?.probability?.basis || "internal-context-blend-v2"
          : "shin_market")
        : "unavailable",
    },
    model: {
      status: modelStatus,
      validatedEdge,
      policyAuthorized: canApply && policyAuthorized,
      operatingVersion: raw?.model?.operating_version || "shin-market-anchor-v1",
      residualVersion: raw?.model?.residual_version || null,
    },
    stages,
    evidence,
    evidenceCounts: counts,
    explanation: {
      kind: raw?.explanation?.kind || game?.["설명메타"]?.kind || "deterministic",
      affectsProbability: false,
    },
    sources: uniqueById(raw?.sources || []),
    audit: raw?.audit || null,
    contractReconstructed: contract.reconstructed === true,
    contractRecoveredErrors: contract.contractRecoveredErrors || [],
    liveOddsRecalculated: game?._liveOddsRecalculated === true,
    policyRecalculated: contract.policyRecalculated === true,
    recommendationEligible,
    recommendationPriority: recommendationTier,
    contractErrors: contract.errors,
    withholdReasons: withholdReasonsFor(game, contract, action),
    gateCodes: contract.errors.length
      ? ["invalid_decision_contract", ...contract.errors]
      : recommendationTier === "fallback"
        ? ["lower_odds_fallback"]
        : recommendationEligible === false
          ? ["not_auto_recommendable"]
        : raw?.gate_codes || (action === "withhold" ? ["no_operating_selection"] : []),
    staleReason: liveRevisionChanged ? "live_price_revision_changed" : null,
  };
}

export function decisionLabel(decision) {
  if (decision?.action === "closed") return "경기 시작 · 사전 판정 마감";
  if (decision?.action === "recalculating") return "배당 변경 · 재계산 대기";
  if (decision?.contractErrors?.length) return "판정 계약 오류 · 보류";
  if (decision?.action !== "market_reference") return "비교 후보 보류";
  if (decision?.recommendationPriority === "reversal") {
    return "이전 이변 판정 · 재계산 필요";
  }
  if (decision?.recommendationPriority === "fallback") {
    return "예상 적중 비교 · 1.50 미만 보조";
  }
  if (decision?.policyRecalculated && decision?.recommendationEligible) {
    return "예상 적중 비교 · 1.50~2.20 우선";
  }
  if (decision?.liveOddsRecalculated) return "예상 적중 비교 · 실시간 재계산";
  if (decision?.contractReconstructed) return "예상 적중 비교 · 자동 복구";
  return decision?.model?.validatedEdge ? "검증 AI 적중 판정" : "예상 적중 비교";
}
