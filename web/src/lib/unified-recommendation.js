import { gradeOf } from "./fmt.js";
import { eligibleFinalSelections, finalRecommendedSelection,
  hitProbabilityOf, marketOnlyRecommendedSelection,
  qualifiedUnderdogSelections, recommendationPriority } from "./recommendation-policy.js";
import { canApplyDecisionProbability, resolveDecisionOption } from "./decision-view-model.js";
import { scheduledAt } from "./match-status.js";
import { pickMatchReason } from "./pick-match-reason.js";

const clean = (value) => String(value ?? "").trim();

export const DAILY_HIGHLIGHT_MIN_HIT = 0.55;
export const DAILY_HIGHLIGHT_BASE_PER_LEAGUE = 3;
export const DAILY_HIGHLIGHT_STRONG_MIN_HIT = 0.60;

const dailyLeagueKey = (selection) => {
  const kickoff = Date.parse(selection?.kickoff_at || "");
  const date = String(selection?.date || "").match(/^(\d{2})\.(\d{2})/);
  const day = Number.isFinite(kickoff)
    ? new Date(kickoff + 9 * 3600000).toISOString().slice(0, 10)
    : date ? `${selection?.year || new Date().getFullYear()}-${date[1]}-${date[2]}` : "undated";
  return `${day}|${clean(selection?.league) || "리그 미분류"}`;
};

const recommendationRank = (a, b) =>
  hitProbabilityOf(b) - hitProbabilityOf(a) ||
  Number(b?.probability_lower_bound ?? hitProbabilityOf(b)) -
    Number(a?.probability_lower_bound ?? hitProbabilityOf(a)) ||
  Number(a?.odds ?? a?.["배당"] ?? Infinity) -
    Number(b?.odds ?? b?.["배당"] ?? Infinity) ||
  String(a?.kickoff_at || a?.date || "").localeCompare(
    String(b?.kickoff_at || b?.date || ""),
  ) || selectionKey(a, a?.round).localeCompare(selectionKey(b, b?.round));

export function recommendationDisplay(selection) {
  if (!selection) return null;
  const hit = hitProbabilityOf(selection);
  const lower = Number(selection?.probability_lower_bound);
  const odds = Number(selection?.odds ?? selection?.["배당"]);
  const preferred = recommendationPriority(selection) === 1;
  const live = selection?.price_source === "live_odds" || selection?._live === true;
  const validated = selection?.has_validated_edge === true;
  const parts = [
    `적중 ${Number.isFinite(hit) ? `${(hit * 100).toFixed(1)}%` : "계산 불가"}`,
    `배당 ${Number.isFinite(odds) ? odds.toFixed(2) : "확인 불가"}${preferred ? " · 우선구간" : " · 저배당 보조"}`,
    validated && Number.isFinite(lower)
      ? `검증 하한 ${(lower * 100).toFixed(1)}%`
      : "Shin 시장 기준",
    live ? "실시간 배당" : "발매 스냅샷",
  ];
  return { parts, text: parts.join(" · "), preferred, validated, live };
}

