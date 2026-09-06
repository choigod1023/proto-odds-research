import PickProbabilities from "./PickProbabilities.jsx";
import { odds, pct } from "../lib/fmt.js";

/** Detail-only presentation: one score, one prediction, one probability block. */
export default function MatchDetailHeader({ game, score, status, phase, option, openingProbability,
  estimate, message, capturedAt, recommended, outcome, selected, children, predictionTitle }) {
  return <div className="match-detail-summary">
    <header className="detail-scoreboard" aria-label="경기 점수판">
      <div className="detail-match-meta"><span>{game.league} · {game.date}</span><b>{status}</b></div>
      <div className="detail-scoreline"><strong>{game.home}</strong>
        <span className="detail-score" aria-label={score ? `점수 ${score[0]} 대 ${score[1]}` : phase === "upcoming" ? "경기 시작 전" : "점수 미확인"}>{score ? `${score[0]} : ${score[1]}` : phase === "upcoming" ? "vs" : "—"}</span>
        <strong>{game.away}</strong></div>
      {children}
    </header>
    <section className="detail-prediction" aria-label="경기 예측">
      <div className="detail-prediction-labels"><h3>{predictionTitle || (option ? "경기 전 예측 픽" : "예측 정보")}</h3>
        {recommended && <span className="recommendation-badge">오늘의 추천 픽</span>}
        {selected && <span className="selection-badge">✓ 내 선택</span>}</div>
      {option ? <p className="detail-pick">{option.market} {option.label} · <b>{option.선택}</b> <span>배당 {odds(option.배당)}</span></p>
        : <p>사전 예측 기록 없음</p>}
      {phase === "finished" ? <>
        <p><span className={`result-badge is-${outcome.state}`}>{outcome.label}</span></p>
        <p>경기 전 적중 확률 {Number.isFinite(openingProbability) ? pct(openingProbability) : "기록 없음"}</p>
        {outcome.source === "score" && <p className="review-note">종료 점수 기준 판정이며 공식 정산이 확인되면 반영됩니다.</p>}
      </> : <PickProbabilities openingProbability={openingProbability} estimate={estimate} phase={phase} message={message} />}
      {capturedAt && <small className="detail-record-time">예측 시각 {new Date(capturedAt).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })} KST</small>}
    </section>
  </div>;
}
