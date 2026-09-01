function scheduledAt(game) {
  const match = String(game?.date || "").match(/(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})/);
  if (!match) return null;
  const [, month, day, hour, minute] = match;
  const year = Number(game?.year || new Date().getFullYear());
  // 명시적 +09:00: 사용자의 브라우저 시간대와 무관하게 프로토 KST 시각으로 판정한다.
  const value = new Date(`${year}-${month}-${day}T${hour}:${minute}:00+09:00`).getTime();
  return Number.isFinite(value) ? value : null;
}

export function gamePhase(game, live = game?._liveState, now = Date.now()) {
  if (live?.cancelled || live?.postponed) return "pending";
  const observed = new Date(game?._liveFeedAt || live?.observed_at || 0).getTime();
  const feedFresh = !observed || Number(now) - observed <= 10 * 60 * 1000;
  if (live && live.status !== "BEFORE" && !live.finished && feedFresh) return "live";
  if (game?.status === "정산" || live?.finished) return "finished";
  if (live && live.status !== "BEFORE" && !live.finished && !feedFresh) return "pending";
  if (game?.status === "결과확인") return "pending";
  const start = scheduledAt(game);
  // 원천 매칭이 일시 실패해도 이미 끝났을 가능성이 큰 경기를 영원히 '예정'으로 두지 않는다.
  if (start && Number(now) - start > 8 * 60 * 60 * 1000) return "pending";
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
  if (!record) return { state: "unrecorded", label: "사전 기록 전 경기", record: null };
  if (record.result === "hit") return { state: "hit", label: "적중", record };
  if (record.result === "miss") return { state: "miss", label: "적중 실패", record };
  if (record.result === "void") return { state: "void", label: "무효", record };
  return { state: "pending", label: "판정 대기", record };
}
