import { gradeOf } from "./fmt.js";
import { eligibleAutoSelections } from "./recommendation-policy.js";

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
  const source = game?.["추천"];
  if (!source) return null;
  const wanted = selectionKey(source, game?.round);
  const current = (options || []).find((option) => selectionKey(option, game?.round) === wanted);
  if (!current) return null;
  return eligibleAutoSelections(options).includes(current) ? current : null;
}

export function canonicalPick(game, options, grades) {
  const option = canonicalOption(game, options);
  if (!option) return null;
  const grade = gradeOf(grades, option["배당"]);
  return { o: option, g: grade, tie: false, policy: "prediction-calibrated" };
}

/** 경기 모델 추천과 시장가격 기반 보완 선택을 서로 다른 근거로 표시한다. */
export function alignTodayRecommendations(today, games = []) {
  if (!today) return today;
  const inputCandidates = today.candidates || [];
  const canonical = new Map((games || []).flatMap((game) => {
    const option = canonicalOption(game, game?.options || []);
    return option ? [[selectionGroupKey(option, game.round), selectionKey(option, game.round)]] : [];
  }));
  const safeCandidates = eligibleAutoSelections(inputCandidates);
  const candidates = safeCandidates.map((candidate) => {
    const wanted = canonical.get(selectionGroupKey(candidate, candidate?.round));
    return {
      ...candidate,
      recommendation_basis: wanted === selectionKey(candidate, candidate?.round)
        ? "game-model-match" : "market-only",
    };
  });
  const allowed = new Set(candidates.map((candidate) => selectionKey(candidate, candidate?.round)));
  const keep = (candidate) => allowed.has(selectionKey(candidate, candidate?.round));
  const plans = (today.plans || []).map((plan) => ({
    ...plan,
    picks: (plan.picks || []).filter(keep),
  }));
  const solo = today.solo && keep(today.solo) ? today.solo : null;
  const gameModelCandidates = candidates.filter(
    (candidate) => candidate.recommendation_basis === "game-model-match",
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
      market_fallback_candidates: candidates.length - gameModelCandidates,
      dropped_by_safety: inputCandidates.length - safeCandidates.length,
    },
  };
}
