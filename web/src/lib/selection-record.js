import { createBetRecord } from "./bet-ledger.js";
import { savedLivePrediction } from "./saved-live-prediction.js";
import { matchEvidence } from "./match-evidence.js";

export function createSelectionRecord(draft, purchase) {
  const record = createBetRecord(draft.game, draft.option, purchase);
  const saved = savedLivePrediction(draft.game);
  const sameSaved = saved && saved.option.market === draft.option.market
    && saved.option.label === (draft.option.label || "") && saved.option.선택 === draft.option.선택;
  const raw = draft.option["시장확률"];
  const probability = raw != null && raw !== "" && Number.isFinite(Number(raw)) && Number(raw) > 0 && Number(raw) < 1
    ? Number(raw) : sameSaved ? saved.openingProbability : null;
  record.openingProbability = probability;
  record.history = Number.isFinite(probability) ? [{ at: draft.selectedAt || record.createdAt, probability, phase: "selection" }] : [];
  record.selectionSnapshot = {
    selectedAt: draft.selectedAt || record.createdAt,
    option: structuredClone(draft.option),
    prediction: structuredClone(draft.game.prediction_record || null),
    evidence: structuredClone(draft.game["경기근거"] || null),
    performance: matchEvidence(draft.game),
  };
  return record;
}
