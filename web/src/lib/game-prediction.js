import { finalRecommendedSelection, recommendationPriority } from "./recommendation-policy.js";

const finiteProbability = (option) => {
  const value = Number(option?.["시장확률"]);
  return Number.isFinite(value) && value > 0 && value < 1 ? value : null;
};

/**
 * 경기마다 방향 하나는 반드시 제시한다. 다만 예측 방향과 구매 추천은 분리한다.
 * 검증된 운영 후보가 있으면 그대로 쓰고, 없으면 현재 배당에서 마진을 제거한
 * 시장확률이 가장 높은 선택을 비교 픽으로 남긴다.
 */
export function predictionForGame(options = []) {
  const valid = options.filter((option) => {
    const price = Number(option?.["배당"]);
    return String(option?.market || "").trim() !== "홀짝"
      && Number.isFinite(price) && price > 1 && finiteProbability(option) !== null;
  });
  if (!valid.length) return null;

  // 한 경기의 대표 방향은 풀타임 승패/승무패를 우선한다. 핸디캡이나 전반 시장의
  // 높은 확률이 대표 픽을 가로채면 "어느 팀이 유력한가"가 흐려진다.
  const main = valid.filter((option) => ["승패", "승무패"].includes(String(option.market).trim()));
  const pool = main.length ? main : valid;
  const comparisonOption = [...pool].sort((a, b) =>
    finiteProbability(b) - finiteProbability(a)
      || Number(a["배당"]) - Number(b["배당"])
      || String(a.selection_id || "").localeCompare(String(b.selection_id || ""))
  )[0];
  const recommended = finalRecommendedSelection(valid);
  const option = recommended || comparisonOption;
  const priority = recommended ? recommendationPriority(recommended) : -1;
  // 가격대와 시장 최유력 여부만 통과한 선택은 비교 방향이지 구매 추천이 아니다.
  // 검증 보정이 실제 최종확률에 반영된 선택만 추천 문구를 사용할 수 있다.
  const validated = recommended && (
    recommended["AI반영"] === true || recommended.has_validated_edge === true
  );
  return {
    option,
    probability: finiteProbability(option),
    recommendation: validated
      ? (priority === 1 ? "recommend" : "weak")
      : recommended ? "market" : "watch",
  };
}

export const predictionStrengthLabel = (prediction) => ({
  recommend: "추천",
  weak: "약한 추천",
  market: "시장 우세",
  watch: "관망",
}[prediction?.recommendation] || "픽 산출 대기");
