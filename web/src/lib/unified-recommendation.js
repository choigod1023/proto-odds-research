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

/** 경기 카드 추천을 우선하되, 추천이 없는 경기는 안전한 시장 최유력으로 보완한다. */
export function alignTodayRecommendations(today, games = []) {
  if (!today) return today;
  const canonical = new Map((games || []).flatMap((game) => {
    const option = canonicalOption(game, game?.options || []);
    return option ? [[selectionGroupKey(option, game.round), selectionKey(option, game.round)]] : [];
  }));
  const candidates = eligibleAutoSelections(today.candidates || []).map((candidate) => {
    const wanted = canonical.get(selectionGroupKey(candidate, candidate?.round));
    return {
      ...candidate,
      recommendation_basis: wanted === selectionKey(candidate, candidate?.round)
        ? "game-model" : "market-favorite-fallback",
    };
  });
  const allowed = new Set(candidates.map((candidate) => selectionKey(candidate, candidate?.round)));
  const keep = (candidate) => allowed.has(selectionKey(candidate, candidate?.round));
  const plans = (today.plans || []).map((plan) => ({
    ...plan,
    picks: (plan.picks || []).filter(keep),
  }));
  const solo = today.solo && keep(today.solo) ? today.solo : null;
  return { ...today, candidates, plans, solo };
}
