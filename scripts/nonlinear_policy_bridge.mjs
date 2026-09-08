// Research-only boundary: actual policy functions receive NO settlement fields.
import { finalRecommendedSelection } from "../web/src/lib/recommendation-policy.js";
import { dailyHighlightedSelections } from "../web/src/lib/unified-recommendation.js";
import { pathToFileURL } from "node:url";

const fields = new Set(["selection_id", "event_key", "sport", "league", "kickoff_at",
  "market", "market_label", "sel", "odds", "market_prob", "predicted_hit_prob",
  "is_market_favorite", "n_way", "game_no", "round"]);

export function replay(rows) {
  if (!Array.isArray(rows)) throw new Error("rows must be an array");
  const ids = new Set();
  const events = new Map();
  for (const row of rows) {
    if (!row || typeof row !== "object" || Array.isArray(row) ||
        Object.keys(row).some((key) => !fields.has(key)) ||
        [...fields].some((key) => !Object.hasOwn(row, key))) {
      throw new Error("invalid fields: only outcome-free policy inputs are allowed");
    }
    for (const key of ["selection_id", "event_key", "sport", "league", "market",
      "sel", "game_no"]) {
      if (typeof row[key] !== "string" || !row[key].trim()) throw new Error(key);
    }
    for (const key of ["market_prob", "predicted_hit_prob"]) {
      if (typeof row[key] !== "number" || !Number.isFinite(row[key]) ||
          !(row[key] > 0 && row[key] < 1)) throw new Error(key);
    }
    if (typeof row.odds !== "number" || !Number.isFinite(row.odds) || row.odds <= 1 ||
        row.is_market_favorite !== true || ![2, 3].includes(row.n_way) ||
        row.round !== "research" || typeof row.market_label !== "string") {
      throw new Error("invalid market metadata");
    }
    const stamp = Date.parse(row.kickoff_at);
    if (typeof row.kickoff_at !== "string" ||
        !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$/.test(row.kickoff_at) ||
        !Number.isFinite(stamp) ||
        new Date(stamp + 9 * 3600000).toISOString().slice(0, 19) !== row.kickoff_at.slice(0, 19)) {
      throw new Error("kickoff must be a real KST timestamp");
    }
    if (ids.has(row.selection_id)) throw new Error("duplicate selection id");
    ids.add(row.selection_id);
    const group = events.get(row.event_key) || [];
    if (group.length && ["sport", "league", "kickoff_at"].some((key) => group[0][key] !== row[key])) {
      throw new Error("conflicting event metadata");
    }
    group.push(row);
    events.set(row.event_key, group);
  }
  const choices = [...events.values()].map(finalRecommendedSelection).filter(Boolean);
  const sports = new Map();
  for (const row of choices) {
    if (!sports.has(row.sport)) sports.set(row.sport, []);
    sports.get(row.sport).push(row);
  }
  return {
    event_choices: choices.map((row) => row.selection_id),
    highlighted: [...sports.values()].flatMap((pool) =>
      dailyHighlightedSelections(pool).map((row) => row.selection_id)),
  };
}

// This module is also imported by tests without consuming their stdin.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    process.stdin.setEncoding("utf8");
    let input = "";
    for await (const chunk of process.stdin) input += chunk;
    const result = replay(JSON.parse(input));
    process.stdout.write(JSON.stringify(result) + "\n");
  } catch (error) {
    process.stderr.write(String(error.message) + "\n");
    process.exitCode = 1;
  }
}
