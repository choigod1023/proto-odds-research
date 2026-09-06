import { AI_STAGE_CATALOG, AI_STATUS_LABELS, evidenceUsageRows } from "../lib/decision-view-model.js";

const isNumeric = (value) => value !== null && value !== undefined && value !== ""
  && Number.isFinite(Number(value));

const percent = (value, digits = 1) => isNumeric(value)
  ? `${(Number(value) * 100).toFixed(digits)}%` : "–";

const signedPoint = (value) => isNumeric(value)
  ? `${Number(value) >= 0 ? "+" : ""}${(Number(value) * 100).toFixed(1)}%p` : "–";

const reasonText = {
  model_not_promoted: "미래 홀드아웃에서 아직 승격되지 않음",
  no_first_seen_prediction_ledger: "최초 관측 시각이 고정된 예측 원장이 없음",
  market_anchor_policy: "현재 운영식은 시장 기준",
  not_in_operating_formula: "운영 수식에 아직 포함되지 않음",
  context_not_probability: "확률이 아니라 확인용 문맥",
  diagnostic_not_promoted: "교차 마켓 진단은 연구 단계",
  diagnostic_only: "선택을 바꾸지 않는 진단값",
};

const gateText = {
  no_eligible_market_reference: "유효한 배당·시장확률을 갖춘 자동 비교 후보가 없습니다.",
  lower_odds_fallback: "최종 적중확률은 가장 높지만 1.50 미만 저배당이라 적중 시 수익폭이 작습니다.",
  qualified_market_reversal: "이전 역배 전환 판정입니다. 현재 정책에서는 시장 최유력으로 다시 계산합니다.",
  not_auto_recommendable: "시장 분석은 유지하지만 자동 투입 안전조건을 통과하지 못했습니다.",
  no_operating_selection: "운영 판정이 없어 선택을 보류합니다.",
  invalid_decision_contract: "판정 자료의 시각·식별자·스키마가 맞지 않아 보류합니다.",
};

const badgeClass = (status) => `ai-status ai-status-${status || "missing"}`;

export function AiMethodology({ id = "ai-method", showLink = true }) {
  return (
    <section id={id} className="ai-method" aria-labelledby={`${id}-title`}>
      <div className="ai-method-heading">
        <div>
          <p>현재 AI 사용 범위</p>
          <h2 id={`${id}-title`}>최종 추천은 예상 적중확률로 고르고, 검증된 AI만 확률에 반영합니다</h2>
        </div>
        {showLink && <a href="research.html#ai-model">검증 방법 보기</a>}
      </div>
      <ol className="ai-stage-grid">
        {AI_STAGE_CATALOG.map((stage, index) => (
          <li key={stage.id}>
            <div className="ai-stage-top">
              <span className="tnum">{String(index + 1).padStart(2, "0")}</span>
              <b>{stage.label}</b>
              <span className={badgeClass(stage.status)}>{AI_STATUS_LABELS[stage.status]}</span>
            </div>
            <p>{stage.summary}</p>
          </li>
        ))}
      </ol>
      <p className="ai-method-foot">
        경기별 방향은 2.20 미만 유효 후보의 최종 적중확률로 고릅니다. 1.50 경계는 저배당 수익폭 표시이며, 다폴 조합의 배당칸은 별도로 1.50~2.20을 사용합니다. 검증된 AI 보정이 없으면 배당 기반 시장확률로 복귀합니다.
      </p>
    </section>
  );
}

function ProbabilityProvenance({ decision }) {
  const probability = decision?.probability || {};
  const stale = ["recalculating", "closed"].includes(decision?.action);
  return (
    <div className="probability-provenance" role="group" aria-label="최종 확률 계산 경로">
      <div><span>배당 기반 확률</span><b>{stale ? "재계산 대기" : percent(probability.market)}</b></div>
      <span className="probability-arrow" aria-hidden="true">+</span>
      <div><span>AI 반영</span><b>{stale ? "–" : signedPoint(probability.aiDeltaApplied)}</b></div>
      <span className="probability-arrow" aria-hidden="true">=</span>
      <div><span>최종 확률</span><b>{stale ? "숨김" : percent(probability.final)}</b></div>
    </div>
  );
}

const usageReason = (evidence) => {
  const row = evidenceUsageRows(evidence).find((item) => item?.reason_code);
  return reasonText[row?.reason_code] || row?.reason_code || null;
};

