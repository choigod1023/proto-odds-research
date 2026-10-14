#!/usr/bin/env node
// Outcome-free counterfactual selector bridge; never a historical runtime replay.
import {
  finalRecommendedSelection, PREFERRED_AUTO_ODDS, MAX_AUTO_ODDS,
} from "../web/src/lib/recommendation-policy.js";
import {
  dailyHighlightedSelections, DAILY_HIGHLIGHT_MIN_HIT,
  DAILY_HIGHLIGHT_BASE_PER_LEAGUE, DAILY_HIGHLIGHT_STRONG_MIN_HIT,
} from "../web/src/lib/unified-recommendation.js";

const fields = new Set([
  "selection_id", "event_key", "sport", "league", "kickoff_at", "market",
  "market_label", "sel", "odds", "market_prob", "predicted_hit_prob",
  "is_market_favorite", "n_way", "game_no", "round",
]);
const outcomes = new Set(["won", "winner", "result", "hit", "final_score"]);
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const fail = (message) => { throw new Error(message); };

function validateOption(row, ids, context) {
  if (!object(row)) fail(`${context}: option must be an object`);
  for (const key of Object.keys(row)) {
    if (outcomes.has(key)) fail(`${context}: forbidden outcome field ${key}`);
    if (!fields.has(key)) fail(`${context}: unsupported option field ${key}`);
  }
  for (const key of fields) {
    if (!Object.hasOwn(row, key)) fail(`${context}: missing ${key}`);
  }
  for (const key of ["selection_id", "event_key", "sport", "league", "kickoff_at",
    "market", "sel", "game_no"]) {
    if (typeof row[key] !== "string" || !row[key].trim()) fail(`${context}: invalid ${key}`);
  }
  if (typeof row.market_label !== "string") fail(`${context}: invalid market_label`);
  if (row.round !== "research") fail(`${context}: round must be research`);
  if (ids.has(row.selection_id)) fail(`${context}: duplicate selection_id ${row.selection_id}`);
  ids.add(row.selection_id);
  for (const key of ["market_prob", "predicted_hit_prob"]) {
    if (typeof row[key] !== "number" || !Number.isFinite(row[key]) ||
        !(row[key] > 0 && row[key] < 1)) fail(`${context}: invalid ${key}`);
  }
  if (typeof row.odds !== "number" || !Number.isFinite(row.odds)) fail(`${context}: invalid odds`);
  if (typeof row.is_market_favorite !== "boolean") fail(`${context}: invalid is_market_favorite`);
  if (![2, 3].includes(row.n_way)) fail(`${context}: n_way must be 2 or 3`);
  const time = row.kickoff_at;
  const stamp = Date.parse(time);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?\+09:00$/.test(time) ||
      !Number.isFinite(stamp) ||
      new Date(stamp + 9 * 3600000).toISOString().slice(0, 16) !== time.slice(0, 16)) {
    fail(`${context}: kickoff_at must be a valid explicit +09:00 timestamp`);
  }
}

function selectStrategy(rows, name) {
  if (!Array.isArray(rows)) fail(`${name}: options must be an array`);
  const ids = new Set();
  const events = new Map();
  rows.forEach((row, index) => {
    validateOption(row, ids, `${name}[${index}]`);
    const group = events.get(row.event_key) || [];
    if (group.length && ["sport", "league", "kickoff_at"].some((key) => group[0][key] !== row[key])) {
      fail(`${name}: inconsistent event metadata for ${row.event_key}`);
    }
    group.push(row);
    events.set(row.event_key, group);
  });
  const choices = [...events.values()].map(finalRecommendedSelection).filter(Boolean);
  const sports = new Map();
  for (const choice of choices) {
    if (!sports.has(choice.sport)) sports.set(choice.sport, []);
    sports.get(choice.sport).push(choice);
  }
  return {
    event_choices: choices.map((row) => row.selection_id),
    highlighted: [...sports.values()].flatMap((pool) =>
      dailyHighlightedSelections(pool).map((row) => row.selection_id)),
  };
}

try {
  // Preserve multibyte characters split across pipe chunks (e.g. Korean leagues).
  process.stdin.setEncoding("utf8");
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const payload = JSON.parse(input);
  if (!object(payload) || !object(payload.strategies)) fail("strategies must be an object");
  const strategies = Object.fromEntries(Object.entries(payload.strategies).map(([name, rows]) =>
    [name, selectStrategy(rows, name)]));
  process.stdout.write(JSON.stringify({
    strategies,
    policy_constants: {
      preferred_auto_odds: PREFERRED_AUTO_ODDS,
      max_auto_odds_exclusive: MAX_AUTO_ODDS,
      daily_highlight_min_hit: DAILY_HIGHLIGHT_MIN_HIT,
      daily_highlight_base_per_league: DAILY_HIGHLIGHT_BASE_PER_LEAGUE,
      daily_highlight_strong_min_hit: DAILY_HIGHLIGHT_STRONG_MIN_HIT,
    },
    probability_mode: "counterfactual_input",
    grouping: "event; highlights independently by sport, KST date and league",
    historical_runtime_replay: false,
  }) + "\n");
} catch (error) {
  process.stderr.write(`replay_recommendation_policy: ${error.message}\n`);
  process.exitCode = 1;
}