/** Selection policy stays separate from the match evidence shown to the user. */
export function dailyRecommendationDecisions(candidates = []) {
  const eligible = eligibleFinalSelections(candidates);
  const eligibleSet = new Set(eligible);
  const selected = new Set(dailyHighlightedSelections(candidates));
  const leagueRows = new Map();
  eligible.forEach((selection) => {
    if (hitProbabilityOf(selection) < DAILY_HIGHLIGHT_MIN_HIT) return;
    const league = dailyLeagueKey(selection);
    if (!leagueRows.has(league)) leagueRows.set(league, []);
    leagueRows.get(league).push(selection);
  });
  const rankBySelection = new Map();
  leagueRows.forEach((rows) => {
    const primary = rows.filter((selection) => recommendationPriority(selection) === 1);
    const pool = (primary.length ? primary : rows).sort(recommendationRank);
    pool.forEach((selection, index) => rankBySelection.set(selection, index + 1));
  });
  return candidates.map((selection) => {
    const hit = hitProbabilityOf(selection);
    const league = clean(selection?.league) || "리그 미분류";
    const rows = leagueRows.get(dailyLeagueKey(selection)) || [];
    const hasPrimary = rows.some((row) => recommendationPriority(row) === 1);
    const preferred = recommendationPriority(selection) === 1;
    const rank = rankBySelection.get(selection) || null;
    let reason;
    if (!eligibleSet.has(selection)) {
      reason = "자동 추천 안전조건을 통과하지 못했다.";
    } else if (!(hit >= DAILY_HIGHLIGHT_MIN_HIT)) {
      reason = `예상 적중 ${Number.isFinite(hit) ? `${(hit * 100).toFixed(1)}%` : "계산 불가"}로 55% 기준에 미달했다.`;
    } else if (!preferred && hasPrimary) {
      reason = "같은 리그에 1.50~2.20 우선 배당 후보가 있어 저배당 보조 후보에서 제외했다.";
    } else if (!selected.has(selection)) {
      reason = `해당 날짜 리그 내 ${rank || 4}순위이며 추가 추천 기준 60%에 미달했다.`;
    } else if (rank && rank <= DAILY_HIGHLIGHT_BASE_PER_LEAGUE) {
      reason = `55% 기준을 통과했고 해당 날짜 ${league} 유효 후보 중 ${rank}위라 기본 추천 3개에 포함했다.`;
    } else {
      reason = `해당 날짜 리그 기본 3개 밖이지만 예상 적중 ${(hit * 100).toFixed(1)}%로 추가 기준 60%를 통과했다.`;
    }
    const matchReason = selection.match_reason || pickMatchReason(null, null);
    return {
      selection,
      recommended: selected.has(selection),
      reason: matchReason.reason,
      counterReason: matchReason.counterReason,
      evidence: matchReason.evidence,
      reasonStatus: matchReason.status,
      policyReason: reason,
      display: recommendationDisplay(selection),
      leagueRank: rank,
    };
  });
}

/** 날짜별 리그 기본 3개와 60% 이상 추가 후보를 고른다. 기준 미달은 채우지 않는다. */
export function dailyHighlightedSelections(candidates = []) {
  const byLeague = new Map();
  eligibleFinalSelections(candidates)
    .filter((selection) => hitProbabilityOf(selection) >= DAILY_HIGHLIGHT_MIN_HIT)
    .forEach((selection) => {
      const league = dailyLeagueKey(selection);
      if (!byLeague.has(league)) byLeague.set(league, []);
      byLeague.get(league).push(selection);
    });
  return [...byLeague.values()].flatMap((rows) => {
    const primary = rows.filter((selection) => recommendationPriority(selection) === 1);
    const pool = (primary.length ? primary : rows).sort(recommendationRank);
    const base = pool.slice(0, DAILY_HIGHLIGHT_BASE_PER_LEAGUE);
    const strong = pool.slice(DAILY_HIGHLIGHT_BASE_PER_LEAGUE)
      .filter((selection) => hitProbabilityOf(selection) >= DAILY_HIGHLIGHT_STRONG_MIN_HIT);
    return [...base, ...strong];
  }).sort(recommendationRank);
}

export function selectionKey(selection, round = selection?.round) {
  const gameNo = selection?.game_no ?? selection?.["게임번호"];
  const choice = selection?.sel ?? selection?.["선택"];
  const label = selection?.market_label ?? selection?.label ?? "";
  return [round, gameNo, selection?.market, label, choice].map(clean).join("|");
}

/**
 * 오늘의 경기별 최종 추천을 경기 카드에 붙이기 위한 단일 인덱스다.
 * 별도 후보 화면이 같은 경기를 다시 해석하지 않고, 이미 정렬된 선택 키만 공유한다.
 */
