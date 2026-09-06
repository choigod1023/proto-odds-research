const stamp = (row) => Date.parse(row?.observed_at || "") || 0;
const key = (row) => `${row.source || ""}|${row.game_id || `${row.home}|${row.away}`}|${row.start || row.md || ""}`;
const closed = (row) => row.finished || row.cancelled || row.postponed;
const prefer = (old, next) => {
  if (!old) return next;
  if (stamp(next) < stamp(old)) return old;
  if (next.status === "BEFORE" && old.status !== "BEFORE") return old;
  if (stamp(next) === stamp(old) && closed(old) && !closed(next)) return old;
  return next;
};

/** Partial responses must not erase known scores or refresh their observation time. */
export function mergeLiveFeed(previous, incoming, now = Date.now()) {
  if (!incoming || !Array.isArray(incoming.games)) return previous || null;
  const rows = new Map();
  for (const row of previous?.games || []) {
    if (stamp(row) && now - stamp(row) < 24 * 3600000) rows.set(key(row), row);
  }
  for (const row of incoming.games) {
    const observed = row.observed_at || incoming.generated_at;
    const normalized = { ...row, observed_at: observed };
    const old = rows.get(key(row));
    const selected = prefer(old, normalized);
    // Keep verified names for this exact provider event across partial snapshots.
    rows.set(key(row), old ? { ...selected,
      home_alias: [...new Set([...(old.home_alias || []), ...(normalized.home_alias || [])])],
      away_alias: [...new Set([...(old.away_alias || []), ...(normalized.away_alias || [])])],
    } : selected);
  }
  const generated = Date.parse(incoming.generated_at) >= Date.parse(previous?.generated_at || 0)
    ? incoming.generated_at : previous?.generated_at || incoming.generated_at;
  return { ...incoming, generated_at: generated, games: [...rows.values()] };
}

/** Multiple providers share aliases: select by observation time, never array order. */
export function buildLiveIndex(feed) {
  const index = new Map();
  for (const row of feed?.games || []) {
    for (const home of [row.home, ...(row.home_alias || [])].filter(Boolean)) {
      for (const away of [row.away, ...(row.away_alias || [])].filter(Boolean)) {
        const id = `${home}|${away}|${row.md}`;
        index.set(id, prefer(index.get(id), row));
      }
    }
  }
  return index;
}
