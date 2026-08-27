export const PREFERRED_AUTO_ODDS = 1.5;
// 호환용 별칭. 1.50은 더 이상 제외 하한이 아니라 1순위 경계다.
export const MIN_AUTO_ODDS = 1.5;
export const MAX_AUTO_ODDS = 2.2;
export const UPSET_MIN_ODDS = 1.5;
export const UPSET_MAX_ODDS = 3.0;
export const UPSET_MIN_MARKET_PROBABILITY = 0.28;
export const UPSET_MIN_MODEL_PROBABILITY = 0.50;
export const UPSET_MAX_MODEL_PROBABILITY = 0.75;
export const UPSET_MIN_MODEL_GAP = 0.08;
export const UPSET_MAX_MODEL_GAP = 0.25;
const EXCLUDED_MARKETS = new Set(["홀짝"]);

const oddsOf = (selection) => Number(selection?.odds ?? selection?.["배당"]);
const probabilityOf = (selection) => Number(
  selection?.market_prob ?? selection?.["시장확률"],
);
const modelProbabilityOf = (selection) => Number(
  selection?.model_prob ?? selection?.["모델확률"],
);

const groupKey = (selection) => {
  const event = selection?.event_key ||
    `${selection?.date || ""}|${selection?.home || ""}|${selection?.away || selection?.match || ""}`;
  const label = selection?.market_label ?? selection?.label ?? "";
  return `${event}|${selection?.market || ""}|${label}|${selection?.line ?? ""}`;
};

/**
 * 자동 추천은 가격을 계산할 수 있는 모든 선택지가 아니라 검증된 안전 구간만 쓴다.
 * 현재는 홀짝, 2.20 이상, 같은 마켓의 현재 최저 배당이 아닌 역배를 제외한다.
 * 1.50 미만 최유력은 남기되 1.50 이상 후보보다 뒤에 둔다.
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
    if (!Number.isFinite(odds) || odds <= 1 || odds >= MAX_AUTO_ODDS) return false;
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

export function recommendationPriority(selection) {
  const odds = oddsOf(selection);
  if (!Number.isFinite(odds) || odds <= 1 || odds >= MAX_AUTO_ODDS) return -1;
  return odds >= PREFERRED_AUTO_ODDS ? 1 : 0;
}

/**
 * 시장과 구조 모델이 크게 다른 연구용 이변 관찰 후보를 고른다.
 * 운영 선택·확률·기대수익에는 반영하지 않는다.
 */
export function qualifiedUnderdogSelections(selections) {
  const rows = (selections || []).filter(Boolean);
  const favoriteProbabilityByGroup = new Map();
  for (const selection of rows) {
    const probability = probabilityOf(selection);
    if (!Number.isFinite(probability) || probability <= 0 || probability >= 1) continue;
    const key = groupKey(selection);
    favoriteProbabilityByGroup.set(
      key,
      Math.max(favoriteProbabilityByGroup.get(key) ?? 0, probability),
    );
  }

  return rows.filter((selection) => {
    if (EXCLUDED_MARKETS.has(String(selection.market || "").trim())) return false;
    const odds = oddsOf(selection);
    const probability = probabilityOf(selection);
    const modelProbability = modelProbabilityOf(selection);
    const favoriteProbability = favoriteProbabilityByGroup.get(groupKey(selection));
    const gap = modelProbability - probability;
    return Number.isFinite(odds)
      && Number.isFinite(probability)
      && Number.isFinite(modelProbability)
      && Number.isFinite(favoriteProbability)
      && odds >= UPSET_MIN_ODDS
      && odds < UPSET_MAX_ODDS
      && probability >= UPSET_MIN_MARKET_PROBABILITY
      && probability < favoriteProbability - 1e-9
      && modelProbability >= UPSET_MIN_MODEL_PROBABILITY
      && modelProbability <= UPSET_MAX_MODEL_PROBABILITY
      && gap >= UPSET_MIN_MODEL_GAP
      && gap <= UPSET_MAX_MODEL_GAP;
  }).sort((a, b) => {
    const gap = (modelProbabilityOf(b) - probabilityOf(b))
      - (modelProbabilityOf(a) - probabilityOf(a));
    return gap || probabilityOf(b) - probabilityOf(a) || oddsOf(a) - oddsOf(b);
  });
}

/** 경기에서 화면·설명·오늘 조합이 함께 따라야 할 최종 선택 하나를 반환한다. */
export function finalRecommendedSelection(selections) {
  // 이변 후보는 shadow 진단이다. 외부검증 전에는 운영 방향을 바꾸지 않는다.
  return [...eligibleAutoSelections(selections)].sort((a, b) =>
    probabilityOf(b) - probabilityOf(a) ||
    oddsOf(a) - oddsOf(b) ||
    String(a?.selection_id || "").localeCompare(String(b?.selection_id || ""))
  )[0] || null;
}

/** 완전히 교체된 최종 역배를 오늘 조합 후보로 전달할 때만 쓰는 명시적 표식이다. */
export function eligibleFinalSelections(selections) {
  return (selections || []).filter((selection) => {
    if (selection?.final_reversal === true || selection?.["최종전환"] === true) {
      return false;
    }
    return eligibleAutoSelections([selection]).includes(selection);
  });
}