export function buildTodayMemberships(today) {
  const memberships = new Map();
  const ensure = (selection) => {
    if (!selection) return null;
    const key = selectionKey(selection, selection?.round);
    if (!memberships.has(key)) {
      memberships.set(key, { selection, recommended: false, display: null });
    }
    return memberships.get(key);
  };

  // 하이라이트의 기준은 자동 조합이 아니라 생성기가 확정한 경기별 후보다.
  dailyRecommendationDecisions(today?.candidates || []).forEach((decision) => {
    const membership = ensure(decision.selection);
    if (membership) {
      membership.recommended = decision.recommended;
      membership.display = decision.display;
      membership.reason = decision.reason;
      membership.counterReason = decision.counterReason;
    }
  });
  return memberships;
}

const selectionGroupKey = (selection, round = selection?.round) => {
  const gameNo = selection?.game_no ?? selection?.["게임번호"];
  const label = selection?.market_label ?? selection?.label ?? "";
  return [round, gameNo, selection?.market, label].map(clean).join("|");
};

const eventMarketKey = (selection) => {
  const label = selection?.market_label ?? selection?.label ?? "";
  if (![selection?.date, selection?.home, selection?.away].every((value) => clean(value))) {
    return selectionGroupKey(selection, selection?.round);
  }
  return [selection?.date, selection?.home, selection?.away, selection?.market, label]
    .map(clean).join("|");
};

const validProbability = (value) => {
  const probability = Number(value);
  return Number.isFinite(probability) && probability > 0 && probability < 1
    ? probability : null;
};

function snapshotMatchesCurrentOption(game, option) {
  const snapshot = game?.decision_snapshot;
  const snapshotMarket = validProbability(snapshot?.probability?.market);
  const currentMarket = validProbability(option?.["시장확률"]);
  return Boolean(
    snapshot && option &&
    snapshot.selection_id === option.selection_id &&
    snapshot.offer_id === option.offer_id &&
    snapshotMarket != null && currentMarket != null &&
    Math.abs(snapshotMarket - currentMarket) <= 1e-9,
  );
}

function snapshotCanAffectProbability(game, option) {
  const model = game?.decision_snapshot?.model || {};
  return snapshotMatchesCurrentOption(game, option) &&
    canApplyDecisionProbability(model) &&
    validProbability(game?.decision_snapshot?.probability?.final) != null;
}

/** 현재 선택·가격 revision 하나에서 확률과 승인 메타데이터를 함께 다시 만든다. */
function currentProbabilityMetadata(game, option) {
  const market = validProbability(option?.["시장확률"]);
  const snapshot = snapshotMatchesCurrentOption(game, option)
    ? game.decision_snapshot : null;
  const model = snapshot?.model || {};
  const probability = snapshot?.probability || {};
  const operational = model.status === "operational" && model.promotion_gate === "passed";
  const validated = canApplyDecisionProbability(model);
  const policyAuthorized = operational && model.policy_authorized === true;
  const currentFinal = validProbability(probability.final);
  const applied = currentFinal != null && validated;
  const final = applied ? currentFinal : market;
  const rawInterval = applied && Array.isArray(probability.residual_interval)
    ? probability.residual_interval.map(validProbability) : [];
  const intervalLower = rawInterval.length === 2 && rawInterval.every((value) => value != null) &&
    final != null && rawInterval[0] <= final
    ? rawInterval[0] : null;
  const marketLower = market != null && final != null ? Math.min(market, final) : market;

  return {
    predicted_hit_prob: final,
    probability_source: applied
      ? probability.basis || "validated_final_probability"
      : "shin_market_fallback",
    probability_lower_bound: intervalLower ?? marketLower,
    probability_interval: intervalLower == null ? null : rawInterval,
    uncertainty_source: intervalLower == null
      ? "shin_market_fallback" : "validated_residual_interval",
    validated_uncertainty_available: intervalLower != null,
    has_validated_edge: applied,
    // 정책 플래그는 관찰용이다. 검증된 artifact가 아니면 최종확률을 바꾸지 않는다.
    policy_authorized: policyAuthorized,
    decision_pipeline_applied: applied,
    selection_basis: applied ? "validated_decision_pipeline" : "shin_market_fallback",
    decision_id: snapshot?.decision_id || null,
    decision_model: snapshot ? model.operating_version || null : null,
    decision_pipeline_status: snapshot ? model.status || null : "market_fallback",
    decision_promotion_gate: snapshot ? model.promotion_gate || null : null,
    decision_artifact_hash: snapshot ? model.artifact_hash || null : null,
    decision_evidence_ids: snapshot
      ? (snapshot.evidence || []).map((row) => row?.id).filter(Boolean) : [],
  };
}

