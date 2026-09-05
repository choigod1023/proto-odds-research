// Betting availability must not decide whether an event remains visible.
// Recorded prices are only used to match filters, never added as buyable offers.
export function matchesGameMarketFilters(game, market = "", cap = 0) {
  const options = (game.options || []).filter((option) => !market || option.market === market);
  const record = game.prediction_record;
  const saved = record && (!market || record.market === market) ? record : null;
  if (market && !options.length && !saved) return false;
  if (!cap) return true;
  const prices = options.map((option) => Number(option.배당));
  if (saved) prices.push(Number(saved.odds));
  return prices.some((price) => Number.isFinite(price) && price > 1 && price <= cap);
}
