// Research adapter: call real eligibility rules; never send settlement labels here.
import fs from 'node:fs';
import { finalRecommendedSelection, recommendationPriority } from '../web/src/lib/recommendation-policy.js';
import { dailyHighlightedSelections, DAILY_HIGHLIGHT_MIN_HIT } from '../web/src/lib/unified-recommendation.js';
const events = JSON.parse(fs.readFileSync(0, 'utf8'));
const chosen = events.map(finalRecommendedSelection).filter(Boolean);
const groups = new Map();
for (const row of chosen.filter(r => r.predicted_hit_prob >= DAILY_HIGHLIGHT_MIN_HIT)) {
  const day = new Date(Date.parse(row.kickoff_at) + 9 * 3600000).toISOString().slice(0, 10);
  const key = `${row.league}|${day}`;
  if (!groups.has(key)) groups.set(key, []);
  groups.get(key).push(row);
}
const eligible = [...groups.values()].flatMap(rows => {
  const primary = rows.filter(r => recommendationPriority(r) === 1);
  return primary.length ? primary : rows;
});
process.stdout.write(JSON.stringify({eligible, production: dailyHighlightedSelections(chosen)}));
