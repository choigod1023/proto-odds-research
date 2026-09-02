import { kickoffTime } from "./today-plan.js";

export const STALE_DATA_MS = 3 * 60 * 60 * 1000;

export function latestGeneratedAt(...values) {
  let latest = null;
  let latestTime = -Infinity;
  values.flat().forEach((value) => {
    const time = Date.parse(value || "");
    if (Number.isFinite(time) && time > latestTime) {
      latest = value;
      latestTime = time;
    }
  });
  return latest;
}

export function isDataStale(generatedAt, now = Date.now()) {
  const generated = Date.parse(generatedAt || "");
  return !Number.isFinite(generated) || now - generated >= STALE_DATA_MS;
}

/** 실시간 소스의 첫 확인 전에는 오래된 정적 fallback을 장애로 단정하지 않는다. */
export function freshnessStatus({ staticGeneratedAt, liveGeneratedAt, liveChecked, now = Date.now() }) {
  const latest = latestGeneratedAt(liveGeneratedAt, staticGeneratedAt);
  if (!liveChecked && isDataStale(staticGeneratedAt, now)) return "checking";
  return isDataStale(latest, now) ? "stale" : "fresh";
}

export function waitingLabel(game, { generatedAt, year, now = Date.now() } = {}) {
  if (isDataStale(generatedAt, now)) return "데이터 갱신 지연";
  const kickoff = kickoffTime(game, year);
  if (Number.isFinite(kickoff) && kickoff <= now) return "상태 확인 불가";
  return "배당 발표 전";
}
