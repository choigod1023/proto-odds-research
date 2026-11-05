export const FAVORITES_KEY = "proodd-favorites-v1";
export const THEME_KEY = "proodd-theme-v1";
export const SPORTS = [["", "전체 종목"], ["sc", "축구"], ["bs", "야구"], ["bk", "농구"], ["vl", "배구"]];

// League labels from the game and odds feeds. World competitions and
// unidentified leagues cannot be classified from the team names alone.
const ASIAN_LEAGUES = new Set([
  "K리그1", "K리그2", "한국FA컵", "코리아컵",
  "J1리그", "J2리그", "J3리그", "J리그1", "J리그2", "일본J1리그", "일본J2리그",
  "J1백년구상리그", "J2J3백년구상리그", "일리그컵", "일본리그컵", "일본FA컵", "중국슈퍼리그",
  "ACLE", "ACL2", "AFC챔피언스리그", "AFC챔피언스리그엘리트", "AFC챔피언스리그2",
  "아시안컵", "AFC남자아시안컵예선", "축ASEA챔", "FIFA아세안컵", "동남아시아축구챔피언십", "아라비안걸프컵",
  "KBO", "NPB", "KBL", "WKBL", "KBL컵", "박신자컵", "남농EASL", "동아시아슈퍼리그",
  "KOVO남", "KOVO여", "KOVO컵남", "KOVO컵여", "V리그남", "V리그여", "남배아선", "여배아선",
  "아시안게임남자축구", "아시안게임여자축구", "아시안게임야구", "아시안게임남자농구", "아시안게임여자농구",
]);

export function isAsianLeague(league) {
  return ASIAN_LEAGUES.has(String(league ?? "").replace(/\s+/g, "").toUpperCase());
}

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
