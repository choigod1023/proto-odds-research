import { savedLivePrediction } from "./saved-live-prediction.js";
import { gamePhase, recommendationOutcome, scheduledAt } from "./match-status.js";
import { dailyHighlightedSelections, DAILY_HIGHLIGHT_MIN_HIT } from "./unified-recommendation.js";
import { eligibleFinalSelections } from "./recommendation-policy.js";

const clean = (value) => String(value ?? "").trim();
const number = (value) => typeof value === "number" || (typeof value === "string" && value.trim())
  ? Number(value) : NaN;
const probability = (value) => {
  const p = number(value);
  return Number.isFinite(p) && p > 0 && p < 1 ? p : null;
};
const kstDay = (time) => Number.isFinite(time)
  ? new Date(time + 9 * 3600000).toISOString().slice(0, 10) : null;
const selectionKey = (row) => JSON.stringify([
  clean(row?.market), clean(row?.market_label ?? row?.label),
  clean(row?.sel ?? row?.selection ?? row?.선택),
]);
const isoTime = (text) => {
  let value = clean(text);
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(value)) {
    value = `${value.replace(" ", "T")}+09:00`;
  }
  return Date.parse(value);
};
const candidateTime = (row, year) => {
  const iso = isoTime(row?.kickoff_at);
  return Number.isFinite(iso) ? iso : scheduledAt({ ...row, year: row?.year || year });
};
const teams = (row) => {
  const pair = clean(row?.match).split(" vs ");
  return [clean(row?.home || pair[0]), clean(row?.away || pair[1])];
};
const eventKey = (row, kickoff) => JSON.stringify([
  kickoff, clean(row?.sport), clean(row?.league), ...teams(row),
]);
const matchesGame = (candidate, game, kickoff, year) => {
  const [home, away] = teams(candidate);
  return home && away && home === clean(game.home) && away === clean(game.away)
    && clean(candidate.league) === clean(game.league)
    && (!candidate.sport || candidate.sport === game.sport)
    && candidateTime(candidate, year) === kickoff;
};
const pregameStamp = (candidate, today, kickoff, now) => {
  const stamp = isoTime(candidate.recommended_at || today?.generated_at);
  return Number.isFinite(stamp) && stamp < kickoff && stamp <= now;
};
const safe = (candidate) => eligibleFinalSelections([candidate]).length === 1;

const ESTIMATE_MESSAGE = {
  missing_opening: "사전 확률 기록이 없어 현재 추정을 제공하지 않습니다.",
  waiting_live: "최신 경기 중계가 확인되면 현재 추정을 표시합니다.",
  stale_live: "중계 갱신이 늦어 현재 추정을 보류합니다.",
  missing_score: "현재 점수 자료가 없어 추정을 보류합니다.",
  unsupported_market: "이 마켓의 현재 추정은 지원하지 않습니다.",
  closed: "경기 종료·중단 후에는 현재 추정을 표시하지 않습니다.",
};

