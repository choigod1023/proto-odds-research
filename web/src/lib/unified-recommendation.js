import { gradeOf } from "./fmt.js";
import { eligibleFinalSelections, finalRecommendedSelection,
  hitProbabilityOf, qualifiedUnderdogSelections } from "./recommendation-policy.js";
import { pickNextLegs, ticketMetrics } from "./today-plan.js";
import { resolveDecisionOption } from "./decision-view-model.js";

const clean = (value) => String(value ?? "").trim();

export const DAILY_HIGHLIGHT_MIN_HIT = 0.55;
export const DAILY_HIGHLIGHT_LIMIT = 5;

/** 조합을 만들지 않고 경기별 최종 후보 가운데 오늘 표시할 소수만 고른다. */
export function dailyHighlightedSelections(candidates = []) {
  return [...eligibleFinalSelections(candidates)]
    .filter((selection) => hitProbabilityOf(selection) >= DAILY_HIGHLIGHT_MIN_HIT)
    .sort((a, b) =>
      hitProbabilityOf(b) - hitProbabilityOf(a) ||
      Number(a?.odds ?? a?.["배당"] ?? Infinity) -
        Number(b?.odds ?? b?.["배당"] ?? Infinity) ||
      String(a?.kickoff_at || a?.date || "").localeCompare(
        String(b?.kickoff_at || b?.date || ""),
      ) || selectionKey(a, a?.round).localeCompare(selectionKey(b, b?.round)))
    .slice(0, DAILY_HIGHLIGHT_LIMIT);
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
      memberships.set(key, { selection, recommended: false, solo: false, targets: [] });
    }
    return memberships.get(key);
  };

  // 하이라이트의 기준은 조합 포함 여부가 아니라 생성기가 확정한 경기별 후보다.
  // plans/solo 정보는 기존 데이터 호환과 설명용으로만 보존한다.
  dailyHighlightedSelections(today?.candidates || []).forEach((selection) => {
    const membership = ensure(selection);
    if (membership) membership.recommended = true;
  });

  if (today?.solo) {
    const membership = ensure(today.solo);
    if (membership) membership.solo = true;
  }
  (today?.plans || []).filter((plan) => plan?.ok).forEach((plan) => {
    (plan.picks || []).forEach((selection) => {
      const membership = ensure(selection);
      if (!membership || membership.targets.some((target) => Number(target) === Number(plan.target))) return;
      membership.targets.push(plan.target);
    });
  });
  return memberships;
}

const selectionGroupKey = (selection, round = selection?.round) => {
  const gameNo = selection?.game_no ?? selection?.["게임번호"];
  const label = selection?.market_label ?? selection?.label ?? "";
  return [round, gameNo, selection?.market, label].map(clean).join("|");
};

/** 생성 단계에서 하나로 확정한 추천을 현재(실시간 배당 반영) 선택지에 다시 연결한다. */
export function canonicalOption(game, options = game?.options || []) {
  if (game?._liveOddsChanged || game?._liveStarted) return null;
  const current = resolveDecisionOption(game, options);
  if (!current) return null;
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
export function alignTodayRecommendations(today, games = []) {
  if (!today) return today;
  const inputCandidates = today.candidates || [];
  const canonical = new Map((games || []).flatMap((game) => {
    const option = canonicalOption(game, game?.options || []);
    return option ? [[selectionGroupKey(option, game.round), {
      key: selectionKey(option, game.round),
      basis: game?.decision_snapshot ? "game-decision" : "market-fallback",
      option,
      game,
    }]] : [];
  }));
  const candidateGroups = new Map();
  inputCandidates.forEach((candidate) => {
    const key = selectionGroupKey(candidate, candidate?.round);
    if (!candidateGroups.has(key)) candidateGroups.set(key, candidate);
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
    }];
  });
  const candidates = eligibleFinalSelections(repriced).filter((candidate) => {
    const wanted = canonical.get(selectionGroupKey(candidate, candidate?.round));
    return wanted?.key === selectionKey(candidate, candidate?.round);
  });
  const plans = (today.plans || []).map((plan) => {
    const bins = plan.bins || [];
    const picks = bins.length
      ? pickNextLegs(candidates, bins, today.year, Number(plan.target)) : null;
    if (!picks) return {
      ...plan, ok: false, picks: [],
      why: "최종 픽 전환 뒤 목표 배당을 구성할 수 없다",
    };
    return {
      ...plan,
      ok: true,
      picks,
      legs: picks.length,
      ...ticketMetrics(picks),
      why: "경기별 최종 픽 하나로 다시 계산했다",
    };
  });
  const solo = today.solo
    ? candidates.find((candidate) =>
      selectionGroupKey(candidate, candidate?.round) ===
      selectionGroupKey(today.solo, today.solo?.round)) || null
    : null;
  const gameModelCandidates = candidates.filter(
    (candidate) => candidate.recommendation_basis === "game-decision",
  ).length;
  const marketFallbackCandidates = candidates.filter(
    (candidate) => candidate.recommendation_basis === "market-fallback",
  ).length;
  return {
    ...today,
    candidates,
    plans,
    solo,
    alignment: {
      input_candidates: inputCandidates.length,
      safe_candidates: candidates.length,
      game_model_candidates: gameModelCandidates,
      market_fallback_candidates: marketFallbackCandidates,
      dropped_by_safety: inputCandidates.length - candidates.length,
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
