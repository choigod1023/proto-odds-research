import { odds, pct } from "../lib/fmt.js";

export default function PregameRecommendation({ recommendation }) {
  const {option, price, probability, estimatedRoi} = recommendation;
  return <span className="grid gap-1 rounded border border-rule bg-panel px-2 py-1" role="group" aria-label="경기 추천 1픽">
    <small>경기 추천 1픽 · 수익성 미검증</small>
    <b>{option.market}{option.label ? ` ${option.label}` : ""} · {option.선택} · <span className="tnum">{odds(price)}배</span></b>
    <small>배당 기준 적중 가능성 {pct(probability)} · 추정 ROI {estimatedRoi > 0 ? "+" : ""}{pct(estimatedRoi)} · 무효 제외</small>
  </span>;
}
