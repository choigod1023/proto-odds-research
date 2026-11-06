import { odds, pct } from "../lib/fmt.js";

export default function PregameRecommendation({ recommendation }) {
  const {option, price, probability, estimatedRoi, frozen, result} = recommendation;
  const status = {hit: "적중", miss: "미적중", void: "무효"}[result] || "공식 결과 대기";
  return <span className="grid gap-1 rounded border border-rule bg-panel px-2 py-1" role="group" aria-label="경기 추천 1픽">
    <small>경기 추천 1픽 · {frozen ? `사전 보존 · ${status}` : "수익성 미검증"}</small>
    <b>{option.market}{option.label ? ` ${option.label}` : ""} · {option.선택} · <span className="tnum">{odds(price)}배</span></b>
    <small>배당 기준 적중 가능성 {pct(probability)} · 추정 ROI {estimatedRoi > 0 ? "+" : ""}{pct(estimatedRoi)} · 무효 제외</small>
  </span>;
}

export function FeaturedPickPerformance({ data }) {
  if (!data) return null;
  const percent = (value) => Number.isFinite(value) ? `${(100 * value).toFixed(2)}%` : "미산출";
  return <section className="my-3 text-[12px] leading-6" aria-label="경기 추천 1픽 성적">
    <b>경기 추천 1픽 성적</b>
    <p className="my-1">기록 {data.captured}경기 · 정산 {data.settled}경기 · 대기 {data.pending}경기 · 무효 {data.refunds}경기</p>
    <p className="my-1">적중률 {percent(data.hit_rate)} ({data.hits}/{data.decided}) · 실현 ROI {percent(data.roi)}</p>
    <p className="my-1 text-ink3">사전 원장 도입 이후 경기당 1픽 · 공식 프로토 결과 · 가상 1단위 기준</p>
  </section>;
}
