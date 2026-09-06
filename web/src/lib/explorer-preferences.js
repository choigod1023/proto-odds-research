export const FAVORITES_KEY = "proodd-favorites-v1";
export const THEME_KEY = "proodd-theme-v1";
export const SPORTS = [["", "전체 종목"], ["sc", "축구"], ["bs", "야구"], ["bk", "농구"], ["vl", "배구"]];

// Scope names by sport and league: equal display names need not be the same team.
export function favoriteKey(game, type, name) {
  return JSON.stringify([game.sport || "", game.league || "", type, name]);
}
export function favoriteEntries(game) {
  return [["league", game.league], ["team", game.home], ["team", game.away]]
    .filter(([, name]) => name).map(([type, name]) => ({
      key: favoriteKey(game, type, name), type, name,
      label: `${type === "league" ? "리그" : "팀"} · ${name}`,
    }));
}
export function isFavoriteGame(game, favorites) {
  return favoriteEntries(game).some(({ key }) => favorites.includes(key));
}
export function readFavorites(storage = globalThis.localStorage) {
  try {
    const data = JSON.parse(storage?.getItem(FAVORITES_KEY) || "[]");
    return Array.isArray(data) ? [...new Set(data.filter((key) => {
      if (typeof key !== "string") return false;
      try { const row = JSON.parse(key); return Array.isArray(row) && row.length === 4
        && row.every((value) => typeof value === "string") && ["team", "league"].includes(row[2]); }
      catch { return false; }
    }))] : [];
  } catch { return []; }
}
export function readTheme(storage = globalThis.localStorage) {
  try { const value = storage?.getItem(THEME_KEY); return ["light", "dark"].includes(value) ? value : "system"; }
  catch { return "system"; }
}

export function selectionKey(game, option) {
  return JSON.stringify([game.sport, game.league, game.year, game.round, game.date,
    game.home, game.away, option["게임번호"], option.market, option.label, option.line, option["선택"]]);
}
export function addSelection(items, game, option) {
  const key = selectionKey(game, option);
  if (items.some((item) => item.key === key)) return items;
  // Preserve the exact state seen at selection; polling must never reprice this draft.
  return [...items, { key, game: structuredClone(game), option: structuredClone(option),
    selectedAt: new Date().toISOString() }];
}
