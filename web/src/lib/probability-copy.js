export const ESTIMATE_MESSAGE = {
  missing_opening: "경기 전에 저장한 확률이 없어 현재 확률을 계산할 수 없습니다.",
  waiting_live: "실시간 경기 정보를 기다리고 있습니다. 점수와 진행 상황이 확인되면 계산합니다.",
  stale_live: "실시간 점수 업데이트가 늦어지고 있습니다. 최신 점수가 확인되면 다시 계산합니다.",
  missing_score: "현재 점수가 확인되지 않아 확률을 계산할 수 없습니다.",
  unsupported_market: "이 픽의 경기 유형은 실시간 확률 계산을 아직 지원하지 않습니다.",
  closed: "경기가 끝났거나 중단되어 실시간 확률 계산을 멈췄습니다.",
};
export const PROBABILITY_EXPLANATION = "이 픽이 맞을 확률입니다. 경기 전 값은 고정되고, 현재 값은 점수와 경기 진행에 따라 달라지는 추정치입니다. 실제 결과를 보장하지 않습니다.";
