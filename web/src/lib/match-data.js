import { API_ORIGIN } from './api-origin.js';
export const API = API_ORIGIN;

export async function readMatchJson(path, signal) {
  const response = await fetch(`${API}${path}${path.includes('?') ? '&' : '?'}_=${Date.now()}`,
    {signal, cache:'no-store', headers:{'Cache-Control':'no-cache'}});
  // Pages and collector deploy independently. Only unsupported routes use the old API.
  if (response.status === 404 && path.startsWith('/api/matches?'))
    return readMatchJson('/api/picks', signal);
  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export async function readFixtureHistory(path, signal, legacyPath, project) {
  try {
    return await readMatchJson(path, signal);
  } catch (error) {
    // During a staged rollout the collector may not have indexed a feed yet,
    // or the old edge can reject an unknown route without CORS headers.
    if (signal?.aborted || (error.status !== 404 && !(error instanceof TypeError))) throw error;
    return project(await readMatchJson(legacyPath, signal));
  }
}

export function shouldRefreshDetailList(path, status, previousPath) {
  return Boolean(path?.startsWith('/api/match-detail?') && status === 409 && path !== previousPath);
}

export function mergeMatchDetail(summary, response, revision) {
  if (!summary?._detail_key) return summary;
  if (!response || response.revision !== revision) return null;
  const detail = response.game;
  if (!detail || !['year','round','sport','league','date','home','away'].every(k =>
    String(detail[k] ?? '') === String(summary[k] ?? ''))) return null;
  // Keep current live prices, frozen picks and live feed from the synchronized card.
  const merged = {...detail, ...summary, 선발:detail.선발};
  // Lists omit the model rows. Restore them only for the identical market
  // evidence and pregame snapshot, keeping the live state from the list.
  const sameMarkets = (a, b) => a?.pick_comparison_version === "exact-market-frontier-v1"
    && a.evidence_hash && a.evidence_hash === b?.evidence_hash;
  if (sameMarkets(summary.market_recommendations, detail.market_recommendations)) {
    merged.market_recommendations = detail.market_recommendations;
  }
  for (const field of ["market_prediction_record", "prediction_record"]) {
    const card = summary[field], full = detail[field];
    if (card?.prediction_snapshot_id && card.prediction_snapshot_id === full?.prediction_snapshot_id
      && sameMarkets(card.market_recommendations, full.market_recommendations)) {
      merged[field] = {...card, market_recommendations: full.market_recommendations};
    }
  }
  return merged;
}
