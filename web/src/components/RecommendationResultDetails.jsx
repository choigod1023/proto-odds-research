import MatchDetailHeader from './MatchDetailHeader.jsx';
import { SPORT_NAMES, accuracyTime } from '../lib/overall-accuracy.js';
import { pct } from '../lib/fmt.js';

const probability = value => Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
const time = value => `${accuracyTime(value)}${Number.isFinite(Date.parse(value)) ? ' KST' : ''}`;

/** Historical details always use the archived pick, not today's revised analysis. */
export default function RecommendationResultDetails({entry}) {
  const p = probability(entry.probability);
  const marketP = probability(entry.market_prob);
  const game = {...entry, date:time(entry.kickoff_at)};
  const matched = entry.game;
  const year = new Date(entry.kickoff + 9*3600000).getUTCFullYear();
  const score = matched?.status === '정산' && Number(matched.year) === year
    && Array.isArray(matched.score) && matched.score.length === 2
    && matched.score.every(v=>Number.isFinite(v) && v>=0) ? matched.score : null;
  const option = {market:entry.market,label:entry.market_label || '',선택:entry.sel,
    배당:Number.isFinite(entry.odds) && entry.odds>1 ? entry.odds : null};
  return <div className="match-detail-view">
    <p>{SPORT_NAMES[entry.sport] || SPORT_NAMES.unknown} · {entry.league || '리그 정보 없음'}</p>
    <MatchDetailHeader game={game} score={score} status="공식 결과 확정" phase="finished"
      option={option} openingProbability={p} capturedAt={entry.published_at}
      predictionTitle="당시 보존된 추천 픽"
      outcome={{...entry.outcome,label:entry.outcome.state==='hit'?'적중':'실패'}} />
    {!score && <p className="review-note">연결된 최종 점수 기록이 없습니다. 적중·실패는 해당 픽의 공식 정산 결과입니다.</p>}
    <section className="saved-snapshot" aria-label="추천 당시 기록 상세">
      <h3>추천 당시 기록</h3>
      <p>당시 시장 확률 {marketP===null?'기록 없음':pct(marketP)}</p>
      <p>예측 확률 출처: {entry.probability_source || '기록 없음'}</p>
      <p>예측 방법: {entry.probability_method || '기록 없음'}</p>
      <p>추천 정책: {entry.policy || '기록 없음'}</p>
      <p>회차 {entry.round ?? '기록 없음'} · 경기 번호 {entry.game_no ?? '기록 없음'}</p>
      <p>추천 게시 시각: {time(entry.published_at)}</p>
      <p>추천 보존 시각: {time(entry.recorded_at)}</p>
      <p>공식 정산 기록 시각: {time(entry.settled_at)}</p>
      <p>사전 기록 ID: <span className="recommendation-result-record-id">{entry.id}</span></p>
      <p className="review-note">경기 시작 30분 전 보존된 추천입니다. 당시 확률은 예측값이며 수익률을 보장하지 않습니다. 이 기록에 보존되지 않은 선발·라인업·분석 근거는 현재 정보로 대체하지 않습니다.</p>
    </section>
  </div>;
}
