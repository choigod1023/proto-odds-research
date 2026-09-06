import { gamePhase, decisionFrozen } from "./match-status.js";

const present = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));

// These are observed match facts, not post-hoc claims that they caused the model's pick.
export function matchEvidence(game) {
  const frozen = decisionFrozen(game) || gamePhase(game) !== "upcoming";
  if (frozen) return { frozen: true, facts: [], summary: "사전 픽은 고정되어 있습니다. 당시 근거와 결과를 구분해 확인하세요." };
  const facts = [];
  for (const side of ["home", "away"]) {
    const form = game[`form_${side}`];
    if (form?.last10) facts.push(`${game[side]} 최근 10경기 ${form.last10}`);
    if (present(form?.avg_scored) && present(form?.avg_conceded)) {
      facts.push(`${game[side]} 최근 경기당 득점 ${Number(form.avg_scored).toFixed(1)} · 실점 ${Number(form.avg_conceded).toFixed(1)}`);
    }
    const starter = game["선발"]?.[`${side}_detail`];
    const era = starter?.stats?.era ?? starter?.era;
    if (game.sport === "bs" && starter?.name && present(era)) {
      facts.push(`${game[side]} 선발 ${starter.name} · 평균자책점 ${Number(era).toFixed(2)}`);
    }
  }
  const internal = (game["경기근거"]?.internal || []).filter((row) => typeof row.text === "string" && row.text.trim());
  if (!facts.length) facts.push(...internal.slice(0, 2).map((row) => row.text));
  return { frozen: false, facts, summary: facts[0] || "경기력 자료 확인 중 · 추천 여부와 별도로 확인합니다." };
}