/** 생성 단계에서 하나로 확정한 추천을 현재(실시간 배당 반영) 선택지에 다시 연결한다. */
export function canonicalOption(game, options = game?.options || [], { allowStarted = false } = {}) {
  if (game?._liveOddsChanged || (game?._liveStarted && !allowStarted)) return null;
  const current = resolveDecisionOption(game, options);
  if (!current) return null;
  if (!snapshotCanAffectProbability(game, current)) {
    return marketOnlyRecommendedSelection(options);
  }
  return finalRecommendedSelection(options) === current ? current : null;
}

export function canonicalPick(game, options, grades) {
  const option = canonicalOption(game, options);
  if (!option) return null;
  const grade = gradeOf(grades, option["배당"]);
  return {
    o: option, g: grade, tie: false,
    policy: game?.decision_snapshot ? "market-anchored" : "market-fallback",
  };
}

/** 오늘 후보도 경기 카드의 v2 판정과 정확히 같은 선택만 남긴다. */
export function alignTodayRecommendations(today, games = [], now = Date.now()) {
  today = today || { candidates: [], odds_bins: [] };
  const inputCandidates = today.candidates || [];
  const canonical = new Map();
  (games || []).forEach((game) => {
    // 이미 저장된 사전 추천을 경기 시작 뒤 결과 추적에 연결할 때만 시작 경기를 허용한다.
    // canonicalPick의 기본 동작은 계속 시작 후 새 추천 생성을 막는다.
    const option = canonicalOption(game, game?.options || [], { allowStarted: true });
    if (!option) return;
    const eventKey = eventMarketKey({
      ...option, round: game.round, date: game.date, home: game.home, away: game.away,
    });
    const wanted = {
      key: selectionKey(option, game.round),
      basis: snapshotMatchesCurrentOption(game, option) ? "game-decision" : "market-fallback",
      option,
      game,
    };
    const previous = canonical.get(eventKey);
    if (!previous || Number(game.round) > Number(previous.game.round)) canonical.set(eventKey, wanted);
  });
  const candidateGroups = new Map();
  inputCandidates.forEach((candidate) => {
    const key = eventMarketKey(candidate);
    if (!candidateGroups.has(key)) candidateGroups.set(key, candidate);
  });
  // The recommendation API can lag behind picks_v2. Recover only already
  // recorded pregame decisions in today's / next morning's candidate window.
  // Never invent a historical recommendation after kickoff.
  let recovered = 0;
  const todayDay = Math.floor((now + 9 * 3600000) / 86400000);
  canonical.forEach(({ game, option }, key) => {
    if (candidateGroups.has(key)) return;
    const record = game.prediction_record;
    const kickoff = scheduledAt(game);
    const captured = Date.parse(record?.captured_at || "");
    const kickoffDay = Math.floor((kickoff + 9 * 3600000) / 86400000);
    const hour = new Date(kickoff + 9 * 3600000).getUTCHours();
    if (!record || record.selection_id !== option.selection_id
        || record.prediction_snapshot_id !== game.decision_snapshot?.decision_id
        || !snapshotMatchesCurrentOption(game, option)
        || Number(record.odds) !== Number(option["배당"])
        || game._liveStarted || !["경기전", "배당대기"].includes(game.status)
        || !Number.isFinite(captured) || captured > now || captured >= kickoff
        || !Number.isFinite(kickoff) || kickoff <= now
        || !(kickoffDay === todayDay || (kickoffDay === todayDay + 1 && hour < 12))) return;
    candidateGroups.set(key, {
      date: game.date, year: game.year, home: game.home, away: game.away,
      league: game.league, sport: game.sport, round: game.round,
      market: option.market, market_label: option.label || "",
      kickoff_at: new Date(kickoff).toISOString(),
      event_key: `${kickoff}|${game.home}|${game.away}`,
      recommended_at: record.captured_at,
      price_source: "recorded_picks_v2",
    });
    recovered += 1;
  });
  const grades = { odds_bins: today.odds_bins || [] };
  const repriced = [...candidateGroups.entries()].flatMap(([groupKey, candidate]) => {
    const wanted = canonical.get(groupKey);
    if (!wanted) return [];
    const option = wanted.option;
    const reversal = qualifiedUnderdogSelections(wanted.game.options || []).includes(option);
    const currentOdds = Number(option?.["배당"]);
    const currentProbability = Number(option?.["시장확률"]);
    const grade = gradeOf(grades, currentOdds);
    const overround = Number(option?._liveOverround ?? candidate.overround);
    const probabilityMetadata = currentProbabilityMetadata(wanted.game, option);
    return [{
      ...candidate,
      round: wanted.game.round,
      game_no: String(option?.["게임번호"] ?? candidate.game_no),
      market: option?.market,
      market_label: option?.label || "",
      sel: option?.["선택"],
      odds: currentOdds,
      bin: grade?.bin || candidate.bin,
      market_prob: currentProbability,
      overround: Number.isFinite(overround) ? overround : candidate.overround,
      payout: Number.isFinite(overround) && overround > 0
        ? Number((100 / overround).toFixed(2)) : candidate.payout,
      hist_roi: grade?.roi ?? candidate.hist_roi,
      hist_n: grade?.n ?? candidate.hist_n,
      is_market_favorite: !reversal,
      final_reversal: reversal,
      model_prob: Number(option?.["모델확률"]),
      recommendation_basis: wanted.basis,
      // Rebuild from this exact game's structured context, never old candidate prose.
      match_reason: pickMatchReason(wanted.game, option, now),
      ...probabilityMetadata,
    }];
  });
  const candidates = eligibleFinalSelections(repriced).filter((candidate) => {
    const wanted = canonical.get(eventMarketKey(candidate));
    return wanted?.key === selectionKey(candidate, candidate?.round);
  });
  const gameModelCandidates = candidates.filter(
    (candidate) => candidate.recommendation_basis === "game-decision",
  ).length;
  const marketFallbackCandidates = candidates.filter(
    (candidate) => candidate.recommendation_basis === "market-fallback",
  ).length;
  return {
    ...today,
    candidates,
    plans: [],
    solo: null,
    recommendation: { action: "disabled", recommended_target: null,
      why: "자동 조합 추천을 사용하지 않는다" },
    alignment: {
      input_candidates: inputCandidates.length,
      safe_candidates: candidates.length,
      game_model_candidates: gameModelCandidates,
      market_fallback_candidates: marketFallbackCandidates,
      dropped_by_safety: inputCandidates.length + recovered - candidates.length,
      ...(recovered ? { recovered_from_picks: recovered } : {}),
    },
  };
}

/** 경기의 모든 선택지 중 오늘의 경기별 최종 추천 선택지를 찾는다. */
export function todaySelectionForGame(memberships, options = [], round) {
  for (const option of options || []) {
    const membership = memberships?.get(selectionKey(option, round));
    if (membership) return { option, membership };
  }
  return { option: null, membership: null };
}
