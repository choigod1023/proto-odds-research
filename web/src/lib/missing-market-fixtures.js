import { isVoidMarketResult } from "./pick-result.js";

/** Unlinked full-game market entries, across all supported sports.
 * A market document is not a prediction or an official status feed. */
export function missingMarketFixtures(data, odds) {
  const generated = Date.parse(odds?.generated_at || "");
  if (!Number.isFinite(generated)) return [];
  const kstYear = new Date(generated + 9 * 3600000).getUTCFullYear();
  const key = (g) => JSON.stringify([g.year || data?.year || kstYear, String(g.round), g.sport, g.league, g.date, g.home, g.away]);
  const games = [...(data?.live || []), ...(data?.past || [])].filter(g => g.round != null);
  const existing = new Set(games.map(key));
  // Cross-round links require an explicitly known year and exact oriented identity.
  const physicalKey = (g) => JSON.stringify([String(g.year ?? data?.year ?? ""), g.sport, g.league, g.date, g.home, g.away]);
  const currentByFixture = new Map();
  for (const game of games) {
    if (game.year == null && data?.year == null) continue;
    const id = physicalKey(game);
    if (!currentByFixture.has(id)) currentByFixture.set(id, []);
    currentByFixture.get(id).push(game);
  }
  const seen = new Map();
  for (const [round, markets] of Object.entries(odds?.markets || {})) {
    for (const row of Object.values(markets || {})) {
      const fullGame = row?.sport === "sc"
        ? row.market === "승무패" && row.n_way === 3
        : ["bs", "bk", "vl"].includes(row?.sport) && row.market === "승패" && row.n_way === 2;
      if (!fullGame || !row.league || !row.home || !row.away) continue;
      const match = String(row.date || "").match(/^(\d{2})\.(\d{2})\([^)]*\) (\d{2}):(\d{2})$/);
      if (!match) continue;
      const [, mm, dd, hh, min] = match;
      const times = [kstYear - 1, kstYear, kstYear + 1].map(year => {
        const time = Date.UTC(year, Number(mm) - 1, Number(dd), Number(hh) - 9, Number(min));
        const check = new Date(time + 9 * 3600000);
        return {year, time, valid: check.getUTCFullYear() === year && check.getUTCMonth() + 1 === Number(mm) && check.getUTCDate() === Number(dd) && check.getUTCHours() === Number(hh) && check.getUTCMinutes() === Number(min)};
      }).filter(x => x.valid).sort((a, b) => Math.abs(a.time - generated) - Math.abs(b.time - generated));
      if (!times.length || Math.abs(times[0].time - generated) > 31 * 86400000) continue;
      const fixture = {year:times[0].year, round, sport:row.sport, league:row.league, home:row.home, away:row.away, date:row.date, sourceRows:[]};
      const id = key(fixture);
      if (existing.has(id)) continue;
      if (!seen.has(id)) seen.set(id, fixture);
      // Preserve reissues and contradictory source rows without assigning an official state.
      seen.get(id).sourceRows.push({gameNo: String(row.game_no || ""), status: typeof row.status === "string" && row.status.trim() ? row.status.trim() : null, result: typeof row.result === "string" && row.result.trim() ? row.result.trim() : null});
    }
  }
  return [...seen.values()].map(fixture => {
    // Do not reconcile cancelled, postponed or otherwise contradictory source rows.
    const blocked = fixture.sourceRows.some(row =>
      isVoidMarketResult(row.result)
      || /취소|연기|중단|무효|cancel|postpon/i.test(`${row.status || ""} ${row.result || ""}`)
      || (row.status === "경기전" && row.result != null));
    const conflicting = ["status", "result"].some(field =>
      new Set(fixture.sourceRows.map(row => row[field]).filter(Boolean)).size > 1);
    const candidates = currentByFixture.get(physicalKey(fixture)) || [];
    if (blocked || conflicting || candidates.length !== 1) return fixture;
    const game = candidates[0];
    if (String(game.round) === String(fixture.round)
        || game._liveState?.cancelled || game._liveState?.postponed
        || /취소|연기|중단|무효|cancel|postpon/i.test(String(game.status || ""))) return fixture;
    return { ...fixture, linkedGame: game };
  });
}
