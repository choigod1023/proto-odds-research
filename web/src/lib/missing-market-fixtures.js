/** Unlinked volleyball market entries. A market document is not a prediction or status feed. */
export function missingMarketFixtures(data, odds) {
  const generated = Date.parse(odds?.generated_at || "");
  if (!Number.isFinite(generated)) return [];
  const kstYear = new Date(generated + 9 * 3600000).getUTCFullYear();
  const key = (g) => JSON.stringify([g.year || data?.year || kstYear, String(g.round), g.sport, g.league, g.date, g.home, g.away]);
  const existing = new Set([...(data?.live || []), ...(data?.past || [])].filter(g => g.round != null).map(key));
  const seen = new Map();
  for (const [round, markets] of Object.entries(odds?.markets || {})) {
    for (const row of Object.values(markets || {})) {
      if (row?.sport !== "vl" || row?.market !== "승패" || row.n_way !== 2 || !row.league || !row.home || !row.away) continue;
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
  return [...seen.values()];
}
