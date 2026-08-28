import { decisionLabel } from "../lib/decision-view-model.js";

const reasonParts = (reason) => {
  const [title, ...body] = String(reason).split(" — ");
  return body.length
    ? { title, body: body.join(" — ") }
    : { title: "경기력 근거", body: title };
};

export default function PredictionPanel({ analysis }) {
  if (!analysis) return null;
  const { prediction, reasons, cautions, signalSummary, decision, commentary } = analysis;
  const probability = prediction?.probability == null ? null : Math.round(prediction.probability * 100);
  const label = decisionLabel(decision);
  const summary = signalSummary?.narrative || (
    decision?.action === "market_reference"
      ? "현재 검증된 운영값은 시장확률이며 구조 AI는 반대 신호를 설명하는 연구값으로만 비교합니다."
      : "아래 위험이 해소되지 않아 이 픽은 추천하지 않습니다."
  );
  return (
    <section className="prediction-card" aria-label="경기 모델 판정과 경기력 해석">
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
          <p className="performance-intro">설명 자료와 반대 신호를 함께 보며, AI가 사용한 값은 ‘픽 근거·수식’ 탭에서 확인합니다.</p>
          <ul className="performance-reasons">
            {reasons.map((reason) => {
              const item = reasonParts(reason);
              return <li key={reason}><strong>{item.title}</strong><p>{item.body}</p></li>;
            })}
          </ul>
        </div>
      </div>
      {decision?.action === "withhold" && !!decision.withholdReasons?.length && (
        <div className="prediction-caution">
          <b>이 픽을 가지 말아야 하는 이유</b>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {decision.withholdReasons.map((reason) => (
              <li key={`${reason.title}-${reason.body}`}>
                <strong>{reason.title}</strong> · {reason.body}
              </li>
            ))}
          </ul>
        </div>
      )}
      {commentary && <div className="prediction-commentary">
        <b>해설 정리</b>
        <p>{commentary}</p>
        <small>수집된 사실을 바꾸지 않고 LLM이 문장만 다듬었습니다.</small>
      </div>}
      {!!cautions?.length && <div className="prediction-caution"><b>반대 근거·변수</b><span>{cautions.join(" ")}</span></div>}
      <footer>예상 적중확률은 검증된 보정만 반영하며, 보정이 없으면 시장 기준선으로 복귀합니다. 구매 판단은 직접 합니다.</footer>
    </section>
  );
}
