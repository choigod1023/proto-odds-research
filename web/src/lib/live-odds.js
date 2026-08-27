import { gradeOf } from "./fmt.js";
import { selectionKey } from "./unified-recommendation.js";

export const LIVE_ODDS_MAX_AGE_MS = 10 * 60 * 1000;
const FUTURE_CLOCK_TOLERANCE_MS = 60 * 1000;
const EXPLICIT_ZONE = /(Z|[+-]\d{2}:\d{2})$/i;

const clean = (value) => String(value ?? "").trim();
const number = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 1 ? parsed : null;
};

/** ISO의 UTC/+09 offset을 절대시각으로 비교한다. timezone 없는 값은 fail closed. */
export function liveOddsFreshness(feed, now = Date.now(), maxAgeMs = LIVE_ODDS_MAX_AGE_MS) {
  const generatedAt = clean(feed?.generated_at);
  if (!feed?.odds || !generatedAt) {
    return { fresh: false, status: "missing", generatedAt: generatedAt || null, ageMs: null };
  }
  if (!EXPLICIT_ZONE.test(generatedAt)) {
    return { fresh: false, status: "invalid-timezone", generatedAt, ageMs: null };
  }
  const observed = Date.parse(generatedAt);
  if (!Number.isFinite(observed)) {
    return { fresh: false, status: "invalid-time", generatedAt, ageMs: null };
  }
  const ageMs = Number(now) - observed;
  const fresh = ageMs >= -FUTURE_CLOCK_TOLERANCE_MS && ageMs <= maxAgeMs;
  return {
    fresh,
    status: fresh ? "fresh" : ageMs < -FUTURE_CLOCK_TOLERANCE_MS ? "future" : "stale",
    generatedAt,
    observedAtMs: observed,
    ageMs,
  };
}

const gameIdentity = (game) => [
  game?.round, game?.date, game?.league, game?.home, game?.away,
].map(clean).join("|");

const optionRound = (option, fallbackRound) => clean(option?.round) || clean(fallbackRound);

/**
 * 신규 산출물은 option.round를 직접 싣는다. 다만 배포 직후 첫 재생성 전의
 * 구형 picks_v2도 살아 있어야 하므로, game.round bucket에 번호가 없고 다른
 * 단 하나의 회차에만 있으면 live feed의 구조로 판매 회차를 복원한다.
 * 여러 회차에 같은 게임번호가 있으면 추측하지 않고 기존 회차에 닫는다.
 */
function resolvedOptionRound(option, fallbackRound, feed) {
  const explicit = clean(option?.round);
  if (explicit) return explicit;
  const fallback = clean(fallbackRound);
  const gameNo = clean(option?.["게임번호"]);
  if (!gameNo) return fallback;
  if (feed?.odds?.[fallback]?.[gameNo]) return fallback;
  const matches = Object.entries(feed?.odds || {})
    .filter(([, bucket]) => bucket?.[gameNo])
    .map(([round]) => round);
  return matches.length === 1 ? matches[0] : fallback;
}

const optionIdentity = (option, fallbackIndex) => {
  const fields = [
    option?.round, option?.["게임번호"], option?.market, option?.label, option?.line,
    option?.n_way, option?.["선택"],
  ].map(clean);
  return fields.some(Boolean) ? fields.join("|") : `index:${fallbackIndex}`;
};

const marketIdentity = (option) => [
  option?.round, option?.["게임번호"], option?.market, option?.label, option?.line,
  option?.n_way,
].map(clean).join("|");

function dedupeOptions(options, round, globalSelections) {
  const local = new Set();
  const unique = [];
  (options || []).forEach((option, index) => {
    const identity = optionIdentity(option, index);
    const globalKey = `${optionRound(option, round)}|${identity}`;
    if (local.has(identity) || globalSelections.has(globalKey)) return;
    local.add(identity);
    globalSelections.add(globalKey);
    unique.push(option);
  });
  return unique;
}

function marketMetadata(options) {
  const groups = new Map();
  options.forEach((option) => {
    const key = marketIdentity(option);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(option);
  });
  const enriched = [];
  groups.forEach((rows) => {
    const valid = rows.map((row) => number(row["배당"]));
    const complete = valid.every((value) => value != null) && valid.length >= 2;
    const overround = complete
      ? valid.reduce((total, value) => total + 1 / value, 0) : null;
    const favorite = complete ? Math.min(...valid) : null;
    const changed = rows.some((row) => row._live_changed);
    rows.forEach((row, index) => {
      const next = {
        ...row,
        _is_current_favorite: favorite == null ? null : valid[index] === favorite,
        _current_overround: overround,
        _current_payout: overround ? 1 / overround : null,
        _live_market_changed: changed,
      };
      if (changed) {
        // 모델 확률은 가격과 독립적으로 유지한다. 올바른 Shin 재계산기가 없는
        // 브라우저에서 시장확률·기대손익을 역수 정규화로 꾸며내지 않는다.
        next._odds_metrics_stale = true;
        next["시장확률"] = null;
        next["예상손익"] = null;
        next["괴리"] = null;
      }
      enriched.push(next);
    });
  });
  // group iteration이 원래 마켓 순서를 유지하지만, 여러 마켓이 섞인 경우에도
  // 원본 옵션 순서를 정확히 보존한다.
  const byIdentity = new Map(enriched.map((row) => [optionIdentity(row, 0), row]));
  return options.map((row, index) => byIdentity.get(optionIdentity(row, index)) || row);
}

