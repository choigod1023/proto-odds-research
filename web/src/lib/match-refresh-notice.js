import { decisionFrozen, gamePhase, scheduledAt } from './match-status.js';
import { recommendationOutcome } from './pick-result.js';

export const MATCH_LIST_REFRESH_MS = 60000;
const FINAL = new Set(['hit', 'miss', 'void']);
const validTime = value => value && Number.isFinite(Date.parse(value)) ? value : null;

export function outcomeDisplayLabel(outcome) {
  return outcome?.source === 'score' && ['hit', 'miss'].includes(outcome.state)
    ? `점수 기준 잠정 ${outcome.label}` : outcome?.label;
}

export function matchRefreshNotice(game, { now = Date.now(), phase = gamePhase(game, game?._liveState, now) } = {}) {
  if (!game) return null;
  const waiting = phase !== 'finished' && (game._liveOddsChanged === true || [
    'prediction_ledger_required', 'withheld_unrecorded_live_revision',
  ].includes(game.prediction_status));
  if (waiting && game.prediction_ledger_reason === 'ledger_conflict') {
    return { kind: 'blocked', title: '사전 원장 연결 확인 필요',
      body: '저장된 사전 기록과 새 판정이 충돌해 추천을 보류했습니다. 원장 연결이 복구되어야 다시 표시할 수 있으며, 단순 대기만으로 완료 시각을 보장할 수 없습니다.' };
  }
  if (waiting && (game._decisionFrozen || decisionFrozen(game, now))) {
    return { kind: 'frozen', title: '사전 픽 동결 · 기록 확인 필요',
      body: '경기 시작 30분 전부터 사전 픽을 고정합니다. 이후 배당으로 새 추천을 만들지 않으며, 마감 전에 저장된 기록만 표시합니다.' };
  }
  if (waiting && phase === 'upcoming') {
    const start = scheduledAt(game);
    return { kind: 'recalculating', title: '새 배당 판정 반영 대기',
      body: '새 배당 판정 → 사전 원장 저장 → 경기 목록 게시가 끝나면 추천을 다시 표시합니다. 그동안 이전 추천과 확률을 숨깁니다.',
      followUp: '화면이 열려 있으면 게시된 목록을 약 1분 간격으로 자동 확인합니다. 서버 처리 대기나 재시도가 있어 완료 시각은 확정할 수 없습니다.',
      observedAt: validTime(game._liveOddsRecalculatedAt),
      freezeAt: start == null ? null : new Date(start - 30 * 60000).toISOString() };
  }
  if (phase !== 'finished') return null;
  const pending = [];
  if (game.prediction_record && !FINAL.has(game.prediction_record.result)) pending.push('승패·승무패 픽');
  if (game.featured_pick_record && !FINAL.has(game.featured_pick_record.result?.result)) pending.push('경기 추천 1픽');
  const markets = game.market_prediction_record;
  if ((markets?.market_recommendations?.items || []).some(item =>
    !FINAL.has(markets.market_recommendation_results?.[item.offer_id]?.result))) pending.push('시장별 픽');
  if (!pending.length) return null;
  const outcome = recommendationOutcome(game);
  // A winner result says nothing about an independently pending total/handicap.
  const official = pending.length === 1 && pending[0] === '승패·승무패 픽'
    && outcome.source === 'official' && FINAL.has(outcome.state);
  return { kind: 'settlement', title: official ? '공식 결과 확인 · 성적 집계 반영 대기' : '경기 종료 · 공식 상품 정산 대기',
    body: `${pending.join(' · ')}의 원장 정산을 기다리고 있습니다. 공식 상품 결과가 저장된 사전 픽과 연결되고 새 성적이 게시되면 적중률·실현 ROI에 자동 반영합니다.`,
    followUp: '점수 기준 결과는 잠정 표시입니다. 다른 상품이나 기준점을 임의 정산하지 않으며, 미정산 픽은 실현 ROI 계산에서 제외합니다.' };
}
