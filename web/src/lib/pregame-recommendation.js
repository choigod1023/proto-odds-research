import { recordedMarketOffers } from "./recorded-market-offers.js";
import { PREFERRED_AUTO_ODDS } from "./recommendation-policy.js";

const WINNER_MARKETS = new Set(["승패", "승무패"]);
const LINE_MARKETS = new Set(["핸디캡", "언더오버"]);
const FULL_GAME = new Set(["full", "full_game"]);

/** Rank existing operating picks with a common market probability basis. */
export function pregameRecommendation(winner, recommendations, options) {
  const candidates = recommendations.filter(({item}) => item.automatic_recommendation === true
    && item.candidate_only !== true && LINE_MARKETS.has(item.market)).map(({option}) => option);
  if (WINNER_MARKETS.has(winner?.market) && !winner.label) candidates.push(winner);
  if (!candidates.length) return null;

  const offers = recordedMarketOffers(options);
  const ranked = candidates.flatMap((option) => {
    if (!option?.selection_id || !option.offer_id) return [];
    const matches = options.filter((row) => row?.selection_id === option.selection_id);
    const row = matches[0];
    if (matches.length !== 1 || row.offer_id !== option.offer_id || row.market !== option.market
      || (row.label || "") !== (option.label || "") || row.선택 !== option.선택
      || Number(row.배당) !== Number(option.배당)) return [];

    const offer = offers.find((entry) => entry.gameNo === String(row.게임번호)
      && entry.market === row.market && entry.label === (row.label || ""));
    if (!offer || (offer.period && !FULL_GAME.has(offer.period))
      || (LINE_MARKETS.has(row.market) && !FULL_GAME.has(offer.period))) return [];

    const price = Number(row.배당);
    const inverseSum = offer.selections.reduce((sum, side) => sum + 1 / side.odds, 0);
    const probability = 1 / (price * inverseSum);
    return [{option, price, probability, estimatedRoi: probability * price - 1}];
  });
  // Keep the existing price preference and use the lower-price pool only as a fallback.
  const preferred = ranked.filter(({price}) => price >= PREFERRED_AUTO_ODDS);
  return (preferred.length ? preferred : ranked).sort((a, b) => b.probability - a.probability
    || b.estimatedRoi - a.estimatedRoi
    || a.option.selection_id.localeCompare(b.option.selection_id))[0] ?? null;
}
