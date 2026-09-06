import MatchDetailHeader from "./MatchDetailHeader.jsx";
import MatchProgress from "./MatchProgress.jsx";
import BaseballSituation from "./BaseballSituation.jsx";
import { estimateLiveProbability, settleBet } from "../lib/bet-ledger.js";
import { gamePhase, gameStatusLabel } from "../lib/match-status.js";

export default function RecordMatchDetails({ bet, live }) {
  const game = bet.game;
  const phase = gamePhase(game, live);
  const score = live?.status !== "BEFORE" && Number.isFinite(live?.home_score) && Number.isFinite(live?.away_score)
    ? [live.home_score, live.away_score] : null;
  const result = settleBet(bet, live);
  const estimate = estimateLiveProbability(bet, live);
  const option = {market:bet.selection.market,label:bet.selection.label,선택:bet.selection.choice,배당:bet.purchaseOdds};
  const outcome = estimate.outcome || {state:result || "pending",label:{hit:"적중",miss:"적중실패",void:"취소 · 무효"}[result] || "판정 확인 중",source:"score"};
  return <div className="match-detail-view">
    <MatchDetailHeader game={game} score={score} phase={phase} status={phase === "live" ? `LIVE · ${live?.status_text || "진행 중"}` : gameStatusLabel(game,live)}
      option={option} predictionTitle="내가 저장한 픽" openingProbability={bet.openingProbability}
      estimate={estimate} outcome={outcome} message="최신 경기 정보가 확인되면 현재 확률을 표시합니다.">
      <MatchProgress game={game} live={live} phase={phase} score={score} />
      {phase === "live" && game.sport === "bs" && <BaseballSituation live={live} />}
    </MatchDetailHeader>
    <section className="saved-snapshot" aria-label="선택 당시 기록">
      <h3>선택 당시 기록</h3>
      <p>저장 시각 {new Date(bet.selectionSnapshot?.selectedAt || bet.createdAt).toLocaleString("ko-KR",{timeZone:"Asia/Seoul"})} KST</p>
      {(bet.selectionSnapshot?.performance?.facts || []).map((fact,i)=><p key={i}>{fact}</p>)}
      {!bet.selectionSnapshot?.performance?.facts?.length && <p>당시 경기력 근거가 저장되지 않은 기록입니다.</p>}

    </section>
  </div>;
}
