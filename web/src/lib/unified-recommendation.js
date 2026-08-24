import { gradeOf } from "./fmt.js";
import { eligibleAutoSelections } from "./recommendation-policy.js";

const clean = (value) => String(value ?? "").trim();

export function selectionKey(selection, round = selection?.round) {
  const gameNo = selection?.game_no ?? selection?.["게임번호"];
  const choice = selection?.sel ?? selection?.["선택"];
  const label = selection?.market_label ?? selection?.label ?? "";
  return [round, gameNo, selection?.market, label, choice].map(clean).join("|");
}

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

/** 오늘 조합도 경기 카드와 같은 단일 추천만 사용한다. */
export function alignTodayRecommendations(today, games = []) {
  if (!today) return today;
  const allowed = new Set((games || []).flatMap((game) => {
    const option = canonicalOption(game, game?.options || []);
    return option ? [selectionKey(option, game.round)] : [];
  }));
  const keep = (candidate) => allowed.has(selectionKey(candidate, candidate?.round));
  const candidates = (today.candidates || []).filter(keep);
  const plans = (today.plans || []).map((plan) => ({
    ...plan,
    picks: (plan.picks || []).filter(keep),
  }));
  const solo = today.solo && keep(today.solo) ? today.solo : null;
  return { ...today, candidates, plans, solo };
}