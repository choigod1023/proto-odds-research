import { recommendationOutcome } from "./pick-result.js";
export { recommendationOutcome } from "./pick-result.js";

export function scheduledAt(game) {
  const match = String(game?.date || "").match(/(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})/);
  if (!match) return null;
  const [, month, day, hour, minute] = match;
  const year = Number(game?.year || new Date().getFullYear());
  // 명시적 +09:00: 사용자의 브라우저 시간대와 무관하게 프로토 KST 시각으로 판정한다.
  const value = new Date(`${year}-${month}-${day}T${hour}:${minute}:00+09:00`).getTime();
  return Number.isFinite(value) ? value : null;
}

export function decisionFrozen(game, now = Date.now()) {
  const start = scheduledAt(game);
  return start != null && Number(now) >= start - 30 * 60 * 1000;
}

export function liveFeedWithFallback(direct, fallback) {
  return direct || fallback || null;
}

export function gamePhase(game, live = game?._liveState, now = Date.now()) {
  const outcome = recommendationOutcome(game, live);
  if (["hit", "miss", "void"].includes(outcome.state)) return "finished";
  if (live?.cancelled || live?.postponed) return "pending";
  const observed = new Date(live?.observed_at || game?._liveFeedAt || 0).getTime();
  const feedFresh = !observed || Number(now) - observed <= 10 * 60 * 1000;
  if (live && live.status !== "BEFORE" && !live.finished && feedFresh) return "live";
  if (game?.status === "정산" || live?.finished) return "finished";
  if (live && live.status !== "BEFORE" && !live.finished && !feedFresh) return "pending";
  if (game?.status === "결과확인") return "pending";
  const start = scheduledAt(game);
  // 원천 매칭이 일시 실패해도 시작 시각이 지난 경기에 배당만 계속 노출하지 않는다.
  // 중계·프로토 시계의 작은 차이는 허용하되 15분이 지나면 상태 확인 대상으로 돌린다.
  if (start && Number(now) - start > 15 * 60 * 1000) return "pending";
  return "upcoming";
}

export const PHASE_LABEL = {
  live: "진행 중",
  upcoming: "예정",
  pending: "결과 확인 중",
  finished: "종료",
};
