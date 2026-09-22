export const API = import.meta.env?.VITE_MATCH_API_ORIGIN || 'https://proto-odds-collector.fly.dev';

export async function readMatchJson(path, signal) {
  const response = await fetch(`${API}${path}${path.includes('?') ? '&' : '?'}_=${Date.now()}`,
    {signal, cache:'no-store', headers:{'Cache-Control':'no-cache'}});
  // Pages and collector deploy independently. Only unsupported routes use the old API.
  if (response.status === 404 && path.startsWith('/api/matches?'))
    return readMatchJson('/api/picks', signal);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

export function mergeMatchDetail(summary, response, revision) {
  if (!summary?._detail_key) return summary;
  if (!response || response.revision !== revision) return null;
  const detail = response.game;
  if (!detail || !['year','round','sport','league','date','home','away'].every(k =>
    String(detail[k] ?? '') === String(summary[k] ?? ''))) return null;
  // Keep current live prices, frozen picks and live feed from the synchronized card.
  return {...detail, ...summary, 선발:detail.선발};
}
