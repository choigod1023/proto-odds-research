import { gradeOf } from "./fmt.js";
import { eligibleAutoSelections } from "./recommendation-policy.js";
import { resolveDecisionOption } from "./decision-view-model.js";

const clean = (value) => String(value ?? "").trim();

export function selectionKey(selection, round = selection?.round) {
  const gameNo = selection?.game_no ?? selection?.["게임번호"];
  const choice = selection?.sel ?? selection?.["선택"];
  const label = selection?.market_label ?? selection?.label ?? "";
  return [round, gameNo, selection?.market, label, choice].map(clean).join("|");
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
  return eligibleAutoSelections(options).includes(current) ? current : null;
}

export function canonicalPick(game, options, grades) {
  const option = canonicalOption(game, options);
  if (!option) return null;
  const grade = gradeOf(grades, option["배당"]);
  return { o: option, g: grade, tie: false, policy: "market-anchored" };
}

/** 오늘 후보도 경기 카드의 v2 판정과 정확히 같은 선택만 남긴다. */
export function alignTodayRecommendations(today, games = []) {
  if (!today) return today;
  const inputCandidates = today.candidates || [];
  const canonical = new Map((games || []).flatMap((game) => {
    const option = canonicalOption(game, game?.options || []);
    return option ? [[selectionGroupKey(option, game.round), selectionKey(option, game.round)]] : [];
  }));
  const candidates = eligibleAutoSelections(inputCandidates).filter((candidate) => {
    const wanted = canonical.get(selectionGroupKey(candidate, candidate?.round));
    return wanted === selectionKey(candidate, candidate?.round);
  }).map((candidate) => ({ ...candidate, recommendation_basis: "game-decision" }));
  const allowed = new Set(candidates.map((candidate) => selectionKey(candidate, candidate?.round)));
  const keep = (candidate) => allowed.has(selectionKey(candidate, candidate?.round));
  const plans = (today.plans || []).map((plan) => ({
    ...plan,
    picks: (plan.picks || []).filter(keep),
  }));
  const solo = today.solo && keep(today.solo) ? today.solo : null;
  const gameModelCandidates = candidates.filter(
    (candidate) => candidate.recommendation_basis === "game-decision",
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
      market_fallback_candidates: 0,
      dropped_by_safety: inputCandidates.length - candidates.length,
    },
  };
}
