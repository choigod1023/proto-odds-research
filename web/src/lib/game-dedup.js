const clean = (value) => String(value || "").replace(/\s+/g, "").toLowerCase();

const eventKey = (game) => [game?.sport, game?.league, game?.date, game?.home, game?.away]
  .map(clean).join("|");

const quality = (game) => {
  const priced = (game?.options || []).filter((option) => Number(option?.배당) > 1).length;
  return [priced > 0 ? 1 : 0, game?.status === "배당대기" ? 0 : 1, Number(game?.round) || 0];
};

const better = (candidate, previous) => {
  const left = quality(candidate);
  const right = quality(previous);
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return left[index] > right[index];
  }
  return false;
};

export function deduplicateGameCards(games = []) {
  const chosen = new Map();
  games.forEach((game) => {
    const key = eventKey(game);
    const previous = chosen.get(key);
    if (!previous || better(game, previous)) chosen.set(key, game);
  });
  return [...chosen.values()];
}
