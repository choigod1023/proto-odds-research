import { recordedMarketOffers } from "../lib/recorded-market-offers.js";
import { scheduledAt } from "../lib/match-status.js";

// Only the current, unambiguous product can be attached to a recommendation.
export function currentMarketRecommendations(game, options = []) {
  const data = game?.market_recommendations;
  if (data?.version !== "multi-market-recommendations-v1" || !Array.isArray(data.items) || game?._liveOddsChanged) return [];
  const offers = recordedMarketOffers(options, game);
  const seen = new Set();
  return data.items.flatMap((item) => {
    if (!item || !["핸디캡", "언더오버"].includes(item.market) || !item.selection_id || !item.offer_id) return [];
    const matches = options.filter((o) => o.selection_id === item.selection_id && o.offer_id === item.offer_id);
    if (matches.length !== 1) return [];
    const option = matches[0];
    if (options.filter((o) => o.selection_id === item.selection_id).length !== 1 ||
      option.market !== item.market || (option.label || "") !== (item.label || "") ||
      option["선택"] !== item.selection || typeof item.line !== "number" || !Number.isFinite(item.line) ||
      (option.line == null || option.line === "" || Number(option.line) !== item.line) || Number(option["배당"]) !== item.odds) return [];
    const offer = offers.find((o) => o.gameNo === String(option["게임번호"]) && o.market === item.market && o.label === (item.label || ""));
    if (!offer || !["full", "full_game"].includes(offer.period) || !offer.settlementScope ||
        !offer.selections.some((s) => s.name === item.selection)) return [];
    const key = `${item.selection_id}|${item.offer_id}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ item, option, generatedAt: data.generated_at, comparisonVersion: data.pick_comparison_version }];
  });
}

// The record is the authority after T-30; a flag on the live game is not a snapshot.
export function savedMarketRecommendations(game) {
  const record = game?.market_prediction_record ?? game?.prediction_record;
  const data = record?.market_recommendations;
  const start = scheduledAt(game);
  const captured = Date.parse(record?.captured_at);
  const generated = Date.parse(data?.generated_at);
  const kickoff = Date.parse(record?.kickoff);
  const fixture = record?.fixture;
  const fields = ["sport", "league", "home", "away", "year", "round", "date"];
  if (typeof record?.prediction_snapshot_id !== "string" || !record.prediction_snapshot_id.trim() ||
      !Number.isFinite(start) || kickoff !== start || !Number.isFinite(captured) || !Number.isFinite(generated) ||
      captured > start - 1800000 || generated > captured ||
      (fixture && !fields.every((key) => game?.[key] != null && game[key] !== "" &&
        fixture[key] != null && String(fixture[key]) === String(game[key]))) ||
      (record.event_id && record.event_id !== game?.event_id) ||
      data?.version !== "multi-market-recommendations-v1" || !Array.isArray(data.items)) return [];
  const savedOptions = Array.isArray(record.market_recommendation_options) ? record.market_recommendation_options : [];
  const currentOptions = Array.isArray(game.options) ? game.options : [];
  const sourceResults = record.market_recommendation_results ?? game.market_recommendation_results;
  const results = Array.isArray(sourceResults) ? sourceResults : Array.isArray(sourceResults?.items) ? sourceResults.items :
    sourceResults && typeof sourceResults === "object" ? Object.entries(sourceResults).map(([offer_id, row]) =>
      row && typeof row === "object" && (!row.offer_id || row.offer_id === offer_id) ? {...row, offer_id} : null) : [];
  const sameFixture = Boolean(fixture) || Boolean(record.event_id && record.event_id === game?.event_id);
  const seen = new Set();
  return data.items.flatMap((item) => {
    if (!item || !["핸디캡", "언더오버"].includes(item.market) || !item.selection_id || !item.offer_id ||
        typeof item.line !== "number" || !Number.isFinite(item.line) ||
        typeof item.odds !== "number" || !Number.isFinite(item.odds) || item.odds <= 1 ||
        !item.game_no || !item.settlement_rule) return [];
    const findOption = (options, saved) => {
      const matching = options.filter((option) => option?.selection_id === item.selection_id);
      if (matching.length !== 1) return null;
      const option = matching[0];
      return option.offer_id === item.offer_id && option.market === item.market &&
        (option.label || "") === (item.label || "") && option["선택"] === item.selection &&
        String(option["게임번호"]) === String(item.game_no) && option.line != null && option.line !== "" &&
        Number(option.line) === item.line && (!saved || Number(option["배당"]) === item.odds) ? option : null;
    };
    const option = (sameFixture && findOption(savedOptions, true)) || findOption(currentOptions, false);
    if (!option) return [];
    const key = `${item.selection_id}|${item.offer_id}`;
    if (seen.has(key)) return [];
    seen.add(key);
    const matches = results.filter((r) => r && (!r.selection_id || r.selection_id === item.selection_id) && r.offer_id === item.offer_id &&
      data.items.filter((candidate) => candidate?.offer_id === item.offer_id).length === 1 &&
      r.market === item.market && r.line != null && Number(r.line) === item.line &&
      (!r.model || r.model === item.model) && (!r.settlement_rule || r.settlement_rule === item.settlement_rule) &&
      Number(r.odds) === item.odds && (!r.prediction_snapshot_id || r.prediction_snapshot_id === record.prediction_snapshot_id) &&
      ["hit", "miss", "void"].includes(r.result));
    let result = matches.length === 1 ? matches[0] : null;
    if (result) {
      const net = typeof result.net_units === "number" || (typeof result.net_units === "string" && result.net_units.trim()) ? Number(result.net_units) : NaN;
      const expected = result.result === "hit" ? item.odds - 1 : result.result === "miss" ? -1 : 0;
      result = { ...result, net: Number.isFinite(net) && Math.abs(net - expected) < 1e-8 ? net : null };
    }
    return [{ item, option, result, saved: true, capturedAt: record.captured_at, generatedAt: data.generated_at,
      comparisonVersion: data.pick_comparison_version }];
  });
}

export function resultLabel(result) {
  if (!result) return "결과 대기";
  const name = {hit: "적중", miss: "미적중", void: "무효"}[result.result];
  return `${name}${result.net == null ? "" : ` · 순손익 ${result.net > 0 ? "+" : ""}${result.net.toFixed(2)}단위 (베팅액 1단위 기준 · 지갑 거래 아님)`}`;
}

const fraction = (n) => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1;
const percent = (n) => `${(n * 100).toFixed(1)}%`;
const probabilityLabel = (item) => ({
  m0_score_model_conditional_decisive: "M0 모델 · 무효 제외 조건부",
  market_conditional_decisive: "배당 기반 · 무효 제외 조건부",
  normalized_inverse_market_conditional_decisive: "배당 기반 · 무효 제외 조건부",
})[item.probability_basis] || null;
const modelLabel = (model) => ({'tipster-market-vote-v1':'같은 기준점의 픽스터 투표','market-baseline-v1':'현재 배당 기준'})[model] || model || '미제공';
const ruleLabel = (item) => ({total_half:'총점 기준 미만/초과',handicap_half:'홈팀 핸디캡 적용 승패',handicap_3way:'홈팀 핸디캡 적용 승무패'})[item.settlement_rule] || '상품별 공식 결과';
export const validationLabel = (item) => item.validation_status === "validated_profitable"
  ? "수익성 검증됨" : "자동 추천 · 수익성 미검증";

export function MarketRecommendationPerformance({ data }) {
  if (!data || !data.by_market) return null;
  const outcome = (row) => row?.bets > 0
    ? `정산 ${row.bets}건 · 적중 ${row.hits}/${row.decided} · 환불 ${row.refunds}건 · ROI ${(100 * row.roi).toFixed(2)}%`
    : `기록 ${row?.captured || 0}건 · 정산 대기 · ROI 미산출`;
  return <section className="my-3 text-[12px] leading-6" aria-label="시장별 사전 추천 성적">
    <b>언더오버 · 핸디캡 추천 성적</b>
    {Object.entries(data.by_market).map(([market,row]) => <p className="my-1" key={market}>{market}: {outcome(row)}</p>)}
    {Object.keys(data.by_market).length === 0 && <p className="my-1">새 정책의 사전 기록 수집 중 · 정산 성적 없음</p>}
    {data.matched_m0_comparison?.m0_actual?.captured > 0 && <details><summary>같은 상품·가격의 M0 비교</summary>
      <p>M0: {outcome(data.matched_m0_comparison.m0_actual)}</p>
      <p>시장: {outcome(data.matched_m0_comparison.market)}</p>
      <p>실제 추천: {outcome(data.matched_m0_comparison.operating)}</p>
    </details>}
    <p className="my-1 text-ink3">상품별 가상 1단위 성적 · 승패 적중률 및 모의 지갑과 별도 · 수익성 미검증</p>
  </section>;
}

export default function MarketRecommendations({ recommendations, onSelect, selected = () => false }) {
  if (!recommendations.length) return null;
  return <section className="my-3 rounded border border-rule2 p-3" aria-label="시장별 자동 추천">
    <h3 className="m-0 text-[13px] font-semibold">핸디캡 · 언더오버 자동 추천</h3>
    {recommendations.map(({item, option, result, saved, capturedAt, generatedAt, comparisonVersion}) => <article className="mt-2 rounded border border-rule2 p-2 text-[12px] leading-6" key={`${item.selection_id}|${item.offer_id}`}>
      <b>{item.market} {item.label} · {item.selection} · 배당 {Number(item.odds).toFixed(2)}배</b>
      <p className="my-1">{saved ? `보존된 경기 전 추천 · 기록 ${capturedAt} · ${resultLabel(result)} · ` : ""}{validationLabel(item)} · 기준점 {item.line} · 정산 {ruleLabel(item)}</p>
      <p className="my-1 text-ink3">선정 기준: {modelLabel(item.model)} · 출처 {Array.isArray(item.source_ids) && item.source_ids.length ? item.source_ids.map((source) => source === "proto_official_current_market" ? "현재 프로토 배당" : source).join(", ") : "미제공"}
        {generatedAt ? ` · 생성 ${generatedAt}` : ""}</p>
      <p className="my-1 text-ink3">{probabilityLabel(item) && fraction(item.probability) ? `예상 적중확률 ${percent(item.probability)} (${probabilityLabel(item)})` : "예상 적중확률 미제공"}
        {fraction(item.p_push) ? ` · 무효 확률 ${percent(item.p_push)}` : " · 무효 확률 미제공"}
        {probabilityLabel(item) && typeof item.estimated_roi === "number" && Number.isFinite(item.estimated_roi) ? ` · 예상 수익률 ${(item.estimated_roi * 100).toFixed(1)}% (베팅액 대비)` : " · 예상 수익률 미제공"}</p>
      {comparisonVersion !== "exact-market-frontier-v1" && item.comparisons?.m0_actual?.selection && <p className="my-1 text-ink3">M0 비교 계산 · 미채택: {item.comparisons.m0_actual.selection}
        {fraction(item.comparisons.m0_actual.probability) ? ` · 무효 제외 조건부 ${percent(item.comparisons.m0_actual.probability)}` : ""}</p>}
      {saved && <p className="my-1 text-ink3">경기 시작 30분 전부터 선택 목록에 담을 수 없습니다.</p>}
      {onSelect && !saved && <button type="button" className="bet-record-button" disabled={selected(option)} onClick={() => onSelect(option)}>{selected(option) ? "✓ 선택 목록에 담김" : "이 추천을 선택 목록에 담기"}</button>}
    </article>)}
  </section>;
}