/** Pure display tracking. A missing candidate must never erase a saved pregame pick. */
export function trackTodayPicks({ games = [], today = null, now = Date.now() } = {}) {
  now = Number(now);
  const day = kstDay(now);
  if (!day) return [];
  const year = Number(day.slice(0, 4));
  const candidates = (today?.candidates || []).filter(Boolean);
  const highlighted = new Set(dailyHighlightedSelections(candidates));
  const roster = (candidate) => typeof candidate.daily_recommendation?.recommended === "boolean"
    ? candidate.daily_recommendation.recommended : highlighted.has(candidate);
  const result = new Map();

  for (const original of games || []) {
    if (!original) continue;
    const game = { ...original, year: original.year || today?.year || year };
    const kickoff = scheduledAt(game);
    if (kickoff == null || kstDay(kickoff) !== day || !game.home || !game.away) continue;
    const live = game._liveState;
    const phase = gamePhase(game, live, now);
    const started = kickoff <= now || game._liveStarted === true || phase !== "upcoming";
    const saved = savedLivePrediction(game, live, now);
    const matched = candidates.filter((row) => matchesGame(row, game, kickoff, game.year));
    let option, openingProbability, originalOdds, capturedAt, source, outcome, estimate = null;
    let estimateMessage;

    if (saved) {
      option = saved.option;
      openingProbability = saved.openingProbability;
      originalOdds = number(option.배당);
      capturedAt = saved.capturedAt;
      const prior = matched.filter((row) => selectionKey(row) === selectionKey(option)
        && number(row.odds ?? row.배당) === originalOdds
        && pregameStamp(row, today, kickoff, now));
      // A retained candidate's old recommended_at does not prove that a newer
      // artifact's recomputed daily membership existed before kickoff.
      const published = isoTime(today?.generated_at);
      const rosterWasPregame = Number.isFinite(published) && published < kickoff && published <= now;
      const knownHighlight = rosterWasPregame && prior.some(roster);
      const explicitlyExcluded = rosterWasPregame
        && prior.some((row) => row.daily_recommendation?.recommended === false);
      // Recovery uses only recorded pregame inputs. It is not a reconstructed
      // league ranking, and never implies that membership in the old roster is known.
      const eligiblePrior = openingProbability != null && openingProbability >= DAILY_HIGHLIGHT_MIN_HIT
        && safe({ market: option.market, odds: originalOdds, market_prob: openingProbability,
          is_market_favorite: game.prediction_record.is_market_favorite,
          final_reversal: game.prediction_record.final_reversal });
      if (!knownHighlight && (!started || explicitlyExcluded || !eligiblePrior)) continue;
      source = knownHighlight ? "highlight" : "recorded";
      outcome = recommendationOutcome(game, live);
      if (phase === "live" && saved.estimateStatus === "available"
          && Number.isFinite(saved.estimate?.probability)) estimate = saved.estimate;
      estimateMessage = phase === "finished" ? "경기 종료 · 결과를 확인하세요."
        : ESTIMATE_MESSAGE[saved.estimateStatus] || "현재 추정 자료를 확인 중입니다.";
    } else {
      // Before kickoff only: require both a verified candidate snapshot and the
      // identical current option. Do not create a prediction_record for display.
      if (started || game._liveOddsChanged) continue;
      const current = matched.find((row) => roster(row) && safe(row)
        && pregameStamp(row, today, kickoff, now)
        && probability(row.market_prob) != null
        && (game.options || []).some((o) => selectionKey(o) === selectionKey(row)
          && number(o.배당) === number(row.odds)
          && probability(o.시장확률) != null
          // today_combo stores market probability at four decimal places.
          && Math.abs(number(o.시장확률) - number(row.market_prob)) <= 5.0001e-5));
      if (!current) continue;
      option = { market: current.market, label: current.market_label ?? current.label ?? "",
        선택: current.sel, 배당: current.odds };
      openingProbability = current.decision_pipeline_applied === true && current.has_validated_edge === true
        ? probability(current.predicted_hit_prob) ?? probability(current.market_prob)
        : probability(current.market_prob);
      originalOdds = number(current.odds);
      capturedAt = current.recommended_at || today.generated_at;
      source = "current";
      outcome = { state: "pending", label: "경기 전", record: null };
      estimateMessage = "경기 시작 전입니다.";
    }

    const key = `${eventKey(game, kickoff)}|${selectionKey(option)}`;
    const item = { key, game, kickoff, phase, source, option, openingProbability,
      originalOdds: Number.isFinite(originalOdds) && originalOdds > 1 ? originalOdds : null,
      capturedAt, outcome, estimate, estimateMessage };
    const previous = result.get(key);
    // Exact cross-round duplicate: a saved prior beats a current candidate,
    // then the earliest capture wins, regardless of price or latest round.
    const savedPrior = source !== "current";
    const previousSaved = previous?.source !== "current";
    if (!previous || (savedPrior && !previousSaved)
        || (savedPrior === previousSaved && isoTime(capturedAt) < isoTime(previous.capturedAt))
        || (savedPrior === previousSaved && capturedAt === previous.capturedAt
          && source === "highlight" && previous.source !== "highlight")) {
      result.set(key, item);
    }
  }
  const order = { live: 0, pending: 1, upcoming: 2, finished: 3 };
  return [...result.values()].sort((a, b) => order[a.phase] - order[b.phase] || a.kickoff - b.kickoff
    || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
}
