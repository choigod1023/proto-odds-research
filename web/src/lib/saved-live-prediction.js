import { scheduledAt } from "./match-status.js";
import { estimateLiveProbability } from "./bet-ledger.js";

const numeric = (value) => typeof value === "number" || (typeof value === "string" && value.trim())
  ? Number(value) : NaN;
const probability = (value) => {
  const n = numeric(value);
  return Number.isFinite(n) && n > 0 && n < 1 ? n : null;
};

/** Historical display only: never reconstruct a missing pregame pick from live odds. */
export function savedLivePrediction(game, live = null, now = Date.now()) {
  const record = game?.prediction_record;
  const kickoff = scheduledAt(game);
  const captured = Date.parse(record?.captured_at || "");
  if (!record?.selection_id || !record.market || !record.selection
      || kickoff == null || !Number.isFinite(captured) || captured >= kickoff || captured > now) return null;
  const option = {
    selection_id: record.selection_id, offer_id: record.offer_id,
    market: record.market, label: record.label || "", 선택: record.selection,
    배당: Number.isFinite(numeric(record.odds)) ? numeric(record.odds) : null,
  };
  const openingProbability = probability(record.probability);
  const result = { option, openingProbability, capturedAt: record.captured_at, estimate: null,
    estimateStatus: openingProbability === null ? "missing_opening" : "waiting_live" };
  if (openingProbability === null || !live || live.status === "BEFORE") return result;
  if (live.finished || live.cancelled || live.postponed) return { ...result, estimateStatus: "closed" };
  const observed = Date.parse(game._liveFeedAt || live.observed_at || "");
  if (!Number.isFinite(observed) || now - observed > 10 * 60 * 1000 || observed > now + 5000) {
    return { ...result, estimateStatus: "stale_live" };
  }
  if (![live.home_score, live.away_score].every((v) => Number.isInteger(numeric(v)) && numeric(v) >= 0)) {
    return { ...result, estimateStatus: "missing_score" };
  }
  // The existing estimator has no handicap/period/margin model. Do not present
  // its generic win/loss fallback as a probability for those distinct contracts.
  const supportedChoice = {
    "승패": ["홈", "원정", "승", "패"],
    "승무패": ["홈", "원정", "승", "패", "무", "무승부"],
    "언더오버": ["언더", "오버"],
  }[record.market]?.includes(record.selection);
  if (!supportedChoice || !["sc", "bs", "bk"].includes(game.sport)
      || (record.market === "언더오버" && !/\d/.test(option.label))) {
    return { ...result, estimateStatus: "unsupported_market" };
  }
  const estimate = estimateLiveProbability({ openingProbability, game: { sport: game.sport },
    selection: { market: option.market, label: option.label, choice: option.선택 } }, live);
  return { ...result, estimate, estimateStatus: "available" };
}