export function AiDecisionPath({ decision }) {
  if (!decision) return null;
  const counts = decision.evidenceCounts || {};
  const stamp = decision.asOf ? new Date(decision.asOf) : null;
  const asOf = stamp && !Number.isNaN(stamp.getTime())
    ? stamp.toLocaleString("ko-KR", {
      timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }) : null;
  const sourceById = new Map((decision.sources || []).map((source) => [source.id, source]));
  const visibleGate = (decision.gateCodes || []).find((code) => gateText[code]);
  return (
    <div className="ai-decision-path">
      <ProbabilityProvenance decision={decision} />
      {decision.action === "recalculating" && (
        <p className="ai-revision-warning" role="status" aria-live="polite">
          실시간 배당이 바뀌어 이전 확률과 해설을 숨겼습니다. 다음 계산이 끝나면 같은 시점 값으로 다시 표시합니다.
        </p>
      )}
      {decision.action === "closed" && (
        <p className="ai-revision-warning" role="status">
          실시간 중계에서 경기 시작을 확인해 이 판정은 더 이상 구매 후보로 사용하지 않습니다.
        </p>
      )}
      {decision.contractReconstructed && (
        <p className="ai-revision-warning" role="status">
          {decision.contractRecoveredErrors?.length
            ? "저장된 선택 식별자나 당시 확률이 현재 배당과 어긋나 이전 값을 버렸습니다. 현재 유효 배당의 최종 예상 적중확률로 자동 복구했으며, 이전 픽과 미검증 AI 값은 사용하지 않았습니다."
            : decision.policyRecalculated
            ? decision.recommendationPriority === "fallback"
              ? "현재 배당에서 1.50 미만 시장 최유력을 경기 방향으로 다시 판정했습니다."
              : "현재 배당에서 1.50~2.20 후보 중 최종 예상 적중확률이 가장 높은 선택으로 다시 판정했습니다."
            : decision.liveOddsRecalculated
            ? "실시간 배당으로 배당 기반 시장확률과 최종 판정을 다시 계산했습니다. 구조 AI와 이전 가격의 추천값은 반영하지 않았습니다."
            : "판정 원장이 늦게 갱신되어 현재 배당의 시장 최유력 후보를 안전 규칙으로 다시 계산했습니다. 구조 AI와 레거시 추천값은 반영하지 않았습니다."}
        </p>
      )}
      {(decision.action !== "withhold" && ["lower_odds_fallback", "qualified_market_reversal"].includes(visibleGate)) && visibleGate && (
        <p className="ai-revision-warning" role="status">{gateText[visibleGate]}</p>
      )}
      <div className="ai-stage-list" aria-label="AI 단계별 반영 상태">
        {decision.stages.map((stage) => (
          <article key={stage.id}>
            <div><b>{stage.label}</b><span className={badgeClass(stage.status)}>{AI_STATUS_LABELS[stage.status] || stage.status}</span></div>
          </article>
        ))}
      </div>
      <details className="evidence-usage">
        <summary>
          자료 사용 내역 · 최종 반영 {counts.used || 0} · 연구 중 {counts.shadow || 0} · 설명만 {counts.context_only || 0} · 미반영 {counts.ignored || 0} · 없음 {counts.missing || 0}
        </summary>
        <ul>
          {decision.evidence.map((evidence) => (
            <li key={evidence.id}>
              <span>
                <b>{evidence.label}</b>{usageReason(evidence) ? ` · ${usageReason(evidence)}` : ""}
                {(evidence.source_ids || []).map((sourceId) => {
                  const source = sourceById.get(sourceId);
                  if (!source) return null;
                  const label = ` · 출처 ${source.name || sourceId}`;
                  return source.url
                    ? <a key={sourceId} href={source.url} target="_blank" rel="noreferrer">{label}</a>
                    : <small key={sourceId}>{label}</small>;
                })}
              </span>
              <span className={badgeClass(evidence.display_status)}>{AI_STATUS_LABELS[evidence.display_status] || evidence.display_status}</span>
            </li>
          ))}
        </ul>
      </details>
      <p className="ai-decision-meta">
        생성형 AI 확률 영향 없음{asOf ? ` · 입력 컷오프 KST ${asOf}` : ""}
        {decision.audit?.pre_registered === false ? " · 사전등록 성과 집계 제외" : ""}
      </p>
    </div>
  );
}
