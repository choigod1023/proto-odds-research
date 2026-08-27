import { eligibleAutoSelections } from "./recommendation-policy.js";

const finite = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

export const DECISION_SCHEMA = "decision-snapshot-v2";
// 검증을 마쳐 배포된 artifact가 생길 때만 해시를 코드 리뷰로 추가한다.
const PROMOTED_ARTIFACT_HASHES = new Set();

export const AI_STAGE_CATALOG = [
  {
    id: "market",
    label: "시장 기준선",
    status: "used",
    summary: "동일 시점 배당을 Shin 방식으로 마진 제거해 최종 확률의 기준으로 씁니다.",
  },
  {
    id: "structured_ai",
    label: "수치 AI 후보",
    status: "shadow",
    summary: "현재 팀·득점분포 모델과 시장의 차이를 잔차 후보로 검증하며, 통과 전에는 0%p만 반영합니다.",
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
      ? (finite(option?.["모델확률"]) !== null ? "shadow" : "missing")
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
  const eligible = eligibleAutoSelections(game?.options || [])
    .filter((option) => {
      const probability = finite(option?.["시장확률"]);
      return probability !== null && probability > 0 && probability < 1;
    });
  const resolved = eligible.sort((a, b) =>
    finite(b?.["시장확률"]) - finite(a?.["시장확률"]) ||
    finite(a?.["배당"]) - finite(b?.["배당"]) ||
    String(a?.market || "").localeCompare(String(b?.market || "")) ||
    String(a?.label || "").localeCompare(String(b?.label || "")) ||
    String(a?.["선택"] || "").localeCompare(String(b?.["선택"] || ""))
  )[0] || null;
  const market = finite(resolved?.["시장확률"]);
  const shadow = finite(resolved?.["모델확률"]);
  const recalculatedAt = game?._liveOddsRecalculatedAt || null;
  return {
    reconstructed: true,
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
        ? [game?._liveOddsRecalculated
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
  return { raw, errors, resolved, reconstructed: false };
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
  const canApply = modelStatus === "operational" && validatedEdge;
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
        ? (canApply ? "validated_ai_residual" : "shin_market")
        : "unavailable",
    },
    model: {
      status: modelStatus,
      validatedEdge: canApply,
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
    liveOddsRecalculated: game?._liveOddsRecalculated === true,
    contractErrors: contract.errors,
    gateCodes: contract.errors.length
      ? ["invalid_decision_contract", ...contract.errors]
      : raw?.gate_codes || (action === "withhold" ? ["no_operating_selection"] : []),
    staleReason: liveRevisionChanged ? "live_price_revision_changed" : null,
  };
}

export function decisionLabel(decision) {
  if (decision?.action === "closed") return "경기 시작 · 사전 판정 마감";
  if (decision?.action === "recalculating") return "배당 변경 · 재계산 대기";
  if (decision?.contractErrors?.length) return "판정 계약 오류 · 보류";
  if (decision?.action !== "market_reference") return "비교 후보 보류";
  if (decision?.liveOddsRecalculated) return "시장 기준 비교 · 실시간 재계산";
  if (decision?.contractReconstructed) return "시장 기준 비교 · 자동 복구";
  return decision?.model?.validatedEdge ? "검증 AI 판정" : "시장 기준 비교";
}
