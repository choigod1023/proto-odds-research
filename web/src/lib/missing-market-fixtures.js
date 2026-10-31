/** Current market fixtures absent from the prediction list. These are never predictions. */
export function missingMarketFixtures(data, odds) {
  const observed = Date.parse(odds?.generated_at);
  if (!Number.isFinite(observed)) return [];
  const kstYear = new Date(observed + 9 * 3600000).getUTCFullYear();
  const key = (g) => JSON.stringify([g.year || data?.year || kstYear, g.sport, g.league, g.date, g.home, g.away]);
  const existing = new Set([...(data?.live || []), ...(data?.past || [])].map(key));
  const seen = new Map();
  for (const [round, markets] of Object.entries(odds?.markets || {})) {
    for (const row of Object.values(markets || {})) {
      if (row?.sport !== "vl" || row?.market !== "승패" || row.n_way !== 2 || !row.sport || !row.league || !row.home || !row.away || !/^\d{2}\.\d{2}\([^)]*\) \d{2}:\d{2}$/.test(row.date || "")) continue;
      const match = row.date.match(/^(\d{2})\.(\d{2}).* (\d{2}):(\d{2})$/);
      const [, mm, dd, hh, min] = match;
      const times = [kstYear - 1, kstYear, kstYear + 1].map((year) => ({year, time: Date.parse(`${year}-${mm}-${dd}T${hh}:${min}:00+09:00`)})).filter((x) => Number.isFinite(x.time));
      times.sort((a, b) => Math.abs(a.time - observed) - Math.abs(b.time - observed));
      if (!times.length || Math.abs(times[0].time - observed) > 31 * 86400000) continue;
      const fixture = { year: times[0].year, round, sport: row.sport, league: row.league, home: row.home, away: row.away, date: row.date };
      const id = key(fixture);
      if (!existing.has(id) && !seen.has(id)) seen.set(id, fixture);
    }
  }
  return [...seen.values()];
}
