import { gamePhase } from "./match-status.js";

const present = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));

// These are observed match facts, not post-hoc claims that they caused the model's pick.
export function matchEvidence(game, option = null) {
  const frozen = gamePhase(game) !== "upcoming";
  if (frozen) return { frozen: true, facts: [], summary: "" };
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
  // Backend eligibility prose is not sporting evidence. Use structured facts only.
  const choice = String(option?.선택 || "");
  const awayFirst = /원정|패$/.test(choice) || choice === game.away;
  const sides = awayFirst ? ["away", "home"] : ["home", "away"];
  const recent = sides.filter((side) => game[`form_${side}`]?.last10)
    .map((side) => `${game[side]} ${game[`form_${side}`].last10}`);
  let summary = recent.length ? `최근 10경기 ${recent.join(" · ")}` : facts[0] || "";
  if (String(option?.market || "").includes("언더오버") && sides.every((side) =>
    present(game[`form_${side}`]?.avg_scored) && present(game[`form_${side}`]?.avg_conceded))) {
    summary = sides.map((side) => `${game[side]} 최근 경기당 ${Number(game[`form_${side}`].avg_scored).toFixed(1)}득점·${Number(game[`form_${side}`].avg_conceded).toFixed(1)}실점`).join(" / ");
  }
  return { frozen: false, facts, summary };
}
