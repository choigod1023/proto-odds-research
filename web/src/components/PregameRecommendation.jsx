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
  const sports = {sc: "축구", bs: "야구", bk: "농구", vl: "배구", unknown: "종목 미분류"};
  const breakdown = (groups, labels, title) => groups && Object.keys(groups).length > 0 &&
    <div className="my-2 grid gap-1" role="group" aria-label={title}>
      <b>{title}</b>
      {Object.entries(groups).map(([key, value]) => <p key={key} className="m-0">
        {labels[key] || key} · {value.hits}/{value.decided} 적중 ({percent(value.hit_rate)})
        {" · "}실현 ROI {percent(value.roi)} · 대기 {value.pending}경기
      </p>)}
    </div>;
  return <section className="my-3 text-[12px] leading-6" aria-label="경기 추천 1픽 성적">
    <b>경기 추천 1픽 성적</b>
    <p className="my-1">기록 {data.captured}경기 · 정산 {data.settled}경기 · 대기 {data.pending}경기 · 무효 {data.refunds}경기</p>
    <p className="my-1">적중률 {percent(data.hit_rate)} ({data.hits}/{data.decided}) · 실현 ROI {percent(data.roi)}</p>
    <p className="my-1 text-ink3">사전 원장 도입 이후 경기당 1픽 · 공식 프로토 결과 · 가상 1단위 기준</p>
    {breakdown(data.by_sport, sports, "종목별 1픽 성적")}
    {breakdown(data.by_market, {}, "시장별 1픽 성적")}
    {(data.by_league || data.by_selection_source) && <details>
      <summary>리그·선택 근거별 성적</summary>
      {breakdown(data.by_league, {}, "리그별 1픽 성적")}
      {breakdown(data.by_selection_source, {
        tipster_vote: "픽스터 투표", market_fallback: "무투표 시장 대체",
        market_fallback_no_exact_line_votes: "기준점 일치 투표 없음 · 시장 대체",
        frozen_league_handicap_model: "리그 핸디캡 모델", unknown: "선택 근거 미분류",
      }, "선택 근거별 1픽 성적")}
      {breakdown(data.by_policy, {}, "정책별 1픽 성적")}
    </details>}
    {Number.isFinite(data.selection_rule?.preferred_odds) && <p className="my-1 text-ink3">
      이 1픽은 배당 {Number(data.selection_rule.preferred_odds).toFixed(2)} 이상을 우선해 고릅니다.
      일반 승패 추천과 성적이 다를 수 있으며, 배당 기반 확률은 검증된 모델 적중률이 아닙니다.
      표본이 적은 종목의 적중률은 크게 달라질 수 있습니다.
    </p>}
  </section>;
}
