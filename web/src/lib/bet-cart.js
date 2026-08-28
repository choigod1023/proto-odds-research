export const cartOdds = (items) => items.reduce((total, item) => {
  const value = Number(item.odds);
  return Number.isFinite(value) && value > 0 ? total * value : total;
}, 1);

export const cartPayout = (items, stake) => {
  const amount = Math.max(0, Number(stake) || 0);
  const combined = items.length ? cartOdds(items) : 0;
  const gross = Math.floor(amount * combined);
  return { combined, gross, profit: Math.max(0, gross - amount) };
};

export function toggleCartSelection(items, next) {
  if (items.some((item) => item.id === next.id)) {
    return items.filter((item) => item.id !== next.id);
  }
  return [...items.filter((item) => item.gameId !== next.gameId), next];
}
