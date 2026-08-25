const reasonParts = (reason) => {
  const [title, ...body] = String(reason).split(" — ");
  return body.length
    ? { title, body: body.join(" — ") }
    : { title: "경기력 근거", body: title };
};

export default function PredictionPanel({ analysis }) {
  if (!analysis) return null;
  const { prediction, reasons, cautions, featuredPlayers, playerNotes, signalSummary } = analysis;
  const probability = prediction?.probability == null ? null : Math.round(prediction.probability * 100);
  return (
    <section className="prediction-card" aria-label="경기 예상과 경기력 해석">
      <header className="prediction-head">
        <div>
          <p className="prediction-label">통합 추천 근거 · 참고용</p>
          <h3>{prediction?.headline || "예측 자료 확인 중"}</h3>
          <p>추천은 모델 확률, 아래 표시는 최근 경기력 신호 — 서로 다른 질문을 섞지 않습니다</p>
        </div>
        {probability !== null && <div className="prediction-probability"><b>{probability}%</b><span>모델 예상</span></div>}
      </header>
      {signalSummary && <div className="border-t border-rule2 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
          <b className="text-ink">신호 합의: {signalSummary.state}</b>
          <span className="rounded border border-rule px-2 py-0.5">모델 · {signalSummary.modelSide}</span>
          {signalSummary.signals.map((signal) => <span key={signal.label}
            className={"rounded border px-2 py-0.5 " + (signal.side && signal.side !== signalSummary.modelSide ? "border-sev3 text-sev3" : "border-rule text-ink2")}>
            {signal.label} · {signal.side || "중립"}
          </span>)}
        </div>
        <p className="mt-2 mb-0 text-[11px] leading-[1.65] text-ink3">{signalSummary.explanation}</p>
      </div>}
      <div className={`prediction-body ${featuredPlayers?.length ? "has-players" : ""}`}>
        <div>
          <h4>경기 흐름을 이렇게 봤습니다</h4>
          <p className="performance-intro">최근 경기에서 반복된 공격·수비 흐름과 선수 변수를 중심으로 읽었습니다.</p>
          <ul className="performance-reasons">
            {reasons.map((reason) => {
              const item = reasonParts(reason);
              return <li key={reason}><strong>{item.title}</strong><p>{item.body}</p></li>;
            })}
          </ul>
        </div>
        {!!featuredPlayers?.length && <div className="player-watch">
          <h4>주요 선수</h4>
          <div className="player-watch-grid">
            {featuredPlayers.map((player) => <article key={`${player.team}-${player.name}`}>
              <small>{player.team} · {player.role}</small>
              {player.profileUrl
                ? <a href={player.profileUrl} target="_blank" rel="noreferrer">{player.name}</a>
                : <b>{player.name}</b>}
              <p>{player.detail}</p>
            </article>)}
          </div>
          {!!playerNotes?.length && <ul className="player-notes">{playerNotes.map((note) => <li key={note}>{note}</li>)}</ul>}
        </div>}
      </div>
      {!!cautions?.length && <div className="prediction-caution"><b>확인할 변수</b><span>{cautions.join(" ")}</span></div>}
      <footer>예상은 판단 자료이며 구매를 대신 결정하지 않습니다.</footer>
    </section>
  );
}
