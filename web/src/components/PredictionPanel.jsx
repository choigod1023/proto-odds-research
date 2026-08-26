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
    <section className="prediction-card" aria-label="경기 모델 판정과 경기력 해석">
      <header className="prediction-head">
        <div>
          <p className="prediction-label">{prediction?.outcome ? "경기 모델 최종 선택" : "경기 모델 판정"}</p>
          <h3>{prediction?.headline || "경기 모델 추천 제외"}</h3>
          <p>{signalSummary?.narrative || (prediction?.outcome
            ? "표시된 모델확률과 경기 정보를 기준으로 이 선택을 확정했다."
            : prediction?.modelAvailable
              ? "경기 모델 추천 기준을 통과한 선택이 없다."
              : "이 경기에는 모델확률이 없다.")}</p>
        </div>
        {probability !== null && <div className="prediction-probability"><b>{probability}%</b><span>모델확률</span></div>}
      </header>
      <div className={`prediction-body ${featuredPlayers?.length ? "has-players" : ""}`}>
        <div>
          <h4>판정 근거</h4>
          <p className="performance-intro">최근 공격·수비 기록과 선수 정보를 항목별로 정리했다.</p>
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
      {!!cautions?.length && <div className="prediction-caution"><b>미반영 정보</b><span>{cautions.join(" ")}</span></div>}
      <footer>경기 모델 선택 여부는 수치 기준으로 확정한다. 모델확률은 경기 결과 보장이 아니다.</footer>
    </section>
  );
}
