import { favoriteEntries } from "../lib/explorer-preferences.js";

export default function FavoriteControls({ game, favorites, onToggle }) {
  return <div className="favorite-controls" aria-label="팀·리그 즐겨찾기">
    {favoriteEntries(game).map(({ key, label }) => <button type="button" key={key}
      aria-pressed={favorites.includes(key)} onClick={() => onToggle(key)}>
      <span aria-hidden="true">{favorites.includes(key) ? "★" : "☆"}</span> {label}
    </button>)}
  </div>;
}
