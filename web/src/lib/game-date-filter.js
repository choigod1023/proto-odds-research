import { gamePhase, scheduledAt } from "./match-status.js";

const DAY = 86400000;
const kstDay = (time) => Math.floor((time + 9 * 3600000) / DAY);
const kickoffOf = (game, now) => scheduledAt({ ...game,
  year: game?.year || new Date(now + 9 * 3600000).getUTCFullYear() });

/** Keep last night's active match, not a whole previous day's archive. */
export function isOvernightGame(game, now = Date.now()) {
  const kickoff = kickoffOf(game, now);
  const live = game?._liveState;
  if (kickoff == null || kstDay(kickoff) !== kstDay(now) - 1
      || now - kickoff > 18 * 3600000 || now < kickoff || !live) return false;
  if (live.finished || live.cancelled || live.postponed
      || ["RESULT", "END", "ENDED", "FINAL", "CANCEL", "POSTPONED"].includes(live.status)) return false;
  // Keep the last valid started row through a temporary feed outage too.
  // gamePhase still labels stale observations pending, never freshly LIVE.
  return ["STARTED", "IN_PROGRESS", "LIVE"].includes(live.status)
    && gamePhase(game, live, now) !== "finished";
}

export function matchesGameDate(game, filter = "today", now = Date.now()) {
  if (!filter || filter === "all") return true;
  const kickoff = kickoffOf(game, now);
  if (kickoff == null) return false;
  if (filter === "tomorrow") return kstDay(kickoff) === kstDay(now) + 1;
  return kstDay(kickoff) === kstDay(now) || isOvernightGame(game, now);
}
