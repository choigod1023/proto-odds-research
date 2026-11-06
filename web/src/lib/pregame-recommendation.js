import { scheduledAt } from "./match-status.js";

/** Show the exact recorded selection, including its frozen price after T-30. */
export function pregameRecommendation(game, { frozen = false } = {}) {
  const record = game.featured_pick_record;
  const pick = record?.pick;
  const kickoff = scheduledAt(game);
  const captured = Date.parse(record?.captured_at);
  if (pick?.version !== "featured-pregame-v1" || !record.prediction_snapshot_id ||
      !Number.isFinite(kickoff) || Date.parse(record.kickoff) !== kickoff ||
      !Number.isFinite(captured) || captured >= kickoff - 1800000 ||
      !pick.selection_id || !pick.offer_id ||
      !Number.isFinite(pick.odds) || pick.odds <= 1 ||
      !Number.isFinite(pick.probability) || !Number.isFinite(pick.estimated_roi)) return null;

  if (!frozen) {
    const matches = (game.options || []).filter((row) => row.selection_id === pick.selection_id);
    const row = matches[0];
    if (game._liveOddsChanged || game.prediction_revision_id !== record.prediction_snapshot_id ||
        matches.length !== 1 || row.offer_id !== pick.offer_id || row.market !== pick.market ||
        (row.label || "") !== pick.label || (row.line ?? null) !== (pick.line ?? null) ||
        row.선택 !== pick.selection || String(row.게임번호) !== pick.game_no ||
        Number(row.배당) !== pick.odds) return null;
  }

  return {
    option: {market: pick.market, label: pick.label, 선택: pick.selection, 배당: pick.odds},
    price: pick.odds, probability: pick.probability, estimatedRoi: pick.estimated_roi,
    frozen, result: record.result?.result,
  };
}
