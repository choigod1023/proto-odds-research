export default function PredictionPanel({ analysis }) {
  if (!analysis) return null;
  const { prediction, reasons, cautions, featuredPlayers, playerNotes } = analysis;
  const probability = prediction?.probability == null ? null : Math.round(prediction.probability * 100);
  return (
    <section className="prediction-card" aria-label="경기 예상과 경기력 해석">
      <header className="prediction-head">
        <div>
          <p className="prediction-label">경기 예상 · 참고용</p>
          <h3>{prediction?.headline || "예측 자료 확인 중"}</h3>
          <p>최근 경기력과 현재 선수 정보를 함께 읽은 결과</p>
        </div>
        {probability !== null && <div className="prediction-probability"><b>{probability}%</b><span>모델 예상</span></div>}
      </header>
      <div className={`prediction-body ${featuredPlayers?.length ? "has-players" : ""}`}>
        <div>
          <h4>경기 흐름을 이렇게 봤습니다</h4>
          <ol className="performance-reasons">
            {reasons.map((reason, index) => <li key={reason}><span>{index + 1}</span><p>{reason}</p></li>)}
          </ol>
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
