import { decisionLabel } from "../lib/decision-view-model.js";

const reasonParts = (reason) => {
  const [title, ...body] = String(reason).split(" — ");
  return body.length
    ? { title, body: body.join(" — ") }
    : { title: "경기력 근거", body: title };
};

const scoreNumber = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(1) : "-";
};

function ScoreForecast({ forecast }) {
  if (!forecast || forecast.status !== "shadow") return null;
  const expected = forecast.expected_scores || {};
  const top = Array.isArray(forecast.top_scorelines)
    ? forecast.top_scorelines.slice(0, 3)
    : [];
  const unit = forecast.contract?.score_unit_label || expected.unit || "득점";
  return (
    <div className="mt-3 rounded-md border border-rule2 bg-panel px-3.5 py-3"
      aria-label="스코어 분포 전망">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <b className="text-[12px] text-ink">스코어 흐름 전망</b>
          <p className="mt-1 text-[11px] leading-5 text-ink3">
            현재 팀 전력 분포의 평균은 홈 {scoreNumber(expected.home)} · 원정 {scoreNumber(expected.away)} {unit}입니다.
          </p>
        </div>
        <span className="rounded border border-rule2 bg-paper px-2 py-1 text-[10px] text-ink3">
          연구값 · 추천 확률 미반영
        </span>
      </div>
      {!!top.length && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {top.map((row) => (
            <span key={`${row.home_score}-${row.away_score}`}
              className="rounded border border-rule2 bg-paper px-2 py-1 text-[10.5px] text-ink2">
              {row.home_score}:{row.away_score} · {Math.round(Number(row.probability || 0) * 100)}%
            </span>
          ))}
        </div>
      )}
      <p className="mt-2 text-[10px] leading-4 text-ink3">
        선발·결장별 영향량은 사전 원장으로 충분히 검증된 뒤 시나리오에 반영합니다. 임의 난수나 ‘미래상수’는 예측 입력으로 쓰지 않습니다.
      </p>
    </div>
  );
}

function TeamPreview({ previews }) {
  if (!previews?.some((preview) => preview.characteristics.length || preview.players.length)) return null;
  return <div className="mt-3">
    <h4 className="mb-2 text-[11px] font-semibold tracking-[.04em] text-ink3">팀·핵심 선수 프리뷰</h4>
    <div className="grid gap-2 sm:grid-cols-2">
      {previews.map((preview) => <article key={preview.side} className="rounded-md border border-rule2 bg-paper px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <b className="text-[12px] text-ink">{preview.team}</b>
          <span className="text-[9.5px] text-ink3">{preview.side === "home" ? "홈" : "원정"}</span>
        </div>
        {!!preview.characteristics.length && <ul className="mt-1.5 space-y-1 text-[10.5px] leading-4 text-ink2">
          {preview.characteristics.slice(0, 3).map((trait) => <li key={trait}>· {trait}</li>)}
        </ul>}
        {!!preview.players.length && <div className="mt-2 border-t border-rule2 pt-2">
          {preview.players.map((player) => <div key={player.name} className="mb-1.5 last:mb-0">
            <div className="flex flex-wrap items-baseline gap-1 text-[10.5px]"><b className="text-ink">{player.name}</b><span className="text-ink3">{player.characteristic}</span></div>
            {!!player.facts.length && <p className="tnum mt-0.5 text-[9.5px] text-ink3">{player.facts.join(" · ")}</p>}
          </div>)}
        </div>}
        {!!preview.unavailable.length && <p className="mt-2 border-t border-rule2 pt-1.5 text-[9.5px] leading-4 text-ink3">
          출전 변수 · {preview.unavailable.map((player) => `${player.name}${player.status ? `(${player.status})` : ""}`).join(" · ")}
        </p>}
      </article>)}
    </div>
    <p className="mt-1.5 text-[9.5px] text-ink3">공식 시즌 기록과 최근 흐름을 요약한 설명이며, 검증 전에는 추천 확률을 직접 바꾸지 않습니다.</p>
  </div>;
}

export default function PredictionPanel({ analysis, scoreForecast }) {
  if (!analysis) return null;
  const { prediction, reasons, cautions, signalSummary, decision, commentary } = analysis;
  const probability = prediction?.probability == null ? null : Math.round(prediction.probability * 100);
  const label = decisionLabel(decision);
  const summary = signalSummary?.narrative || (
    decision?.action === "market_reference"
      ? "추천 방향과 함께 최근 경기력·선수 정보·반대 신호를 비교해 확인하세요."
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
        {probability !== null && <div className="prediction-probability"><b>{probability}%</b><span>{decision?.model?.validatedEdge ? "AI 최종확률" : "배당 기반 시장확률"}</span></div>}
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
      <TeamPreview previews={analysis.teamPreviews} />
      <ScoreForecast forecast={scoreForecast} />
      {!!cautions?.length && <div className="prediction-caution"><b>반대 근거·변수</b><span>{cautions.join(" ")}</span></div>}
      <footer>예상 적중확률은 실제 배당에 포함된 마진을 제거한 값을 출발점으로 삼고, 검증된 보정만 추가합니다. 구매 판단은 직접 합니다.</footer>
    </section>
  );
}
