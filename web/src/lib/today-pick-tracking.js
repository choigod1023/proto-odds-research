import { savedLivePrediction } from "./saved-live-prediction.js";
import { gamePhase, recommendationOutcome, scheduledAt } from "./match-status.js";
import { dailyHighlightedSelections, DAILY_HIGHLIGHT_MIN_HIT } from "./unified-recommendation.js";
import { eligibleFinalSelections } from "./recommendation-policy.js";
import { isOvernightGame } from "./game-date-filter.js";

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

import { ESTIMATE_MESSAGE } from "./probability-copy.js";

/** Pure display tracking. A missing candidate must never erase a saved pregame pick. */
export function trackTodayPicks({ games = [], today = null, currentToday = null, now = Date.now() } = {}) {
  now = Number(now);
  const day = kstDay(now);
  if (!day) return [];
  const year = Number(day.slice(0, 4));
  const candidates = (today?.candidates || []).filter(Boolean);
  const highlighted = new Set(dailyHighlightedSelections(candidates));
  const currentCandidates = (currentToday || today)?.candidates || [];
  const currentHighlighted = new Set(dailyHighlightedSelections(currentCandidates));
  const roster = (candidate) => typeof candidate.daily_recommendation?.recommended === "boolean"
    ? candidate.daily_recommendation.recommended : highlighted.has(candidate);
  const currentRoster = (candidate) => typeof candidate.daily_recommendation?.recommended === "boolean"
    ? candidate.daily_recommendation.recommended : currentHighlighted.has(candidate);
  const result = new Map();

  for (const original of games || []) {
    if (!original) continue;
    const game = { ...original, year: original.year || today?.year || year };
    const kickoff = scheduledAt(game);
    if (kickoff == null || (kstDay(kickoff) !== day && !isOvernightGame(game, now)) || !game.home || !game.away) continue;
    const live = game._liveState;
    const phase = gamePhase(game, live, now);
    const started = kickoff <= now || game._liveStarted === true || phase !== "upcoming";
    const saved = savedLivePrediction(game, live, now);
    const matched = candidates.filter((row) => matchesGame(row, game, kickoff, game.year));
    const currentMatched = currentCandidates.filter((row) => matchesGame(row, game, kickoff, game.year));
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
      // Aligned candidates describe a current decision, not the old published
      // roster. They may label a future pick only, never a started-game history.
      const currentHighlight = !started && !game._liveOddsChanged && currentMatched.some((row) =>
        currentRoster(row) && safe(row) && selectionKey(row) === selectionKey(option)
        && number(row.odds ?? row.배당) === originalOdds
        && pregameStamp(row, currentToday || today, kickoff, now));
      const explicitlyExcluded = rosterWasPregame
        && prior.some((row) => row.daily_recommendation?.recommended === false);
      // Recovery uses only recorded pregame inputs. It is not a reconstructed
      // league ranking, and never implies that membership in the old roster is known.
      const eligiblePrior = openingProbability != null && openingProbability >= DAILY_HIGHLIGHT_MIN_HIT
        && safe({ market: option.market, odds: originalOdds, market_prob: openingProbability,
          is_market_favorite: game.prediction_record.is_market_favorite,
          final_reversal: game.prediction_record.final_reversal });
      if (!knownHighlight && !currentHighlight && (!started || explicitlyExcluded || !eligiblePrior)) continue;
      source = knownHighlight ? "highlight" : currentHighlight ? "current" : "recorded";
      outcome = recommendationOutcome(game, live);
      if (phase === "live" && saved.estimateStatus === "available"
          && Number.isFinite(saved.estimate?.probability)) estimate = saved.estimate;
      estimateMessage = phase === "finished" ? "경기 종료 · 결과를 확인하세요."
        : ESTIMATE_MESSAGE[saved.estimateStatus] || "현재 추정 자료를 확인 중입니다.";
    } else {
      // Before kickoff only: require both a verified candidate snapshot and the
      // identical current option. Do not create a prediction_record for display.
      if (started || game._liveOddsChanged) continue;
      const current = currentMatched.find((row) => currentRoster(row) && safe(row)
        && pregameStamp(row, currentToday || today, kickoff, now)
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
      capturedAt = current.recommended_at || (currentToday || today).generated_at;
      source = "current";
      outcome = { state: "pending", label: "경기 전", record: null };
      estimateMessage = "경기 시작 전입니다.";
    }

    const key = `${eventKey(game, kickoff)}|${selectionKey(option)}`;
    const item = { key, game, kickoff, phase, source, option, openingProbability,
      originalOdds: Number.isFinite(originalOdds) && originalOdds > 1 ? originalOdds : null,
      capturedAt, outcome, estimate, estimateMessage, savedPrior: Boolean(saved) };
    const previous = result.get(key);
    // Exact cross-round duplicate: a saved prior beats a current candidate,
    // then the earliest capture wins, regardless of price or latest round.
    const savedPrior = item.savedPrior;
    const previousSaved = previous?.savedPrior;
    if (!previous || (savedPrior && !previousSaved)
        || (savedPrior === previousSaved && isoTime(capturedAt) < isoTime(previous.capturedAt))
        || (savedPrior === previousSaved && capturedAt === previous.capturedAt
          && source === "highlight" && previous.source !== "highlight")) {
      result.set(key, previous ? mergeObservation(item, previous, now) : item);
    } else {
      result.set(key, mergeObservation(previous, item, now));
    }
  }
  const order = { live: 0, pending: 1, upcoming: 2, finished: 3 };
  return [...result.values()].sort((a, b) => order[a.phase] - order[b.phase] || a.kickoff - b.kickoff
    || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
}

function mergeObservation(prior, other, now) {
  const closed = (item) => ["hit", "miss", "void"].includes(item.outcome.state);
  if (closed(prior) && closed(other) && prior.outcome.state !== other.outcome.state) {
    return { ...prior, phase: "pending", estimate: null,
      outcome: { ...prior.outcome, state: "pending", label: "정산 결과 충돌 · 확인 중" } };
  }
  const at = (item) => Date.parse(item.game._liveState?.observed_at || item.game._liveFeedAt || "") || 0;
  const observation = closed(other) && !closed(prior) ? other
    : closed(prior) ? prior
    : other.phase === "finished" && prior.phase !== "finished" ? other
    : prior.phase === "finished" ? prior : at(other) > at(prior) ? other : prior;
  if (observation === prior) return prior;
  const game = { ...prior.game, status: observation.game.status, score: observation.game.score,
    _liveState: observation.game._liveState, _liveFeedAt: observation.game._liveFeedAt,
    _liveStarted: observation.game._liveStarted };
  const estimate = observation.phase === "live" ? savedLivePrediction(game, game._liveState, now)?.estimate : null;
  return { ...prior, game, phase: observation.phase, estimate,
    estimateMessage: observation.estimateMessage,
    outcome: { ...observation.outcome, record: prior.outcome.record } };
}
