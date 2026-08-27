import { decisionLabel } from "../lib/decision-view-model.js";

const reasonParts = (reason) => {
  const [title, ...body] = String(reason).split(" — ");
  return body.length
    ? { title, body: body.join(" — ") }
    : { title: "경기력 근거", body: title };
};

export default function PredictionPanel({ analysis }) {
  if (!analysis) return null;
  const { prediction, reasons, cautions, signalSummary, decision } = analysis;
  const probability = prediction?.probability == null ? null : Math.round(prediction.probability * 100);
  const label = decisionLabel(decision);
  const summary = signalSummary?.narrative || (
    decision?.action === "market_reference"
      ? decision?.recommendationPriority === "reversal"
        ? "구조 모델은 선택 방향을 전환했으며, 표시 확률은 해당 역배의 시장확률입니다."
        : "현재 검증된 운영값은 시장확률이며 구조 AI는 연구값으로만 비교합니다."
      : "운영 조건을 통과한 선택이 없어 팀 방향과 확률을 새로 만들지 않습니다."
  );
  return (
    <section className="prediction-card" aria-label="경기 예상과 경기력 해석">
      <header className="prediction-head">
        <div>
          <p className="prediction-label">{label} · 참고용</p>
          <h3>{prediction?.headline || "예측 자료 확인 중"}</h3>
          <p>{summary}</p>
        </div>
        {probability !== null && <div className="prediction-probability"><b>{probability}%</b><span>{decision?.model?.validatedEdge ? "AI 최종확률" : "Shin 시장확률"}</span></div>}
      </header>
      <div className="prediction-body">
        <div>
          <h4>판정과 구분된 경기 자료</h4>
          <p className="performance-intro">아래 자료는 설명 근거이며, AI가 최종 확률에 사용했는지는 ‘AI 판정’ 탭에서 따로 확인합니다.</p>
          <ul className="performance-reasons">
            {reasons.map((reason) => {
              const item = reasonParts(reason);
              return <li key={reason}><strong>{item.title}</strong><p>{item.body}</p></li>;
            })}
          </ul>
        </div>
      </div>
      {!!cautions?.length && <div className="prediction-caution"><b>확인할 변수</b><span>{cautions.join(" ")}</span></div>}
      <footer>{decision?.recommendationPriority === "reversal"
        ? "전환 픽은 시장 최유력이 아닙니다. 표시 확률과 위험을 함께 확인해야 합니다."
        : "시장 기준 비교와 검증된 AI 우위는 같은 뜻이 아닙니다. 구매 판단은 직접 합니다."}</footer>
    </section>
  );
}
