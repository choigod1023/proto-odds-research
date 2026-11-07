import { apiUrl } from './api-origin.js';

export const SCORES_URL = apiUrl('/api/live-scores-summary');
export const ODDS_URL = apiUrl('/api/live-odds-summary');
export const LEGACY_SCORES_URL = apiUrl('/api/live-scores');
export const LEGACY_ODDS_URL = apiUrl('/api/live-odds');

const identity = (live) => [live?.game_id, live?.source, live?.start];

export function scoreDetailUrl(live, summary) {
  const fields = identity(live);
  if (!summary || !fields.every(value => typeof value === 'string' && value.length)) return null;
  const [game_id, source, start] = fields;
  return apiUrl('/api/live-scores-detail?' + new URLSearchParams({game_id, source, start}));
}

export function attachScoreDetail(live, response) {
  if (!live || !response?.detail || !identity(live).every((value, index) => value === response.identity?.[index])) return live;
  const { timeline, timeline_scope, period_scores } = response.detail;
  return {...live, timeline, timeline_scope, period_scores};
}

export function marketHistoryNumbers(game, liveOdds) {
  if (!game) return [];
  return Object.values(liveOdds?.markets?.[String(game.round)] || {})
    .filter(row => row?.home === game.home && row.away === game.away && row.date === game.date)
    .map(row => String(row.game_no)).filter(value => /^\d{1,6}$/.test(value));
}

export function oddsHistoryUrl(game, numbers, liveOdds) {
  if (!game || !liveOdds || liveOdds.history || !numbers.length) return null;
  const round = String(game.round);
  if (!/^\d{1,4}$/.test(round)) return null;
  return apiUrl('/api/live-odds-history?' + new URLSearchParams({
    round, game_nos: [...new Set(numbers)].sort().slice(0, 16).join(','),
  }));
}

export function marketHistoryForGame(game, numbers, liveOdds, response) {
  if (!game) return [];
  const round = String(game.round);
  const history = liveOdds?.history?.[round] || (response?.round === round ? response.history : null);
  return numbers.flatMap(gameNo => (history?.[gameNo] || []).map(entry => ({...entry, gameNo})));
}