function overlayGame(game, feed) {
  const seenByGameNo = new Map();
  const options = (game.options || []).map((option) => {
    const round = optionRound(option, game.round);
    const bucket = feed.odds?.[round];
    const gameNo = clean(option["게임번호"]);
    const positionKey = `${round}|${gameNo}`;
    const index = seenByGameNo.get(positionKey) || 0;
    seenByGameNo.set(positionKey, index + 1);
    const fresh = bucket?.[gameNo];
    const liveValue = Array.isArray(fresh) ? number(fresh[index]) : null;
    if (liveValue == null) return option;
    const previous = number(option["배당"]);
    return {
      ...option,
      "배당": liveValue,
      _live: true,
      _live_changed: previous !== liveValue,
      _live_observed_at: feed.generated_at,
    };
  });
  const current = marketMetadata(options);
  const observed = current.map((option) => option._live_observed_at).filter(Boolean).sort().at(-1);
  return {
    ...game,
    options: current,
    ...(observed ? { _live_odds_observed_at: observed } : {}),
  };
}

/**
 * live/past를 하나의 안정된 경기 풀로 만들고, fresh feed일 때만 가격을 덮는다.
 * 중복 선택은 live 배열 위치를 소비하기 전에 제거한다.
 */
export function adjustedGamesWithLiveOdds(games, feed, now = Date.now()) {
  const freshness = liveOddsFreshness(feed, now);
  const globalSelections = new Set();
  const gameKeys = new Set();
  const deduped = [];
  (games || []).forEach((game) => {
    const key = gameIdentity(game);
    if (gameKeys.has(key)) return;
    gameKeys.add(key);
    const resolved = (game.options || []).map((option) => {
      if (clean(option?.round)) return option;
      const round = resolvedOptionRound(option, game.round, feed);
      return round ? { ...option, round } : option;
    });
    const options = dedupeOptions(resolved, game.round, globalSelections);
    deduped.push({ ...game, options });
  });
  const adjusted = freshness.fresh
    ? deduped.map((game) => overlayGame(game, feed))
    : deduped;
  return {
    games: adjusted,
    freshness,
    changedOptions: adjusted.reduce(
      (count, game) => count + (game.options || []).filter((option) => option._live_changed).length,
      0,
    ),
  };
}

function optionMap(games) {
  const out = new Map();
  (games || []).forEach((game) => (game.options || []).forEach((option) => {
    const key = selectionKey(option, optionRound(option, game.round));
    if (!out.has(key)) out.set(key, option);
  }));
  return out;
}

function syncCandidate(candidate, currentOptions, grades) {
  if (!candidate) return candidate;
  const option = currentOptions.get(selectionKey(candidate, candidate.round));
  if (!option) return candidate;
  const currentOdds = number(option["배당"]);
  if (currentOdds == null) return candidate;
  const grade = gradeOf(grades, currentOdds);
  const marketChanged = !!option._odds_metrics_stale || Number(candidate.odds) !== currentOdds;
  let next = {
    ...candidate,
    odds: currentOdds,
    actual_odds: currentOdds,
    model_prob: candidate.model_prob ?? option["모델확률"] ?? null,
    is_market_favorite: option._is_current_favorite,
    overround: option._current_overround ?? candidate.overround,
    payout: option._current_payout != null
      ? Number((option._current_payout * 100).toFixed(2)) : candidate.payout,
    ...(option._live ? {
      _live: true,
      odds_observed_at: option._live_observed_at,
    } : {}),
  };
  if (grade) {
    next = { ...next, bin: grade.bin, hist_roi: grade.roi, hist_n: grade.n };
  } else if (Number(candidate.odds) !== currentOdds) {
    next = { ...next, bin: null, hist_roi: null, hist_n: null };
  }
  if (marketChanged) {
    next = {
      ...next,
      _odds_metrics_stale: true,
      market_prob: null,
      failure_prob: null,
      hit_est: null,
      upset_risk: null,
      expected_roi: null,
      calibrated_hit_est: null,
      calibrated_expected_roi: null,
      conservative_hit_est: null,
      conservative_expected_roi: null,
      calibration_min_n: null,
    };
  }
  return next;
}

/** today_combo의 모든 선택을 adjusted game 옵션에 다시 연결한다. */
export function syncTodayOdds(today, games, grades) {
  if (!today) return today;
  const current = optionMap(games);
  const sync = (candidate) => syncCandidate(candidate, current, grades);
  const candidates = (today.candidates || []).map(sync);
  const plans = (today.plans || []).map((plan) => {
    const picks = (plan.picks || []).map(sync);
    const actualOdds = picks.reduce((total, pick) => total * Number(pick?.odds || 0), 1);
    const metricsStale = picks.some((pick) => pick?._odds_metrics_stale);
    return {
      ...plan,
      picks,
      ...(Number.isFinite(actualOdds) && actualOdds > 1
        ? { actual_odds: Number(actualOdds.toFixed(2)) } : {}),
      ...(metricsStale ? {
        hit_est: null,
        upset_risk: null,
        expected_roi: null,
        calibrated_hit_est: null,
        calibrated_expected_roi: null,
        conservative_hit_est: null,
        conservative_expected_roi: null,
        calibration_min_n: null,
      } : {}),
    };
  });
  return {
    ...today,
    candidates,
    plans,
    solo: sync(today.solo),
  };
}
