import { kickoffTime } from "./today-plan.js";

export const STALE_DATA_MS = 3 * 60 * 60 * 1000;

export function isDataStale(generatedAt, now = Date.now()) {
  const generated = Date.parse(generatedAt || "");
  return !Number.isFinite(generated) || now - generated >= STALE_DATA_MS;
}

export function waitingLabel(game, { generatedAt, year, now = Date.now() } = {}) {
  if (isDataStale(generatedAt, now)) return "데이터 갱신 지연";
  const kickoff = kickoffTime(game, year);
  if (Number.isFinite(kickoff) && kickoff <= now) return "상태 확인 불가";
  return "배당 발표 전";
}