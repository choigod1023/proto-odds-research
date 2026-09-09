import fs from 'node:fs';
import { finalRecommendedSelection } from '../web/src/lib/recommendation-policy.js';
import { dailyHighlightedSelections } from '../web/src/lib/unified-recommendation.js';
const rows = JSON.parse(fs.readFileSync(0, 'utf8'));
const events = new Map();
for (const row of rows) {
  const key = `${row.league}|${row.event_key}`;
  if (!events.has(key)) events.set(key, []);
  events.get(key).push(row);
}
const chosen = [...events.values()].map(finalRecommendedSelection).filter(Boolean);
process.stdout.write(JSON.stringify(dailyHighlightedSelections(chosen)));
