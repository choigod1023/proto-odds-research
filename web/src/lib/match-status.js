export function gamePhase(game, live = game?._liveState) {
  if (live?.cancelled || live?.postponed) return "pending";
  if (live && live.status !== "BEFORE" && !live.finished) return "live";
  if (game?.status === "정산" || live?.finished) return "finished";
  if (game?.status === "결과확인") return "pending";
  return "upcoming";
}

export const PHASE_LABEL = {
  live: "진행 중",
  upcoming: "예정",
  pending: "결과 확인 중",
  finished: "종료",
};

export function recommendationOutcome(game) {
  const record = game?.prediction_record;
  if (!record) return { state: "unrecorded", label: "추천 기록 없음", record: null };
  if (record.result === "hit") return { state: "hit", label: "적중", record };
  if (record.result === "miss") return { state: "miss", label: "적중 실패", record };
  if (record.result === "void") return { state: "void", label: "무효", record };
  return { state: "pending", label: "판정 대기", record };
}
