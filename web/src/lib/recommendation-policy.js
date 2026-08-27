export const MIN_AUTO_ODDS = 1.5;
export const MAX_AUTO_ODDS = 2.2;
const EXCLUDED_MARKETS = new Set(["홀짝"]);

const oddsOf = (selection) => Number(selection?.odds ?? selection?.["배당"]);
const probabilityOf = (selection) => Number(
  selection?.market_prob ?? selection?.["시장확률"],
);

const groupKey = (selection) => {
  const event = selection?.event_key ||
    `${selection?.date || ""}|${selection?.home || ""}|${selection?.away || selection?.match || ""}`;
  const label = selection?.market_label ?? selection?.label ?? "";
  return `${event}|${selection?.market || ""}|${label}|${selection?.line ?? ""}`;
};

/**
 * 자동 추천은 가격을 계산할 수 있는 모든 선택지가 아니라 검증된 안전 구간만 쓴다.
 * 현재는 홀짝, 1.50 미만, 2.20 이상, 같은 마켓의 현재 최저 배당이 아닌 역배를 제외한다.
 * 최저 배당은 실시간 가격이 바뀌면 즉시 다시 계산되며 시장확률 1위와 같은 순서다.
 */
export function eligibleAutoSelections(selections) {
  const rows = (selections || []).filter(Boolean);
  const favoriteOddsByGroup = new Map();
  const favoriteProbabilityByGroup = new Map();
  for (const selection of rows) {
    const odds = oddsOf(selection);
    if (!Number.isFinite(odds) || odds <= 1) continue;
    const key = groupKey(selection);
    favoriteOddsByGroup.set(key, Math.min(favoriteOddsByGroup.get(key) ?? Infinity, odds));
    const probability = probabilityOf(selection);
    if (Number.isFinite(probability) && probability > 0 && probability < 1) {
      favoriteProbabilityByGroup.set(
        key,
        Math.max(favoriteProbabilityByGroup.get(key) ?? 0, probability),
      );
    }
  }

  return rows.filter((selection) => {
    if (EXCLUDED_MARKETS.has(String(selection.market || "").trim())) return false;
    const odds = oddsOf(selection);
    if (!Number.isFinite(odds) || odds < MIN_AUTO_ODDS || odds >= MAX_AUTO_ODDS) return false;
    if (selection.is_market_favorite === false) return false;
    const key = groupKey(selection);
    const probability = probabilityOf(selection);
    const favoriteProbability = favoriteProbabilityByGroup.get(key);
    if (Number.isFinite(probability) && Number.isFinite(favoriteProbability)) {
      return probability >= favoriteProbability - 1e-9;
    }
    const favoriteOdds = favoriteOddsByGroup.get(key);
    return !Number.isFinite(favoriteOdds) || odds <= favoriteOdds + 1e-9;
  });
}
